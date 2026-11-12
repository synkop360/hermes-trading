from pathlib import Path
from datetime import datetime,timezone
import json,sys
base=Path(__file__).resolve().parents[4];sys.path.insert(0,str(base.parent/'hermes-trading'))
from scripts.paper_release_transition import apply_transition,inspect_transition
live=Path(__file__).resolve().parent;ref=base.parent/'r27-ea752c5'
plan=json.loads((live/'frozen-live-transition-plan.json').read_text())
actual=json.loads((live/'pc-original-reference.json').read_text())
prepared=json.loads((live/'pc-intent-prepared.json').read_text())
audit=Path(prepared['audit_dir'])
intent=json.loads((audit/'intent.json').read_text())
assert intent['intent_hash']==prepared['intent_hash'] and intent['transition_id']==plan['transition_id']
options=dict(worker_root=ref/'w',supervisor_root=actual['actual_supervisor'],launch_file=actual['actual_launch'],audit_dir=audit)
receipt=apply_transition(plan,**options,apply=True,reconcile=True,approval_ref='owner_20261027_always_deploy_paper_only')
inspection=inspect_transition(plan,**options)
assert receipt['status']=='completed' and all(c['state']=='after' for c in inspection['components'])
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),receipt=receipt,inspection=inspection)
(live/'pc-transition-completed.json').write_text(json.dumps(value,indent=2))
print(json.dumps(value))
