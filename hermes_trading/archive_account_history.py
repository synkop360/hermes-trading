"""Disabled, disposable financial-history reader over pinned original SQLite.

Callers must authenticate ordered snapshot manifests and own their immutable
files. This is a read adapter, not a source compactor, complete runtime restore,
history-availability gate, independent-backup certificate or aggregate quota.
Only the original financial tables are materialized; runtime/control state is
deliberately outside this reader's scope.
"""
from contextlib import closing, contextmanager
from decimal import Decimal
import hashlib
import math
import sqlite3
import tempfile
import time

from hermes_trading.archive_store import _root, _digest

TABLES=('decisions','order_intents','orders','order_fills','positions','projection_payloads')
ENTRY_FIELDS=('position_id','asset','entry_price','strategy_version','opened_at')
MAX_SOURCES=1024


def _q(value):return '"'+value.replace('"','""')+'"'


def _hash_file(path,check,limit):
    digest=hashlib.sha256();total=0
    with path.open('rb') as stream:
        while True:
            check();raw=stream.read(1024*1024)
            if not raw:break
            total+=len(raw)
            if total>limit:raise ValueError('financial history input byte bound exceeded')
            digest.update(raw)
    return digest.hexdigest()


def _schema(db):
    result={}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone():
        raise ValueError('financial history triggers are unsupported')
    for name in TABLES:
        row=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
        if not row or 'VIRTUAL TABLE' in row[0].upper() or 'WITHOUT ROWID' in row[0].upper():
            raise ValueError('financial history schema differs')
        info=db.execute('PRAGMA table_info('+_q(name)+')').fetchall()
        columns=[item[1] for item in info];keys=[item[1] for item in sorted(info,key=lambda item:item[5]) if item[5]]
        if not keys or any(col.lower() in ('rowid','oid','_rowid_') for col in columns):
            raise ValueError('financial history requires original rowid and primary identity')
        if name=='positions' and not set(ENTRY_FIELDS+('status','quantity','partial_exit_count')).issubset(columns):
            raise ValueError('financial position schema differs')
        result[name]=(row[0],columns,keys)
    return result


def _position(previous,current):
    if any(previous[key]!=current[key] for key in ENTRY_FIELDS):
        raise ValueError('original entry identity changed')
    if previous['status']=='closed':
        raise ValueError('closed financial evidence is immutable')
    if previous['status']!='open' or current['status'] not in ('open','closed','closing','error'):
        raise ValueError('unsupported original position continuation')
    old,new=Decimal(previous['quantity']),Decimal(current['quantity'])
    if not old.is_finite() or not new.is_finite() or not 0<new<=old:
        raise ValueError('position inventory increased across history')
    if current['partial_exit_count']<previous['partial_exit_count']:
        raise ValueError('position partial-exit history regressed')


