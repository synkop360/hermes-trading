from pathlib import Path
import json
from scripts.paper_release_transition import apply_transition,inspect_transition
base=Path('/tmp/hermes-discord-ed439e8');peers=base/'peer-originals/pc-reference-bundle'
plan=json.loads((base/'frozen-live-transition-plan.json').read_text())
options=dict(worker_root='/app/state',supervisor_root=peers/'pc-original',
             launch_file=peers/'launch-original.json',audit_dir=base/'worker-audit')
assert not Path('/app/state/.paper-ready-discord-ed439e8').exists()
def stop_after_durable_intent(point,component):
    if point=='intent':raise RuntimeError('intent_prepared_before_offhost_copy_no_binding_write')
try:
    apply_transition(plan,**options,apply=True,
                     approval_ref='owner_20261025_deploy_discord_enabled_asap_ed439e8',fault=stop_after_durable_intent)
except RuntimeError as error:
    assert str(error)=='intent_prepared_before_offhost_copy_no_binding_write'
else:raise AssertionError('explicit pre-write intent pause did not occur')
inspection=inspect_transition(plan,**options)
assert all(c['state']=='before' for c in inspection['components'])
intent=json.loads((base/'worker-audit/intent.json').read_text())
print(json.dumps(dict(status='intent_prepared_all_bindings_original',inspection=inspection,intent=intent)))
