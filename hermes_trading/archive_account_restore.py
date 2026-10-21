"""Disabled fresh-target account restore; never replaces an active database.

The newest pinned source supplies every nonfinancial table and database schema.
The verified original financial union supplies financial rows. Role ownership,
authenticated manifests and coherent cohort/control/nonce recovery remain the
caller's responsibility; one restored account cannot authorize activation.
"""
from contextlib import closing
import hashlib,math,os,sqlite3,tempfile,time

from hermes_trading.archive_account_history import account_history,TABLES,_q,_hash_file
from hermes_trading.archive_store import _root,sync_file,_sync_directory


def _schema(db):
    rows=[tuple(row) for row in db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
    if any(kind not in ('table','index') or 'VIRTUAL TABLE' in sql.upper() or 'WITHOUT ROWID' in sql.upper()
           for kind,name,sql in rows):
        raise ValueError('unsupported original account schema')
    return rows


def _fingerprint(db,tables,check):
    digest=hashlib.sha256()
    for name in sorted(tables):
        check();digest.update(name.encode()+b'\0');count=0
        for row in db.execute('SELECT rowid,* FROM '+_q(name)+' ORDER BY rowid'):
            check();count+=1
            for value in row:
                kind=type(value).__name__.encode()
                raw=value if isinstance(value,bytes) else repr(value).encode('utf-8')
                digest.update(len(kind).to_bytes(2,'big')+kind+len(raw).to_bytes(8,'big')+raw)
        digest.update(count.to_bytes(8,'big'))
    return digest.hexdigest()


def restore_account(sources,target,*,enabled=False,cache_root,max_bytes,timeout_seconds,
                    clock=time.monotonic,fault=None):
    """Verify and atomically publish a new full account SQLite database.

    Inputs and financial cache/output are separately bounded, not an aggregate
    peak-space guarantee. Deadlines are cooperative between SQL/hash/backup
    chunks. Hard-link no-replace publication requires filesystem support.
    Failed publication keeps every original source. A lost post-publication
    reply may leave a valid target; it is never silently overwritten on retry.
    """
    if enabled is not True:raise ValueError('account database restore is disabled')
    if (type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or
            not 0<timeout_seconds<=120):raise ValueError('account restore deadline differs')
    if type(max_bytes) is not int or not 4096<=max_bytes<=4*1024**3:
        raise ValueError('account restore byte bound differs')
    started=clock();deadline=started+timeout_seconds
    def check():
        current=clock()
        if not math.isfinite(current) or current<started:raise ValueError('account restore clock differs')
        if current>=deadline:raise TimeoutError('account restore deadline exceeded')
    def hit(point,path):
        if fault is not None:fault(point,path)
        check()
    check();target=_root(target);cache_root=_root(cache_root)
    if target.exists() or not target.parent.is_dir():raise ValueError('account restore requires a fresh target')
    # The materializer validates ordered pins, consistent sources and their
    # financial transitions before any complete-account target is prepared.
    with account_history(sources,enabled=True,cache_root=cache_root,max_bytes=max_bytes,
                         timeout_seconds=deadline-clock(),clock=clock) as financial:
        active=_root(sources[-1]['path']);expected=sources[-1]['sha256']
        if _hash_file(active,check,max_bytes)!=expected:raise ValueError('original active account digest differs')
        with tempfile.TemporaryDirectory(prefix='.account-restore-',dir=target.parent) as temporary:
            stage=_root(temporary)/'account.sqlite'
            with closing(sqlite3.connect(active.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as original:
                original.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,1024*1024))
                original.set_progress_handler(lambda:int(clock()>=deadline),1000)
                schema=_schema(original);tables=[name for kind,name,sql in schema if kind=='table']
                control=[name for name in tables if name not in TABLES]
                control_hash=_fingerprint(original,control,check)
                financial_hash=_fingerprint(financial,TABLES,check)
                pragmas={key:original.execute('PRAGMA '+key).fetchone()[0] for key in ('user_version','application_id')}
                with closing(sqlite3.connect(stage)) as restored:
                    restored.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,1024*1024))
                    restored.set_progress_handler(lambda:int(clock()>=deadline),1000)
                    original.backup(restored,pages=256,progress=lambda *args:check(),sleep=0)
                    hit('after_backup',stage)
                    restored.execute('PRAGMA journal_mode=DELETE');restored.execute('PRAGMA synchronous=FULL')
                    restored.execute('PRAGMA max_page_count='+str(max_bytes//restored.execute('PRAGMA page_size').fetchone()[0]))
                    restored.execute('BEGIN IMMEDIATE')
                    for table in TABLES:restored.execute('DELETE FROM '+_q(table))
                    for table in TABLES:
                        columns=[row[1] for row in financial.execute('PRAGMA table_info('+_q(table)+')')]
                        statement='INSERT INTO '+_q(table)+'(rowid,'+','.join(_q(col) for col in columns)+') VALUES('+','.join('?' for _ in range(len(columns)+1))+')'
                        for row in financial.execute('SELECT rowid,* FROM '+_q(table)+' ORDER BY rowid'):
                            check();restored.execute(statement,tuple(row))
                    if restored.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('restored original account linkage differs')
                    restored.commit()
                hit('before_publish',stage)
                # Verify again after staging faults, using a new read handle.
                with closing(sqlite3.connect(stage.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as verified:
                    verified.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,min(max_bytes,1024*1024))
                    verified.set_progress_handler(lambda:int(clock()>=deadline),1000)
                    if (_schema(verified)!=schema or _fingerprint(verified,control,check)!=control_hash or
                            _fingerprint(verified,TABLES,check)!=financial_hash):
                        raise ValueError('restored original financial/control evidence differs')
                    if any(verified.execute('PRAGMA '+key).fetchone()[0]!=value for key,value in pragmas.items()):
                        raise ValueError('original account database version differs')
                    if verified.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or verified.execute('PRAGMA foreign_key_check').fetchone():
                        raise ValueError('restored original account integrity differs')
                if _hash_file(active,check,max_bytes)!=expected:raise ValueError('original active account changed during restore')
                size=stage.stat().st_size
                if size>max_bytes:raise ValueError('account restore output byte bound exceeded')
                digest=_hash_file(stage,check,max_bytes);sync_file(stage);check()
            os.link(stage,target);durable=_sync_directory(target.parent)
            hit('after_publish',target)
            return {'schema_version':1,'purpose':'paired_account_restore_v1','database_sha256':digest,
                    'database_bytes':size,'active_source_sha256':expected,'control_rows_sha256':control_hash,
                    'financial_rows_sha256':financial_hash,'directory_fsync_verified':durable,
                    'activated':False,'retirement_authorized':False,'cohort_recovery_verified':False}
