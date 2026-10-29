"""Bounded read-only snapshot sampling alongside ordinary scheduled supervision."""
import sys;sys.dont_write_bytecode=True
from pathlib import Path
from datetime import datetime,timezone
import json,sqlite3,time
from hermes_trading.supervisor_sync import make_snapshot_with_retry
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash
runtime=_release_hashes()
assert _hash(runtime)=='22799e23c53716cae16ca6a40d5eed820d9520881ccbf6b19e966577e3cd0a4d'
root=Path('/app/state/adaptive_200')
known={'worker is not healthy','paired worker supervision is not ready','wallet report does not match the worker heartbeat',
       'worker has not observed the current strategy version','worker configuration changed during snapshot',
       'paired market quote is invalid or stale','paired market candle is invalid or stale',
       'worker heartbeat is stale or future-dated','wallet report is stale or future-dated',
       'paired batch/publication lease is busy','paired batch requires completion before publication'}
def classify(error):
    rows=[];seen=set()
    for _ in range(8):
        if error is None or id(error) in seen:break
        seen.add(id(error));row=dict(type=type(error).__name__,frames=[])
        if isinstance(error,sqlite3.Error):row['sqlite_code']=getattr(error,'sqlite_errorcode',None)
        if type(error) is ValueError and str(error) in known:row['reason']=str(error)
        tb=error.__traceback__
        while tb:
            file=tb.tb_frame.f_code.co_filename
            if file.startswith('/app/hermes_trading/'):
                row['frames'].append(dict(source=file[len('/app/'):],line=tb.tb_lineno,function=tb.tb_frame.f_code.co_name))
            tb=tb.tb_next
        row['frames']=row['frames'][-8:];rows.append(row);error=error.__cause__ or error.__context__
    return rows
def tiny():
    value={}
    for name,fields in [('heartbeat.json',('timestamp','observed_at','status','model_ready','error','wallet_report_hash')),
                        ('model_access.json',('verified_at','valid_until','payment','paid_calls_authorized'))]:
        try:
            path=root/name;assert path.stat().st_size<1024*1024
            body=json.loads(path.read_bytes());value[name]={k:body.get(k) for k in fields}
        except Exception as error:value[name]=dict(unavailable=type(error).__name__)
    return value
from contextlib import closing
import signal
def sample_deadline(signum,frame):raise TimeoutError('bounded diagnostic sample')
signal.signal(signal.SIGALRM,sample_deadline)
started=time.monotonic();rows=[];was_busy=False;polls=0
while time.monotonic()-started<180 and len(rows)<3:
    busy=False;polls+=1
    try:
        with closing(sqlite3.connect((root/'paper_publications.sqlite').as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('SELECT 1 FROM paired_batches LIMIT 1').fetchone()
    except sqlite3.OperationalError as error:
        if getattr(error,'sqlite_errorcode',-1)&255!=sqlite3.SQLITE_BUSY:raise
        busy=True
    if busy and not was_busy:
        tick=time.monotonic();row=dict(at=datetime.now(timezone.utc).isoformat(),before=tiny(),trigger='observed_numeric_sqlite_busy')
        signal.alarm(30)
        try:
            snapshot=make_snapshot_with_retry(root);row.update(status='validated',schema=snapshot['schema_version'])
        except Exception as error:row.update(status='rejected',chain=classify(error))
        finally:signal.alarm(0)
        row.update(elapsed_seconds=time.monotonic()-tick,after=tiny());rows.append(row)
    was_busy=busy;time.sleep(.1)
print(json.dumps(dict(kind='read_only_snapshot_at_busy_onset',runtime_hash=_hash(runtime),observations=rows,
    elapsed_seconds=time.monotonic()-started,polls=polls,models_invoked=False,health_receipts_published=False)))
