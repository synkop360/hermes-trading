"""Capture drained original PC state; writes only new off-host audit copies."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,sys
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text());ref=Path(c['reference'])
assert not ref.exists();ref.mkdir()
sys.path.insert(0,c['package'])
from hermes_trading.archive_snapshot import snapshot
from hermes_trading.supervisor_launch import validate_launch
from scripts.paper_release_transition import _closed_inputs,_layout
plan=json.loads((LIVE/'release-plan.json').read_text());launch=Path(c['actual_launch'])
checked=validate_launch(launch,Path(c['pc_project']),plan['old']);state=checked['local_state_dir']
assert not (state/'paper_supervisor_health_pending.json').exists()
receipt=snapshot(state,ref/'pc-native-snapshot',enabled=True,role='supervisor',max_bytes=500_000_000,timeout_seconds=30)
assert not any(p.name.endswith(('-wal','-shm','-journal')) for p in state.rglob('*') if p.is_file())
native=ref/'pc-native-snapshot';inventory=json.loads((native/'snapshot_inventory.json').read_bytes());target=ref/'p';target.mkdir()
for row in inventory['files']:
    source=native/row['path'];assert source.stat().st_size==row['bytes'] and hashlib.sha256(source.read_bytes()).hexdigest()==row['sha256']
    output=target/row['path'];output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,output)
directories=[p.relative_to(state).as_posix() for p in state.rglob('*') if p.is_dir()]
for name in directories:(target/name).mkdir(parents=True,exist_ok=True)
shutil.copy2(launch,ref/'l.json')
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),actual_supervisor=str(state),actual_launch=str(launch),
    supervisor_reference=str(target),launch_reference=str(ref/'l.json'),native_snapshot=receipt,files=len(inventory['files']),
    inventory_sha256=hashlib.sha256((native/'snapshot_inventory.json').read_bytes()).hexdigest(),directories=directories,
    launch_sha256=hashlib.sha256(launch.read_bytes()).hexdigest(),source_sidecar_free=True)
(LIVE/'pc-original-reference.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
