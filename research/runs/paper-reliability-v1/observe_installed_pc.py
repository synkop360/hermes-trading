"""Read only ordinary scheduled outcomes and actual installed launch bindings."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import json,sqlite3,sys
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text())
hermes_dir=Path('C:/Users/thoma/AppData/Local/hermes');jobid='2f6c5088c6ff'
sys.path.insert(0,c['pc_project'])
from scripts.hermes_watch import _supervisor_release
from hermes_trading.supervisor_launch import validate_launch
from hermes_trading.reflection_supervision import _hash
release=_supervisor_release();assert release==json.loads((LIVE/'release-plan.json').read_text())['new']
checked=validate_launch(Path(c['actual_launch']),Path(c['pc_project']),release);state=checked['local_state_dir']
ready=json.loads((LIVE/'release_successor_readiness.native-result.json').read_text())['result']['ready_at']
utc=lambda value:datetime.fromisoformat(value).astimezone(timezone.utc)
with sqlite3.connect((hermes_dir/'cron/executions.db').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
    db.execute('PRAGMA query_only=ON')
    rows=[dict(zip(('status','started_at','finished_at'),r)) for r in db.execute('SELECT status,started_at,finished_at FROM executions WHERE job_id=? ORDER BY rowid DESC LIMIT 5000',(jobid,))]
rows=sorted((r for r in rows if r['started_at'] and utc(r['started_at'])>=utc(ready)),key=lambda r:utc(r['started_at']))
jobs=json.loads((hermes_dir/'cron/jobs.json').read_text(encoding='utf-8'))['jobs'];job=next(j for j in jobs if j['id']==jobid)
original=json.loads((LIVE/'schedule-originals.json').read_text())[jobid]
keys=('script','no_agent','schedule','workdir','model','provider','skills','enabled_toolsets')
assert all(job.get(k)==original.get(k) for k in keys)
assert not next(j for j in jobs if j['id']=='a316e6ed4190')['enabled']
now=datetime.now(timezone.utc)
result=dict(observed_at=now.isoformat(),release_hash=_hash(release),native_launch_valid=True,
    schedule_definition_unchanged=True,legacy_remains_paused=True,job={k:job.get(k) for k in ('id','enabled','state','last_run_at','last_status','next_run_at')},
    post_readiness_counts=dict(Counter(r['status'] for r in rows)),latest_runs=rows[-10:],
    status=json.loads((state/'supervisor_status.json').read_text()),lifecycle=json.loads((state/'supervisor_lifecycle.json').read_text()))
with (LIVE/(now.strftime('%Y%m%dT%H%M%SZ')+'-pc-installed.json')).open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
