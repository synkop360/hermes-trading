
"""Bounded transfer and raw validation of the native original closed worker copy."""
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
import hashlib,json,shutil,subprocess,tarfile,time
base=Path(__file__).resolve().parents[4]
live=Path(__file__).resolve().parent
ref=base.parent/'d439';wire=ref/'worker-original-bundle.tar';assert not wire.exists()
cli='C:/Users/thoma/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe'
args=[cli,'ssh','--project','f78f42d9-20ec-44f6-ae3f-f8bb79ee7000','--service','8b94cbc9-293e-4222-a8a7-698b656985f7',
'--environment','fdcc71a3-00a9-410c-8d6f-59af9bc98d36','--deployment-instance','b45705e2-e89a-49fe-89c5-f2f81bbac862',
'--','timeout 38 tar -C /tmp/hermes-discord-ed439e8 -cf - worker-original-bundle']
started=time.monotonic()
with wire.open('xb') as output:
 r=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.PIPE,timeout=48)
assert r.returncode==0 and 0<wire.stat().st_size<550_000_000
stage=ref/'closed-bundle';assert not stage.exists();stage.mkdir()
total=0;seen=set()
with tarfile.open(wire,'r:') as archive:
 for member in archive:
  name=PurePosixPath(member.name)
  assert not name.is_absolute() and '..' not in name.parts and name.parts[0]=='worker-original-bundle'
  assert chr(92) not in member.name and ':' not in member.name and member.name not in seen and len(seen)<4096
  seen.add(member.name);target=stage.joinpath(*name.parts)
  assert target.resolve().is_relative_to(stage.resolve())
  if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
  assert member.isreg() and not member.issparse() and member.size>=0
  total+=member.size;assert total<500_000_000
  target.parent.mkdir(parents=True,exist_ok=True)
  with archive.extractfile(member) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
bundle=stage/'worker-original-bundle';native=bundle/'worker-native-snapshot'
raw=(native/'snapshot_inventory.json').read_bytes();inventory=json.loads(raw)
receipt=json.loads((bundle/'capture-receipt.json').read_text())
assert hashlib.sha256(raw).hexdigest()==receipt['native_inventory_sha256']
working=ref/'w';assert not working.exists();working.mkdir()
for entry in inventory['files']:
 name=PurePosixPath(entry['path']);assert not name.is_absolute() and '..' not in name.parts
 p=native.joinpath(*name.parts)
 assert p.stat().st_size==entry['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256']
 dest=working.joinpath(*name.parts);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
dirs=json.loads((bundle/'worker-directories.json').read_text())
for name in dirs:
 p=working.joinpath(*PurePosixPath(name).parts);assert p.resolve().is_relative_to(working.resolve());p.mkdir(parents=True,exist_ok=True)
assert not any(p.name.endswith(('-wal','-shm','-journal')) for p in working.rglob('*') if p.is_file())
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),worker_reference=str(working),native_files_verified=len(inventory['files']),
 native_inventory_sha256=hashlib.sha256(raw).hexdigest(),wire_bytes=wire.stat().st_size,wire_sha256=hashlib.sha256(wire.read_bytes()).hexdigest(),
 directories_preserved=len(dirs),source_sidecar_free=True,original_opening_hash=receipt['opening_hash'],elapsed_seconds=time.monotonic()-started)
(live/'worker-original-reference.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value))
