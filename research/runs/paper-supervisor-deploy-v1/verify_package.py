"""Verify frozen bytes on Windows or cached, network-disabled Linux."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess,sys
RUN=Path(__file__).resolve().parent;metadata=json.loads((RUN/'package.json').read_text());package=Path(metadata['package'])
pins=json.loads(package.with_name(package.name+'.raw-sha256.json').read_text())
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
assert all(sha(package/name)==value for name,value in pins.items())
patterns=['test_paper_supervisor_recovery.py','test_paper_supervisor_health.py','test_paper_safety.py','test_paper_safety_worker.py',
    'test_supervisor_snapshot_retry.py','test_supervisor_sync.py','test_paired_worker.py','test_paired_primary_supervision.py',
    'test_paper_boot.py','test_paper_backup.py','test_pull_paper_backup.py','test_paper_publication.py','test_supervisor_launch.py',
    'test_native_supervisor_launcher.py','test_paper_notification_capture.py','test_paper_notifications.py',
    'test_paper_recovery_release_transition.py','test_paper_snapshot_release_transition.py']
code='''import json,sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path('tests').resolve()),str(Path('.').resolve())]
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash
assert _hash(_release_hashes())==EXPECTED
suite=unittest.TestSuite()
for pattern in PATTERNS:suite.addTests(unittest.defaultTestLoader.discover('tests',pattern=pattern))
result=unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=[str(x) for x in result.skipped],runtime_hash=_hash(_release_hashes()))))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''.replace('EXPECTED',repr(metadata['runtime_hash'])).replace('PATTERNS',repr(patterns))
platform=sys.argv[1];assert platform in ('windows','linux')
if platform=='windows':args=[sys.executable,'-B','-X','utf8','-c',code];kwargs=dict(cwd=package)
else:
    image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','hermes-paired-cost-reserve:20261015'],text=True).strip()
    args=['docker','run','--rm','--pull','never','--network','none','--read-only','--user','65534:65534',
        '--env','PYTHONPATH=/workspace:/workspace/tests','--env','PYTHONDONTWRITEBYTECODE=1','--tmpfs','/tmp:rw,nosuid,nodev,mode=1777',
        '--mount',f'type=bind,source={package},target=/workspace,readonly','--workdir','/workspace','--entrypoint','/app/.venv/bin/python',image,'-B','-X','utf8','-c',code]
    kwargs={}
started=datetime.now(timezone.utc).isoformat()
result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=600,**kwargs)
(RUN/(platform+'-package.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
structured=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
changed=[name for name,value in pins.items() if sha(package/name)!=value]
receipt=dict(platform=platform,started_at=started,finished_at=datetime.now(timezone.utc).isoformat(),exit_code=result.returncode,
    package=metadata,results=structured,changed_sources=changed,scope='affected exact-package checks; not full suite',network='none' if platform=='linux' else 'synthetic fixtures')
(RUN/(platform+'-package.json')).write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
raise SystemExit(0 if result.returncode==0 and not changed else 1)
