"""Check the exact local package bytes and both new correctness modules."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

RUN = Path(__file__).resolve().parent
receipt = RUN/'exact-package-verification-v3.json'
assert not receipt.exists(),'preserve terminal verification'
metadata = json.loads((RUN/'candidate-package-verification-v3.json').read_text())
package = Path(metadata['export'])
pins = json.loads(package.with_name(package.name+'.raw-sha256.json').read_text())
sha = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert all(sha(package/name)==digest for name,digest in pins.items())
code = """import json,sys,unittest
from pathlib import Path
sys.path[:0]=[str(Path('tests').resolve()),str(Path('.').resolve())]
from hermes_trading import supervisor_sync
from hermes_trading.reflection_supervision import _hash
from hermes_trading.paper_publication import _release_hashes
assert Path(supervisor_sync.__file__).resolve().is_relative_to(Path('.').resolve())
print(json.dumps({'runtime_map_hash':_hash(_release_hashes()),'runtime_files':len(_release_hashes())}))
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_supervisor_snapshot_retry','test_paper_snapshot_release_transition'])
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun==20 else 1)
"""
started = datetime.now(timezone.utc).isoformat()
result = subprocess.run([sys.executable,'-B','-X','utf8','-c',code],cwd=package,
                        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=120)
(RUN/'exact-package-tests-v3.log').write_text(result.stdout+result.stderr,encoding='utf-8')
changed = [name for name,digest in pins.items() if sha(package/name)!=digest]
actual = json.loads(result.stdout.splitlines()[0]) if result.stdout else None
value = dict(started_at=started,finished_at=datetime.now(timezone.utc).isoformat(),
    candidate_commit=metadata['candidate_commit'],exit_code=result.returncode,
    runtime_map_observed_from_package=actual,expected_runtime_map_hash=metadata['new_runtime_map_hash'],
    package_files=len(pins),changed_sources=changed,log_sha256=sha(RUN/'exact-package-tests-v3.log'),
    deployed=False,installed_in_actual_pc_runtime=False,actual_guards_changed=False)
value['passed'] = (result.returncode==0 and not changed and actual is not None and
                   actual['runtime_map_hash']==metadata['new_runtime_map_hash'] and actual['runtime_files']==129)
value['tests_passed'] = 20 if value['passed'] else None
receipt.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps(value,indent=2))
raise SystemExit(0 if value['passed'] else 1)
