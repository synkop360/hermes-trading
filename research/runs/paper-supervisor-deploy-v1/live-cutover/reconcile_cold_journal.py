import sys
sys.dont_write_bytecode=True
IDENTITY={'account_hashes': {'adaptive_100': 'ab01234ad2feb966dda08ae4d7334835a08d838cba55bb5ff515723c3f6e7c4d', 'adaptive_150': '114e0c50abc0237b326b8b8be92e01cee16ff58dbf549f2dee037028c5aa2ff4', 'adaptive_200': '8537e0f7f17abc8a092f2ca8e39ad8b1d290f8ee222296fbdc834fe00bb488f3', 'adaptive_50': 'c6cb98dfffbfefb519cd7dc6cfe213cc61f2bfc741d13bc7d23f18e652f2cb72', 'control_100': 'd2bf582f19519c6ea828762c53115a20009668676338fbdba9b7712b8fd0aec3', 'control_150': '33aead4b07aeddd677babf6b1f344df77f3c0ab2a71932ec3f086d0847468527', 'control_200': '25288c8ffd776d40ef0a1cac47b63f1b033e7093a5b01bd551734d62eac589bc', 'control_50': '164833f1175f88b92747e404a0d963fec6344debbe4a07d2f8548df3b7544268'}, 'experiment_id': 'paper-beta-v1-20261021-6c9d520', 'manifest_sha256': '43ec91c55d05f6f04f7a227dd9adc23a2283dd0d788a2255903cb63ee60cbb64', 'opening_hash': '50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349', 'policy_hash': 'db684ab9780a550de2cbaa22d586dfe706154cea47dfd6bef8ed247ff56d1577'}
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import hashlib,json,sqlite3
from scripts.paper_release_transition import _database_hash
from hermes_trading.paired_inception import read_paired_inception
from hermes_trading.reflection_supervision import _hash
root=Path('/app/state');bundle=Path('/tmp/hermes-recovery-ea752c5/worker-original-bundle');snapshot=bundle/'worker-native-snapshot'
assert not (root/'.paper-ready-recovery-ea752c5').exists()
for proc in Path('/proc').iterdir():
 if proc.name.isdigit():
  try:command=(proc/'cmdline').read_bytes()
  except OSError:continue
  assert bytes([0])+b'-m'+bytes([0])+b'hermes_trading.paper_boot'+bytes([0]) not in command
path=root/'worker_runtime.sqlite';journal=path.with_name(path.name+'-journal')
assert journal.is_file() and journal.read_bytes()[:8]==bytes(8),'only observed cold journal may be reconciled'
initial_sidecars=sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name.endswith(('-wal','-shm','-journal')))
assert initial_sidecars==['worker_runtime.sqlite-journal'],'unexpected sidecar requires separate inspection'
with closing(sqlite3.connect((snapshot/'worker_runtime.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True)) as copied:expected=_database_hash(copied)
with closing(sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=0)) as db:
 db.execute('PRAGMA journal_mode=DELETE');db.execute('BEGIN EXCLUSIVE')
 before=_database_hash(db);assert before==expected,'published original copy differs'
 version=db.execute('PRAGMA user_version').fetchone()[0];schema=db.execute('PRAGMA schema_version').fetchone()[0]
 db.execute('PRAGMA user_version='+str(version));db.commit()
 assert _database_hash(db)==before and db.execute('PRAGMA user_version').fetchone()[0]==version and db.execute('PRAGMA schema_version').fetchone()[0]==schema
assert not journal.exists()
sidecars=[str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name.endswith(('-wal','-shm','-journal'))]
assert not sidecars,'closed state still has unsupported sidecars'
def immutable(path,*,check):
 check();return closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0))
opening=read_paired_inception(root,database_factory=immutable);assert _hash(opening)==IDENTITY['opening_hash']
assert hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()==IDENTITY['manifest_sha256']
inventory_raw=(snapshot/'snapshot_inventory.json').read_bytes();inventory=json.loads(inventory_raw)
for record in inventory['files']:
 p=snapshot/record['path'];assert p.stat().st_size==record['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==record['sha256']
directories=[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_dir()]
(bundle/'worker-directories.json').write_text(json.dumps(directories))
with closing(sqlite3.connect((root/'adaptive_200/paper_publications.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True)) as db:counts=dict(db.execute('SELECT status,COUNT(*) FROM paired_batches GROUP BY status'))
assert not counts.get('pending')
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='published_native_original_snapshot_verified_after_cold_journal_reconciliation',native_inventory_sha256=hashlib.sha256(inventory_raw).hexdigest(),files=len(inventory['files']),snapshot_bytes=sum(r['bytes'] for r in inventory['files'])+len(inventory_raw),opening_hash=_hash(opening),manifest_sha256=IDENTITY['manifest_sha256'],batch_counts=counts,directories=len(directories),worker_remains_stopped=True,source_sidecar_free=True,worker_runtime_logical_hash=before,user_version_unchanged=version,schema_version_unchanged=schema,journal_removed_by_sqlite_commit=True,bundle=str(bundle))
(bundle/'capture-receipt.json').write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
