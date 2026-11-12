from pathlib import Path
from datetime import datetime,timezone
import json,os,sys
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text())
assert (LIVE/'pc-native-launch-validated.json').is_file() and (LIVE/'release_successor_readiness.native-result.json').is_file()
sys.path.insert(0,c['pc_project'])
os.environ['HERMES_PROPOSAL_PYTHON']='C:/Users/thoma/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe'
from scripts import hermes_watch as watcher
from hermes_trading.reflection_supervision import load_owner_policy
with watcher.supervised_launch(Path(c['actual_launch'])):
    policy=load_owner_policy(watcher.STATE_DIR)
    watcher._refresh_supervised_access(policy)
    receipt=watcher._sync_supervised_usage(policy)
    access=json.loads((watcher.STATE_DIR/'model_access.json').read_text())
    assert access['payment']=='verified_free' and access['paid_calls_authorized'] is False
    assert access['renewal']['inference_test_performed'] is False
result=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='existing_usage_import_completed',receipt=receipt,inference_test_performed=False,paid_calls_authorized=False)
with (LIVE/'startup-usage-bootstrap.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