@contextmanager
def account_history(sources,*,enabled=False,cache_root,max_bytes,timeout_seconds,clock=time.monotonic):
    """Yield a query-only connection, retaining exact original PKs and rowids.

    Sources are oldest first, with the complete current inventory last. Each
    item contains only path and a digest pinned by the caller's verified
    manifest. Missing closed rows may live in earlier sources; missing open
    inventory is an error. Conflicts fail instead of choosing a financial row.
    Input bytes and the disposable database each have the supplied ceiling;
    this is not a bound on their sum, unrelated state, or OS-blocked I/O.
    """
    if enabled is not True:raise ValueError('financial account history is disabled')
    if type(max_bytes) is not int or not 4096<=max_bytes<=4*1024**3:
        raise ValueError('financial history byte bound differs')
    if (type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or
            not 0<timeout_seconds<=120):raise ValueError('financial history deadline differs')
    if not isinstance(sources,(list,tuple)) or not 1<=len(sources)<=MAX_SOURCES:
        raise ValueError('financial history source bound differs')
    cache_root=_root(cache_root)
    if not cache_root.is_dir():raise ValueError('financial history cache root must exist')
    deadline=clock()+timeout_seconds
    def check():
        if clock()>=deadline:raise TimeoutError('financial history deadline exceeded')
    check();references=[];total=0
    for item in sources:
        if not isinstance(item,dict) or set(item)!={'path','sha256'}:
            raise ValueError('financial history source binding differs')
        path=_root(item['path']);digest=_digest(item['sha256'])
        if not path.is_file():raise ValueError('original financial history is unavailable')
        total+=path.stat().st_size
        if total>max_bytes:raise ValueError('financial history input byte bound exceeded')
        references.append((path,digest))
    with tempfile.TemporaryDirectory(prefix='.account-history-',dir=cache_root) as temporary:
        path=_root(temporary)/'history.sqlite'
        with closing(sqlite3.connect(path)) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA temp_store=MEMORY')
            db.execute('PRAGMA cache_size=-1024')
            db.execute('PRAGMA max_page_count='+str(max_bytes//db.execute('PRAGMA page_size').fetchone()[0]))
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,1024*1024))
            db.set_progress_handler(lambda:int(clock()>=deadline),1000)
            schema=None;latest_positions=set()
            for source,digest in references:
                check()
                wal=source.with_name(source.name+'-wal')
                if wal.exists() and wal.stat().st_size:raise ValueError('original history requires a consistent snapshot')
                if _hash_file(source,check,max_bytes)!=digest:raise ValueError('original financial history digest differs')
                with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as original:
                    original.row_factory=sqlite3.Row
                    original.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,1024*1024))
                    original.set_progress_handler(lambda:int(clock()>=deadline),1000)
                    observed=_schema(original)
                    if schema is None:
                        schema=observed
                        for name in TABLES:db.execute(schema[name][0])
                    elif observed!=schema:raise ValueError('original financial history schema changed')
                    latest_positions=set()
                    for name in TABLES:
                        _,columns,keys=schema[name]
                        projection='rowid AS original_rowid,'+','.join(_q(col) for col in columns)
                        where=' AND '.join(_q(key)+' IS ?' for key in keys)
                        for row in original.execute('SELECT '+projection+' FROM '+_q(name)+' ORDER BY rowid'):
                            check();identity=tuple(row[key] for key in keys)
                            if name=='positions':latest_positions.add(identity)
                            prior=db.execute('SELECT '+projection+' FROM '+_q(name)+' WHERE '+where,identity).fetchone()
                            collision=db.execute('SELECT '+projection+' FROM '+_q(name)+' WHERE rowid=?',(row['original_rowid'],)).fetchone()
                            if collision is not None and tuple(collision[key] for key in keys)!=identity:
                                raise ValueError('original rowid reused for another identity')
                            if prior is not None:
                                if prior['original_rowid']!=row['original_rowid']:
                                    raise ValueError('original primary identity changed rowid')
                                if tuple(prior)==tuple(row):continue
                                if name!='positions':raise ValueError('immutable financial history changed')
                                _position(prior,row)
                                db.execute('DELETE FROM positions WHERE '+where,identity)
                            values=tuple(row)
                            db.execute('INSERT INTO '+_q(name)+'(rowid,'+','.join(_q(col) for col in columns)+') VALUES('+','.join('?' for _ in values)+')',values)
                    if _hash_file(source,check,max_bytes)!=digest:raise ValueError('original financial history changed during read')
            for row in db.execute("SELECT * FROM positions WHERE status!='closed'"):
                check()
                if tuple(row[key] for key in schema['positions'][2]) not in latest_positions:
                    raise ValueError('current open inventory is missing from latest history')
            if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('original financial history linkage is missing')
            if db.execute("SELECT asset FROM positions WHERE status IN ('opening','open','closing') GROUP BY asset HAVING COUNT(*)>1 LIMIT 1").fetchone():
                raise ValueError('conflicting active inventory in original history')
            db.commit();check()
            if path.stat().st_size>max_bytes:raise ValueError('financial history cache byte bound exceeded')
            db.execute('PRAGMA query_only=ON')
            yield db
