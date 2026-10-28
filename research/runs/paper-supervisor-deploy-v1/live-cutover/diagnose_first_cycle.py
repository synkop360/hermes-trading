"""Read durable first-cycle phase receipts, with no exporter/importer calls."""
import sys;sys.dont_write_bytecode=True
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import json,sqlite3,time
root=Path('/app/state/adaptive_200');start='2026-10-28T07:31:19+00:00';end='2026-10-28T07:31:36+00:00'
result={}
for file,table in [('model_access_refresh.sqlite','renewals'),('supervisor_usage_feed.sqlite','snapshots')]:
    try:
        with closing(sqlite3.connect((root/file).as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
            db.execute('PRAGMA query_only=ON');deadline=time.monotonic()+.5
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            rows=db.execute('SELECT at,receipt FROM '+table+' WHERE at>=? AND at<=? ORDER BY at LIMIT 12',(start,end)).fetchall()
        allowed=('status','verified_at','valid_until','imported_at','covered_through')
        result[table]=[dict(at=at,receipt={k:v for k,v in json.loads(raw).items() if k in allowed}) for at,raw in rows]
    except sqlite3.Error as error:result[table]=dict(status='unavailable',kind=type(error).__name__)
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),durable_first_cycle_receipts=result,no_model_or_exporter_invoked=True)))
