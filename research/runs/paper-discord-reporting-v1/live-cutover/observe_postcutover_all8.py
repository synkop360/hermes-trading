"""Read-only installed runtime observation; never initializes or invokes models."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib, json
from pathlib import Path
import sqlite3

from hermes_trading.paired_inception import read_paired_inception
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.reflection_supervision import _hash
from hermes_trading.paper_publication import _release_hashes

root=Path('/app/state');primary=root/'adaptive_200'
def read(path):
    if path.stat().st_size > 4*1024**2:raise ValueError('bounded observation metadata required')
    return json.loads(path.read_bytes())
def database(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN');return closing(db)
manifest=read(root/'manifest.json')
try:opening=read_paired_inception(root,database_factory=lambda path,check:database(path))
except sqlite3.OperationalError:opening=None
accounts={}
for profile in manifest['profiles']:
    name=profile['directory'];state=root/name;account=read(state/'wallet_account.json')
    report=read(state/'wallet_metrics.json')
    try:
        with database(state/'trading.db') as db:
            positions=dict(db.execute('SELECT status,COUNT(*) FROM positions GROUP BY status'))
            orders=db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
            decisions=[list(r) for r in db.execute('SELECT action,COUNT(*) FROM decisions GROUP BY action')]
    except sqlite3.OperationalError:
        positions=orders=decisions=None
    accounts[name]=dict(account_hash=_hash(account),account_cost_t0=account['activated_at'],
        strategy_sha256=hashlib.sha256((state/'strategy.yaml').read_bytes()).hexdigest(),
        positions=positions,orders=orders,decisions=decisions,report=report)
try:
    with database(primary/'paper_publications.sqlite') as db:
        counts=dict(db.execute('SELECT status,COUNT(*) FROM paired_batches GROUP BY status'))
        row=db.execute("SELECT batch_id,input_hash,completed FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
        latest=None if row is None else dict(batch_id=row[0],input_hash=row[1],completed=load_packet(db,row[2]))
except sqlite3.OperationalError:
    counts=latest=None
try:
    with database(root/'worker_runtime.sqlite') as db:
        lifecycle=json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
except sqlite3.OperationalError as error:
    lifecycle={'status':'unavailable_under_native_owner','error_type':type(error).__name__}
backup=Path('/tmp/hermes-paper-daily-backups')
value=dict(kind='read_only_current_installed_worker',observed_at=datetime.now(timezone.utc).isoformat(),
    experiment_id=read(primary/'reflection_owner.json')['experiment_id'],manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
    opening_hash=_hash(opening) if opening else None,opening_at=opening.get('at') if opening else None,
    policy_hash=_hash(read(primary/'reflection_owner.json')),runtime=_release_hashes(),
    accounts=accounts,batch_counts=counts,latest=latest,heartbeat=read(primary/'heartbeat.json'),
    worker_lifecycle=lifecycle,completed_backup_generations=len(list(backup.glob('*/complete.json'))) if backup.exists() else 0,
    limitations=['live observation is not a closed consistent backup','profitability remains unqualified'])

from hermes_trading.notifications import resolve_notification_settings
environment={}
for item in Path('/proc/1/environ').read_bytes().split(b'\0'):
    name,separator,setting=item.partition(b'=')
    if separator:environment[name.decode()]=setting.decode(errors='replace')
settings=resolve_notification_settings(environment)
value['runtime_environment_predicates']={
 'mode_paper':environment.get('HERMES_TRADING_MODE')=='paper',
 'real_orders_explicit_false':environment.get('HERMES_TRADING_ALLOW_REAL_ORDERS','').lower()=='false',
 'risk_acceptance_not_true':environment.get('HERMES_TRADING_I_ACCEPT_RISK','').lower()!='true',
 'webhook_present':bool(settings.webhook_url),'notifications_enabled':settings.enabled,
 'notification_mode':settings.mode,'summary_every_cycles':settings.summary_every_cycles}
value['supervisor_safety']=read(root/'paper_safety_state.json')
value['worker_limits']=read(root/'worker_limits.json')
value['new_readiness_absent']=not(root/'.paper-ready-discord-ed439e8').exists()
value['known_processes']=[]
for p in Path('/proc').iterdir():
 if p.name.isdigit():
  try:args=(p/'cmdline').read_bytes().split(b'\0')
  except OSError:continue
  modules=[x.decode() for x in args if x in (b'hermes_trading.paper_boot',b'hermes_trading.colocated',b'hermes_trading.paired_worker')]
  if modules:value['known_processes'].append(dict(pid=int(p.name),modules=modules))

print(json.dumps(value,sort_keys=True))
