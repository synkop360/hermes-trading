from pathlib import Path
from datetime import datetime,timezone
import json,sys,time
sys.dont_write_bytecode=True
base=Path(__file__).resolve().parents[4];sys.path.insert(0,str(base.parent/'hermes-snapshot-20eb6af'))
from scripts.paper_release_transition import apply_transition,inspect_transition
live=Path(__file__).resolve().parent;ref=base.parent/'s20'
plan=json.loads((live/'frozen-live-transition-plan.json').read_text())
actual=json.loads((live/'pc-original-reference.json').read_text())
audit=Path('C:/Users/thoma/AppData/Local/hermes/cutovers/paper-snapshot-20eb6af/pc-audit')
options=dict(worker_root=ref/'w',supervisor_root=actual['actual_supervisor'],launch_file=actual['actual_launch'],audit_dir=audit)
def stop(point,component):
 if point=='intent':raise RuntimeError('intent_prepared_before_any_binding_write')
start=time.monotonic()
try:apply_transition(plan,**options,apply=True,approval_ref='owner_20261026_snapshot_20eb6af_paper_cutover',fault=stop)
except RuntimeError as error:assert str(error)=='intent_prepared_before_any_binding_write'
else:raise AssertionError('pre-write intent stop missing')
inspection=inspect_transition(plan,**options)
assert all(c['state']=='before' for c in inspection['components'])
intent=json.loads((audit/'intent.json').read_text())
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='pc_intent_prepared_all_bindings_original',
 transition_id=plan['transition_id'],root_binding=intent['root_binding'],intent_hash=intent['intent_hash'],
 audit_dir=str(audit),inspection=inspection,elapsed_seconds=time.monotonic()-start)
(live/'pc-intent-prepared.json').write_text(json.dumps(value,indent=2))
print(json.dumps(value))
