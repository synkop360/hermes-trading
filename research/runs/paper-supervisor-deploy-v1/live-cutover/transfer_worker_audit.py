
"""Bounded transfer and raw validation of the native original closed worker copy."""
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
import hashlib,json,shutil,subprocess,tarfile,time
base=Path(__file__).resolve().parents[4]
live=Path(__file__).resolve().parent
ref=base.parent/'r27-ea752c5';wire=ref/'worker-audit.tar';assert not wire.exists()
cli='C:/Users/thoma/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe'
args=[cli,'ssh','--project','f78f42d9-20ec-44f6-ae3f-f8bb79ee7000','--service','8b94cbc9-293e-4222-a8a7-698b656985f7',
'--environment','fdcc71a3-00a9-410c-8d6f-59af9bc98d36','--deployment-instance','b295439f-053d-4911-8b55-5db4e01f00cc',
'--','timeout 38 tar -C /tmp/hermes-recovery-ea752c5 -cf - worker-audit']
started=time.monotonic()
with wire.open('xb') as output:
 r=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.PIPE,timeout=48)
assert r.returncode==0 and 0<wire.stat().st_size<550_000_000
stage=ref/'audit-closed-bundle';assert not stage.exists();stage.mkdir()
total=0;seen=set()
with tarfile.open(wire,'r:') as archive:
 for member in archive:
  name=PurePosixPath(member.name)
  assert not name.is_absolute() and '..' not in name.parts and name.parts[0]=='worker-audit'
  assert chr(92) not in member.name and ':' not in member.name and member.name not in seen and len(seen)<4096
  seen.add(member.name);target=stage.joinpath(*name.parts)
  assert target.resolve().is_relative_to(stage.resolve())
  if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
  assert member.isreg() and not member.issparse() and member.size>=0
  total+=member.size;assert total<500_000_000
  target.parent.mkdir(parents=True,exist_ok=True)
  with archive.extractfile(member) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)

import sys
sys.path.insert(0,str(Path('E:\\_OB_VAULTS\\Main Vault\\Projects\\Automatic Trading\\hermes-recovery-ea752c5')))
from scripts.paper_release_transition import _verify_rollback,_bounded,_hash
audit=stage/'worker-audit'
plan=json.loads((live/'frozen-live-transition-plan.json').read_text())
intent=json.loads((audit/'intent.json').read_text())
native=json.loads((live/'prepare_worker_intent.native-result.json').read_text())['result']
assert intent['intent_hash']==native['intent_hash'] and intent['root_binding']==native['root_binding']
assert intent['intent_hash']==_hash({k:v for k,v in intent.items() if k!='intent_hash'})
assert intent['transition_id']==plan['transition_id']
_bounded(_verify_rollback)(plan,dict(audit=audit))
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='original_remote_intent_and_rollback_verified_offhost',
 transition_id=plan['transition_id'],remote_root_binding=intent['root_binding'],intent_hash=intent['intent_hash'],
 offhost_audit=str(audit),wire_bytes=wire.stat().st_size,wire_sha256=hashlib.sha256(wire.read_bytes()).hexdigest(),
 all_live_bindings_still_original_at_intent=True,elapsed_seconds=time.monotonic()-started)
(live/'worker-intent-offhost-verified.json').write_text(json.dumps(value,indent=2))
print(json.dumps(value))
