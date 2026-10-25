"""Offline exact source export. No installed state, credentials or transport."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import io
import json
import os
import re
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile

ROOT=Path.cwd().resolve()
sys.path.insert(0,str(ROOT))
from hermes_trading.reflection_supervision import _hash
from scripts.paper_release_transition import validate_release_plan

OUTPUT=ROOT/'research/runs/paper-discord-reporting-v1'
PREVIOUS=ROOT/'research/runs/paper-operational-corrections-v1'
ALLOWED={'notifications.py','paired_paper.py','paired_worker.py','paper_boot.py'}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def normal(raw):return raw.replace(b'\r\n',b'\n')
def save(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def disk(path):return Path('\\\\?\\'+str(path)) if os.name=='nt' else path

commit=subprocess.check_output(['git','rev-parse',sys.argv[1]],cwd=ROOT,text=True).strip()
suffix='-'+sys.argv[2] if len(sys.argv)>2 else ''
if suffix and re.fullmatch('-v[1-9][0-9]*',suffix) is None:raise ValueError('version suffix required')
destination=OUTPUT/('release-upload-'+commit[:7]+suffix)
if destination.exists():raise ValueError('preserve existing export and pins')
old_root=PREVIOUS/'release-upload-49c8513'
old_pins=json.loads((PREVIOUS/'release-upload-49c8513.raw-sha256.json').read_text())
assert all(sha((old_root/name).read_bytes())==digest for name,digest in old_pins.items())
archive=subprocess.check_output(['git','archive','--format=tar',commit],cwd=ROOT)
files={};preserved=[]
with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as source:
    for member in source:
        path=PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('export path differs')
        if member.isdir():continue
        if not member.isfile():raise ValueError('plain source export required')
        raw=source.extractfile(member).read()
        if member.name in old_pins:
            previous=(old_root/member.name).read_bytes()
            if normal(previous)==normal(raw):raw=previous;preserved.append(member.name)
        files[member.name]=raw
old_plan=json.loads((PREVIOUS/'proposed-release-plan-49c8513.json').read_text())
old=old_plan['new'];new=deepcopy(old)
runtime={name.removeprefix('hermes_trading/'):sha(raw) for name,raw in files.items()
         if name.startswith('hermes_trading/') and name.endswith('.py')}
assert set(runtime)==set(old['runtime'])
assert {name for name in runtime if runtime[name]!=old['runtime'][name]}==ALLOWED
new['runtime']=runtime
assert sha(files['scripts/hermes_watch.py'])==old['watcher']
assert sha(files['scripts/native_hermes_watch.py'])==old['native_launcher']
plan=dict(old_commit='49c851315b98bb33b1eb78180fb789afe2a49f3d',new_commit=commit,
          target=old_plan['target'],old=old,new=new)
validate_release_plan(plan)
destination.mkdir()
for name,raw in files.items():
    path=disk(destination/name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
pins={name:sha(raw) for name,raw in sorted(files.items())}
assert all(sha(disk(destination/name).read_bytes())==digest for name,digest in pins.items())
save(OUTPUT/('release-upload-'+commit[:7]+suffix+'.raw-sha256.json'),pins)
save(OUTPUT/'proposed-release-plan.json',plan)
identity=json.loads((PREVIOUS/'proposed-original-identity-20261024.json').read_text())
save(OUTPUT/'proposed-original-identity.json',identity)
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),source_commit=commit,
           previous_commit=plan['old_commit'],files=len(files),runtime_modules=len(runtime),
           runtime_delta=sorted(ALLOWED),old_export_pins_verified=len(old_pins),
           old_raw_files_preserved=len(preserved),export=destination.name,
           old_supervisor_release=_hash(old),new_supervisor_release=_hash(new),
           old_runtime_map=_hash(old['runtime']),new_runtime_map=_hash(runtime),
           release_plan_sha256=sha((OUTPUT/'proposed-release-plan.json').read_bytes()),
           source_newline_equivalence_verified=True,deployed=False,discord_requests=0)
save(OUTPUT/'export-verification.json',value)
print(json.dumps(value,indent=2))
