"""Read-only verification of the completed Oct25 E: daily copy; no transport."""
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
import hashlib, json, sqlite3, sys, time
MAIN = Path(r"E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading")
sys.path.insert(0,str(MAIN))
from hermes_trading.paper_backup import _verified
from hermes_trading.paired_inception import read_paired_inception
from hermes_trading.reflection_supervision import _hash
OUT=Path(__file__).resolve().parent
identity=json.loads((OUT/'proposed-original-identity.json').read_text(encoding='utf-8'))
stdout=MAIN/'state/paired-supervisor/paper-beta-v1-20261021-6c9d520/backup-last.stdout.json'
task=json.loads(stdout.read_text(encoding='utf-8-sig'))
root=Path(task['path'])
assert root.name=='state-2026-10-25-73a6a6a8ab53'
assert root.parent.samefile(MAIN/'backups/paper-beta-v1/paper-beta-v1-20261021-6c9d520')
raw=(root/'manifest.json').read_bytes()
assert hashlib.sha256(raw).hexdigest()==identity['manifest_sha256']
source_identity=hashlib.sha256(b'/app/state\0'+raw).hexdigest()
deadline=time.monotonic()+60
def check():
    if time.monotonic()>=deadline:raise TimeoutError('bounded read-only daily-copy verification deadline')
receipt=_verified(root,source_identity,check,500_000_000)
sidecars=[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name.endswith(('-wal','-shm','-journal'))]
assert not sidecars
def immutable(path,*,check):
    check()
    uri_path=Path(str(path)[4:]) if str(path).startswith(chr(92)*2+'?'+chr(92)) else path
    assert uri_path.samefile(path)
    return closing(sqlite3.connect(uri_path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0))
opening=read_paired_inception(root,database_factory=immutable,check=check)
assert _hash(opening)==identity['opening_hash']
accounts={}
for name,expected in identity['account_hashes'].items():
    path=root/name/'wallet_account.json'
    observed=_hash(json.loads(path.read_text(encoding='utf-8')))
    assert observed==expected
    accounts[name]=observed
inventory=json.loads((root/'snapshot_inventory.json').read_text(encoding='utf-8'))
result=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='read_only_completed_daily_copy_verified',
    generation=root.name,captured_at=receipt['captured_at'],source_identity=source_identity,
    inventory_sha256=receipt['sha256'],files=len(inventory['files']),bytes=sum(x['bytes'] for x in inventory['files']),
    manifest_sha256=identity['manifest_sha256'],opening_hash=_hash(opening),opening_at=opening['at'],
    account_hashes=accounts,sidecars=sidecars,task_stdout_status=task['status'],
    task_stdout_off_host_verified=task['off_host_verified'],task_stdout_sha256=hashlib.sha256(stdout.read_bytes()).hexdigest(),
    snapshot_modified=False,live_state_modified=False,network_requests=0,restore_test=False,qualification=False)
target=OUT/'daily-copy-verification-20261025.json'
with target.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2);stream.write('\n')
print(json.dumps(result,indent=2))
