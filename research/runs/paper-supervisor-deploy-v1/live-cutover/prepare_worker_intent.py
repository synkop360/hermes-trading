import sys;sys.dont_write_bytecode=True
from pathlib import Path
from datetime import datetime,timezone
import json
from scripts.paper_release_transition import apply_transition,inspect_transition
base=Path('/tmp/hermes-recovery-ea752c5');peers=base/'peer-originals/pc-reference-bundle'
plan=json.loads((base/'frozen-live-transition-plan.json').read_text())
options=dict(worker_root='/app/state',supervisor_root=peers/'pc-original',launch_file=peers/'launch-original.json',audit_dir=base/'worker-audit')
assert not Path('/app/state/.paper-ready-recovery-ea752c5').exists()
def stop(point,component):
 if point=='intent':raise RuntimeError('intent_prepared_before_any_binding_write')
try:apply_transition(plan,**options,apply=True,approval_ref='owner_20261027_always_deploy_paper_only',fault=stop)
except RuntimeError as error:assert str(error)=='intent_prepared_before_any_binding_write'
else:raise AssertionError('pre-component-write intent boundary missing')
inspection=inspect_transition(plan,**options);assert all(c['state']=='before' for c in inspection['components'])
intent=json.loads((base/'worker-audit/intent.json').read_text())
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),status='worker_intent_prepared_all_bindings_original',transition_id=plan['transition_id'],root_binding=intent['root_binding'],intent_hash=intent['intent_hash'],inspection=inspection)))
