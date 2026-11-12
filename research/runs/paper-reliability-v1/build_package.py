"""Freeze a reviewed commit over the verified installed raw-byte package."""
import hashlib,json,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3];RUN=Path(__file__).resolve().parent
BASE='ea752c5f3990acff2a6f7164be733fe2bf92df35'
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
sha=lambda raw:hashlib.sha256(raw).hexdigest()
base=ROOT.parent/'hermes-recovery-ea752c5'
pins=json.loads(base.with_name(base.name+'.raw-sha256.json').read_text())
data={name:(base/name).read_bytes() for name in pins}
assert all(sha(raw)==pins[name] for name,raw in data.items())
prior=json.loads((ROOT/'research/runs/paper-reliability-v1/predecessor-release-plan.json').read_text())
changed=subprocess.check_output(['git','diff','--name-only',BASE,commit],cwd=ROOT,text=True).splitlines()
overlays={name for name in changed if name.endswith('.py') and name.startswith(('hermes_trading/','scripts/','tests/'))}
allowed={'hermes_trading/'+name for name in ('supervisor_sync.py',)}
assert {name for name in overlays if name.startswith('hermes_trading/')}==allowed
assert {name for name in overlays if name.startswith('scripts/')}=={'scripts/paper_release_transition.py'}
overlays|={'AGENTS.md','docs/PAPER_SUPERVISOR_RELIABILITY.md','research/runs/paper-reliability-v1/predecessor-release-plan.json'}
for name in sorted(overlays):data[name]=subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT)
release=dict(runtime={name[len('hermes_trading/'):]:sha(raw) for name,raw in data.items() if name.startswith('hermes_trading/') and name.endswith('.py')},
    watcher=sha(data['scripts/hermes_watch.py']),native_launcher=sha(data['scripts/native_hermes_watch.py']))
plan=dict(old_commit=BASE,new_commit=commit,target=prior['target'],old=prior['new'],new=release)
sys.path.insert(0,str(ROOT))
from scripts.paper_release_transition import validate_release_plan,_purpose
from hermes_trading.reflection_supervision import _hash
validate_release_plan(plan)
destination=ROOT.parent/('hermes-reliability-'+commit[:7]);assert not destination.exists()
for name,raw in data.items():
    path=destination/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
inventory={name:sha((destination/name).read_bytes()) for name in sorted(data)}
assert all(inventory[name]==sha(raw) for name,raw in data.items())
destination.with_name(destination.name+'.raw-sha256.json').write_text(json.dumps(inventory,indent=2)+'\n')
(RUN/'release-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
result=dict(commit=commit,package=str(destination),files=len(inventory),runtime_files=len(release['runtime']),
    runtime_hash=_hash(release['runtime']),release_hash=_hash(release),purpose=_purpose(plan),
    overlays=sorted(overlays),inventory_sha256=sha(destination.with_name(destination.name+'.raw-sha256.json').read_bytes()))
(RUN/'package.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
