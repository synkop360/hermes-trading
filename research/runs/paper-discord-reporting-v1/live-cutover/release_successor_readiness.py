from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import json,os,sqlite3
from scripts.paper_release_transition import inspect_transition
from hermes_trading.paper_publication import _release_hashes,_sync_dir
from hermes_trading.paired_inception import read_paired_inception
from hermes_trading.reflection_supervision import _hash
base=Path('/tmp/hermes-discord-ed439e8');peers=base/'peer-originals/pc-reference-bundle';root=Path('/app/state')
plan=json.loads((base/'frozen-live-transition-plan.json').read_text())
inspection=inspect_transition(plan,worker_root=root,supervisor_root=peers/'pc-original',launch_file=peers/'launch-original.json',audit_dir=base/'worker-audit')
assert all(c['state']=='after' for c in inspection['components'])
assert _release_hashes()==plan['release_plan']['new']['runtime'] and os.environ.get('HERMES_TRADING_MODE')=='paper' and os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS')=='false'
receipt=json.loads((base/'worker-audit/receipt.json').read_text());assert receipt['status']=='completed' and receipt['transition_id']==plan['transition_id']
def immutable(path,*,check):
 check();return closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0))
assert _hash(read_paired_inception(root,database_factory=immutable))==plan['identity']['opening_hash']
assert os.environ.get('HERMES_NOTIFY_ENABLED','').strip().lower()=='true'
safety=json.loads((root/'paper_safety_state.json').read_text())
assert safety['supervisor']['entry_halt'] is True and safety['supervisor']['consecutive_errors']==3
marker=root/'.paper-ready-discord-ed439e8';assert not marker.exists()
value=dict(transition_id=plan['transition_id'],source_candidate=plan['release_plan']['new_commit'],pc_native_validation_sha256='045f965bcd98a84aefc932f88ee302dddfd196f64ed71cad261295668d003772',ready_at=datetime.now(timezone.utc).isoformat(),original_opening_hash=plan['identity']['opening_hash'])
with marker.open('xb') as stream:stream.write((json.dumps(value,sort_keys=True)+'\n').encode());stream.flush();os.fsync(stream.fileno())
_sync_dir(root)
print(json.dumps(dict(status='original_experiment_successor_readiness_released',**value)))
