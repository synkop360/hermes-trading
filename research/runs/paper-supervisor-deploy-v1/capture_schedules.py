"""Read original schedule and native PC launch before any interruption."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,sys
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text())
sys.path.insert(0,c['pc_project'])
from scripts.hermes_watch import _supervisor_release
from hermes_trading.supervisor_launch import validate_launch
release=_supervisor_release();plan=json.loads((LIVE/'release-plan.json').read_text());assert release==plan['old']
checked=validate_launch(Path(c['actual_launch']),Path(c['pc_project']),release)
state=checked['local_state_dir'];lifecycle=json.loads((state/'supervisor_lifecycle.json').read_text())
assert lifecycle['model_attempts_reserved']==0 and not (state/'paper_supervisor_health_pending.json').exists()
jobs=json.loads(Path('C:/Users/thoma/AppData/Local/hermes/cron/jobs.json').read_text(encoding='utf-8'))['jobs']
selected={j['id']:j for j in jobs if j['id'] in ('2f6c5088c6ff','a316e6ed4190')}
assert len(selected)==2 and selected['2f6c5088c6ff']['enabled'] and not selected['a316e6ed4190']['enabled']
assert selected['2f6c5088c6ff']['no_agent'] and selected['2f6c5088c6ff']['schedule']=={'kind':'interval','minutes':1,'display':'every 1m'}
with (LIVE/'schedule-originals.json').open('x',encoding='utf-8') as f:json.dump(selected,f,indent=2)
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='original_pc_launch_and_schedules_validated',model_attempts_reserved=0,
           actual_state=str(state),cycles_started=lifecycle['cycles_started'],deadline=lifecycle['deadline'],native_runtime_files=len(release['runtime']),
           native_launch_sha256=hashlib.sha256(Path(c['actual_launch']).read_bytes()).hexdigest())
with (LIVE/'pc-preflight.json').open('x') as f:json.dump(value,f,indent=2)
print(json.dumps(value))
