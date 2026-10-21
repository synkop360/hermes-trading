"""Disabled, report-only union of caller-pinned original shared snapshots.

Pins must come from the caller's verified exports. This module does not resolve
or authenticate archive manifests, attest independent copies, compact sources,
or authorize worker recovery/retirement. A full report still needs intact
account financial evidence and a consistent cohort. Never pass a live WAL main
file as a sealed snapshot. Input and disposable-cache ceilings are separate;
they do not bound total runtime storage, memory or OS-blocked file operations.
"""
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
import hashlib
import math
import sqlite3
import tempfile
import time

from hermes_trading import archive_journal as journal
from hermes_trading.archive_store import _root, _digest
from hermes_trading.paired_packet_storage import MAX_PACKET_BYTES
from hermes_trading.reflection_supervision import _hash

MAX_SOURCES=1024
TABLES={
    'paired_batches':('batch_id','status','payload','input_hash','completed'),
    'paired_settlements':('batch_id','payload'),
    'paired_packet_objects':('sha256','data','raw_bytes'),
}


@dataclass(frozen=True)
class SharedHistoryView:
    history: sqlite3.Connection
    current: sqlite3.Connection
    complete: int
    retired_complete: int


def _pin(path, digest, check, limit):
    check()
    for suffix in ('-wal','-journal'):
        sidecar=path.with_name(path.name+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('shared history requires a consistent snapshot without nonempty sidecars')
    result=hashlib.sha256();total=0
    with path.open('rb') as stream:
        while raw:=stream.read(1024*1024):
            check();total+=len(raw)
            if total>limit:raise ValueError('shared history input byte bound exceeded')
            result.update(raw)
    if result.hexdigest()!=digest:raise ValueError('original shared history digest changed or differs')


def _columns(db, table):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchone():
        raise ValueError('shared history triggers are unsupported')
    columns=db.execute('PRAGMA table_xinfo('+table+')').fetchall()
    if (tuple(row[1] for row in columns)!=TABLES[table] or any(row[6] for row in columns)):
        raise ValueError('shared history schema differs')


def _merge(source, target, table, check):
    exists=source.execute("SELECT type FROM sqlite_master WHERE name=?",(table,)).fetchone()
    if exists is None and table=='paired_packet_objects':return
    if exists is None or exists[0]!='table':raise ValueError('shared history table is missing')
    _columns(source,table)
    columns=','.join(TABLES[table]);key=TABLES[table][0]
    for row in source.execute('SELECT rowid,'+columns+' FROM '+table+' ORDER BY rowid'):
        check();values=tuple(row)
        if type(values[0]) is not int or values[0]<1:raise ValueError('original shared rowid differs')
        prior=target.execute('SELECT rowid,'+columns+' FROM '+table+' WHERE '+key+'=?',(values[1],)).fetchone()
        collision=target.execute('SELECT rowid,'+columns+' FROM '+table+' WHERE rowid=?',(values[0],)).fetchone()
        if prior is not None or collision is not None:
            if prior is None or collision is None or tuple(prior)!=values or tuple(collision)!=values:
                raise ValueError('conflicting original shared evidence or reused rowid')
            continue
        target.execute('INSERT INTO '+table+'(rowid,'+columns+') VALUES('+','.join('?' for _ in values)+')',values)


def _requests(db, checkpoint, expected_experiment, check):
    """The caller already checked full checkpoint ancestry with _checkpoint."""
    result=[]
    while checkpoint is not None:
        check()
        operation=journal._operation(db,checkpoint['operation_id']);request=operation['request']
        if request['experiment_id']!=expected_experiment:
            raise ValueError('shared history experiment differs')
        result.append(request)
        if request['revision']==1:break
        row=db.execute("SELECT operation_id FROM paired_archive_operations WHERE result_sha256=? AND status='retired'",
                       (request['previous_checkpoint_sha256'],)).fetchone()
        checkpoint=journal._operation(db,row[0])['result']
    return result


@contextmanager
def shared_history(sources, *, enabled=False, expected_cohort_sha256,
                   expected_experiment_id, cache_root, max_bytes, timeout_seconds,
                   clock=time.monotonic):
    """Yield separate query-only history/current connections; latest source last.

    Sources contain exactly path/sha256, pinned by the caller's verified
    manifest. Raw original bytes and rowids must agree across overlaps. Every
    source is itself a complete consistent journal; the full union must cover
    sequence1 through the current head and match all checkpoint prefix/next
    hashes. History contains no runnable lifecycle/checkpoint/control state.
    The deadline remains active while the consumer reads the returned view.
    """
    if enabled is not True:raise ValueError('shared report history is disabled')
    _digest(expected_cohort_sha256)
    if not isinstance(expected_experiment_id,str) or not 1<=len(expected_experiment_id)<=128:
        raise ValueError('shared history experiment identity differs')
    if type(max_bytes) is not int or not 4096<=max_bytes<=4*1024**3:
        raise ValueError('shared history byte bound differs')
    if (type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=120 or
            not math.isfinite(timeout_seconds)):raise ValueError('shared history deadline differs')
    if not isinstance(sources,(list,tuple)) or not 1<=len(sources)<=MAX_SOURCES:
        raise ValueError('shared history source bound differs')
    cache_root=_root(cache_root)
    if not cache_root.is_dir():raise ValueError('shared history cache root must exist')
    deadline=clock()+timeout_seconds
    def check():
        if clock()>=deadline:raise TimeoutError('shared history deadline exceeded')
    check();references=[];total=0
    for item in sources:
        if not isinstance(item,dict) or set(item)!={'path','sha256'}:
            raise ValueError('shared history source binding differs')
        path=_root(item['path']);digest=_digest(item['sha256'])
        if not path.is_file():raise ValueError('original shared history is unavailable')
        total+=path.stat().st_size
        if total>max_bytes:raise ValueError('shared history input byte bound exceeded')
        references.append((path,digest))
    with tempfile.TemporaryDirectory(prefix='.shared-history-',dir=cache_root) as temporary, ExitStack() as stack:
        path=_root(temporary)/'history.sqlite'
        db=stack.enter_context(closing(sqlite3.connect(path,timeout=0)))
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA cache_size=-1024')
        db.execute('PRAGMA max_page_count='+str(max_bytes//db.execute('PRAGMA page_size').fetchone()[0]))
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,MAX_PACKET_BYTES+131072))
        db.set_progress_handler(lambda:int(clock()>=deadline),1000)
        db.executescript('''CREATE TABLE paired_identity(id INTEGER PRIMARY KEY,manifest_hash TEXT);
            CREATE TABLE paired_batches(batch_id TEXT PRIMARY KEY,status TEXT,payload BLOB,input_hash TEXT,completed BLOB);
            CREATE TABLE paired_settlements(batch_id TEXT PRIMARY KEY,payload BLOB);
            CREATE TABLE paired_packet_objects(sha256 TEXT PRIMARY KEY,data BLOB,raw_bytes INTEGER);''')
        db.execute('INSERT INTO paired_identity VALUES(1,?)',(expected_cohort_sha256,))
        requests={};prior_complete=0;prior_retired=0;prior_revision=0
        for source,digest in references:
            _pin(source,digest,check,max_bytes)
            current=stack.enter_context(closing(sqlite3.connect(source.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)))
            current.row_factory=sqlite3.Row;current.execute('PRAGMA query_only=ON')
            current.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,MAX_PACKET_BYTES+131072))
            current.set_progress_handler(lambda:int(clock()>=deadline),1000)
            checkpoint=journal._checkpoint(current)
            observed=journal._read_journal(current,checkpoint,check)
            retired=0 if checkpoint is None else checkpoint['retired_complete']
            revision=0 if checkpoint is None else checkpoint['revision']
            if observed['cohort_sha256']!=expected_cohort_sha256:
                raise ValueError('shared history cohort differs')
            if observed['pending']:raise ValueError('pending shared history snapshot is incomplete')
            if observed['complete']<prior_complete or retired<prior_retired or revision<prior_revision:
                raise ValueError('shared history order or lifetime regressed')
            for request in _requests(current,checkpoint,expected_experiment_id,check):
                prior=requests.get(request['operation_id'])
                if prior is not None and prior!=request:raise ValueError('shared history checkpoint request changed')
                requests[request['operation_id']]=request
            for table in ('paired_packet_objects','paired_batches','paired_settlements'):
                _merge(current,db,table,check)
            _pin(source,digest,check,max_bytes)
            prior_complete=observed['complete'];prior_retired=retired;prior_revision=revision
        full=journal._read_journal(db,None,check)
        if full['pending'] or full['complete']!=prior_complete:
            raise ValueError('original shared history does not match current complete head')
        # Stream the chain once, rather than rehashing the full prefix per cut.
        by_cut={}
        for request in requests.values():by_cut.setdefault(request['through_sequence'],[]).append(request)
        prefix=_hash([]);rows=full['rows']
        for index,row in enumerate(rows):
            check();prefix=_hash({'previous':prefix,'record':row})
            for request in by_cut.get(row['sequence'],[]):
                if prefix!=request['prefix_sha256']:raise ValueError('original shared history prefix differs')
                if index+1>=len(rows) or rows[index+1]['input_hash']!=request['next_input_hash']:
                    raise ValueError('original shared history next input differs')
                if request['observed_complete']>full['complete']:
                    raise ValueError('original shared history observed head differs')
        if any(cut>full['complete'] for cut in by_cut):raise ValueError('shared history retired prefix is missing')
        db.commit();check()
        if path.stat().st_size>max_bytes:raise ValueError('shared history cache byte bound exceeded')
        db.execute('PRAGMA query_only=ON')
        yield SharedHistoryView(db,current,full['complete'],prior_retired)
        for source,digest in references:_pin(source,digest,check,max_bytes)
