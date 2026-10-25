"""Use the cached Linux image with no network and a read-only frozen package."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

RUN = Path(__file__).resolve().parent
receipt = RUN/'linux-verification-v3.json'
assert not receipt.exists(),'preserve terminal evidence'
metadata = json.loads((RUN/'candidate-package-verification-v3.json').read_text())
package = Path(metadata['export'])
pins = json.loads(package.with_name(package.name+'.raw-sha256.json').read_text())
sha = lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
assert all(sha(package/name)==digest for name,digest in pins.items())
code = """import json,sys,unittest
sys.path[:0]=['/workspace/tests','/workspace']
from hermes_trading.reflection_supervision import _hash
from hermes_trading.paper_publication import _release_hashes
print(json.dumps({'runtime_files':len(_release_hashes()),'runtime_map_hash':_hash(_release_hashes()),'python':sys.version.split()[0]}))
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_supervisor_snapshot_retry','test_paper_snapshot_release_transition','test_paper_safety.PaperSafetyTests.test_volume_symlink_is_not_followed_and_reports_unknown'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({'tests':result.testsRun,'skipped':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors)}))
raise SystemExit(0 if result.wasSuccessful() and result.testsRun==21 and not result.skipped else 1)
"""
args = ['docker','run','--rm','--pull','never','--name','hermes-snapshot-a79-linux-v3',
    '--network','none','--read-only','--user','65534:65534',
    '--tmpfs','/tmp:rw,nosuid,nodev,mode=1777','--mount',
    f'type=bind,source={package},target=/workspace,readonly','--workdir','/workspace',
    '-i','--entrypoint','/app/.venv/bin/python','hermes-paired-cost-reserve:20261015','-B','-X','utf8','-']
started = datetime.now(timezone.utc).isoformat()
result = subprocess.run(args,input=code,capture_output=True,text=True,timeout=180)
log = RUN/'linux-tests-v3.log'
log.write_text(result.stdout+result.stderr,encoding='utf-8')
changed = [name for name,digest in pins.items() if sha(package/name)!=digest]
observations = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
value = dict(started_at=started,finished_at=datetime.now(timezone.utc).isoformat(),
    candidate_commit=metadata['candidate_commit'],exit_code=result.returncode,image='hermes-paired-cost-reserve:20261015',
    cached_image_id='13054ce2ae8c',network='none',source_mount='read_only',user='65534:65534',
    log_sha256=sha(log),structured_stdout=observations,changed_package_files=changed,
    product_or_host_state_installed=False,paid_calls=0)
value['passed'] = (result.returncode==0 and not changed and len(observations)==2 and
                   observations[0]['runtime_map_hash']==metadata['new_runtime_map_hash'])
receipt.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps(value,indent=2))
raise SystemExit(0 if value['passed'] else 1)
