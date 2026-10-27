"""Exact five-file install after both durable intents and off-host verification."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shutil,sys
RUN=Path(__file__).resolve().parent;LIVE=RUN/'live-cutover';c=json.loads((LIVE/'config.json').read_text())
package=Path(c['package']);actual=Path(c['pc_project']);plan=json.loads((LIVE/'release-plan.json').read_text())
jobs=json.loads(Path('C:/Users/thoma/AppData/Local/hermes/cron/jobs.json').read_text(encoding='utf-8'))['jobs']
assert all(not j['enabled'] for j in jobs if j['id'] in ('2f6c5088c6ff','a316e6ed4190'))
for name in ('worker-intent-offhost-verified.json','pc-intent-offhost-verified.json'):
    assert (LIVE/name).is_file()
sys.path.insert(0,str(actual));from scripts.hermes_watch import _supervisor_release
from hermes_trading.reflection_supervision import _hash
assert _supervisor_release()==plan['old']
pins=json.loads(package.with_name(package.name+'.raw-sha256.json').read_text())
changed=['hermes_trading/'+name for name in ('notifications.py','paper_safety.py','paper_supervisor_health.py','supervisor_sync.py')]+['scripts/paper_release_transition.py']
backup=LIVE/'pc-source-before';assert not backup.exists();backup.mkdir()
for name in changed:
    assert hashlib.sha256((package/name).read_bytes()).hexdigest()==pins[name]
    dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(actual/name,dest)
    assert dest.read_bytes()==(actual/name).read_bytes()
for name in changed:shutil.copyfile(package/name,actual/name)
release=_supervisor_release();assert release==plan['new']
assert all(hashlib.sha256((actual/name).read_bytes()).hexdigest()==pins[name] for name in changed)
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),status='exact_five_file_pc_source_install_verified',changed=changed,
           runtime_files=len(release['runtime']),new_release_hash=_hash(release),watcher_and_native_unchanged=True,
           other_125_runtime_files_unchanged=True,raw_installed_sha256={n:pins[n] for n in changed})
with (LIVE/'pc-source-install.json').open('x') as f:json.dump(value,f,indent=2)
print(json.dumps(value))
