import sys;sys.dont_write_bytecode=True
EXPECTED_INTENT='15c9258d2fd78f30f65acdee37cabc16aa72dd2d6075a9461cf3a77139778991'
from pathlib import Path
from datetime import datetime,timezone
import json
from scripts.paper_release_transition import apply_transition,inspect_transition
from hermes_trading.paper_publication import _release_hashes
base=Path('/tmp/hermes-recovery-ea752c5');peers=base/'peer-originals/pc-reference-bundle'
plan=json.loads((base/'frozen-live-transition-plan.json').read_text())
options=dict(worker_root='/app/state',supervisor_root=peers/'pc-original',launch_file=peers/'launch-original.json',audit_dir=base/'worker-audit')
assert _release_hashes()==plan['release_plan']['new']['runtime']
assert not Path('/app/state/.paper-ready-recovery-ea752c5').exists()
intent=json.loads((base/'worker-audit/intent.json').read_text());assert intent['intent_hash']==EXPECTED_INTENT
receipt=apply_transition(plan,**options,apply=True,reconcile=True,approval_ref='owner_20261027_always_deploy_paper_only')
inspection=inspect_transition(plan,**options)
assert receipt['status']=='completed' and all(c['state']=='after' for c in inspection['components'])
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),receipt=receipt,inspection=inspection,new_marker_released=False)))
