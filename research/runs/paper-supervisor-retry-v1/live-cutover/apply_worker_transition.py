import sys;sys.dont_write_bytecode=True
EXPECTED_INTENT='38e5570009775ed3c3c274e93c061d6107429f1e9e191ec7d383080528220606'
from pathlib import Path
from datetime import datetime,timezone
import json
from scripts.paper_release_transition import apply_transition,inspect_transition
from hermes_trading.paper_publication import _release_hashes
base=Path('/tmp/hermes-snapshot-20eb6af');peers=base/'peer-originals/pc-reference-bundle'
plan=json.loads((base/'frozen-live-transition-plan.json').read_text())
options=dict(worker_root='/app/state',supervisor_root=peers/'pc-original',launch_file=peers/'launch-original.json',audit_dir=base/'worker-audit')
assert _release_hashes()==plan['release_plan']['new']['runtime']
assert not Path('/app/state/.paper-ready-snapshot-20eb6af').exists()
intent=json.loads((base/'worker-audit/intent.json').read_text());assert intent['intent_hash']==EXPECTED_INTENT
receipt=apply_transition(plan,**options,apply=True,reconcile=True,approval_ref='owner_20261026_snapshot_20eb6af_paper_cutover')
inspection=inspect_transition(plan,**options)
assert receipt['status']=='completed' and all(c['state']=='after' for c in inspection['components'])
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),receipt=receipt,inspection=inspection,new_marker_released=False)))
