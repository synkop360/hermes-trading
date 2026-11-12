import os
assert os.environ.get('HERMES_NOTIFY_ENABLED','').strip().lower()=='true','notification flag must match approved boot path'
from pathlib import Path
import json
from scripts.paper_release_transition import apply_transition,inspect_transition
base=Path('/tmp/hermes-discord-ed439e8');peers=base/'peer-originals/pc-reference-bundle'
plan=json.loads((base/'frozen-live-transition-plan.json').read_text())
options=dict(worker_root='/app/state',supervisor_root=peers/'pc-original',
             launch_file=peers/'launch-original.json',audit_dir=base/'worker-audit')
assert not Path('/app/state/.paper-ready-discord-ed439e8').exists()
intent=json.loads((base/'worker-audit/intent.json').read_text())
assert intent['intent_hash']=='c2bc8d612a0f41dff8c1e8c43dea2a9bbb10c25334d26f0ed9eaceea59cef4aa'
receipt=apply_transition(plan,**options,apply=True,reconcile=True,
                         approval_ref='owner_20261025_deploy_discord_enabled_asap_ed439e8')
inspection=inspect_transition(plan,**options)
assert receipt['status']=='completed' and all(c['state']=='after' for c in inspection['components'])
print(json.dumps(dict(receipt=receipt,inspection=inspection,new_marker_released=False)))
