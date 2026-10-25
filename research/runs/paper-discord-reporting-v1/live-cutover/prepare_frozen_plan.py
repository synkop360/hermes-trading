
from pathlib import Path
from datetime import datetime,timezone
import json,sys,time
base=Path(__file__).resolve().parents[4];sys.path.insert(0,str(base))
from scripts.paper_release_transition import prepare_plan
live=Path(__file__).resolve().parent;run=live.parent;ref=base.parent/'d439'
start=time.monotonic()
plan=prepare_plan(ref/'w',ref/'p',ref/'l.json',release_plan=json.loads((run/'proposed-release-plan.json').read_text()),
 expected_identity=json.loads((run/'proposed-original-identity.json').read_text()))
for p in (live/'frozen-live-transition-plan.json',ref/'plan.json'):
 with p.open('x',encoding='utf-8') as stream:json.dump(plan,stream,indent=2,sort_keys=True);stream.write('\n')
print(json.dumps(dict(status='fresh_frozen_plan_prepared',observed_at=datetime.now(timezone.utc).isoformat(),
 transition_id=plan['transition_id'],components=len(plan['components']),protected_items=len(plan['protected']),elapsed_seconds=time.monotonic()-start)))
