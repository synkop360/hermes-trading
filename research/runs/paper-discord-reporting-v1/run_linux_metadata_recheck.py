"""Native Linux correctness on the exact export, cached image, network disabled."""
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time

ROOT=Path.cwd().resolve();OUTPUT=ROOT/'research/runs/paper-discord-reporting-v1/linux-metadata-recheck-ed439e8'
OUTPUT.mkdir(exist_ok=True)
if (OUTPUT/'verification.json').exists():raise ValueError('preserve terminal evidence')
EXPORT=ROOT/'research/runs/paper-discord-reporting-v1/release-upload-ed439e8'
PINS=EXPORT.with_name(EXPORT.name+'.raw-sha256.json')
NAME='hermes-discord-metadata-ed439e8'
IDS=['test_paper_notifications.PaperNotificationProcessShutdownTests.test_stalled_metadata_never_keeps_asyncio_run_alive_after_cancellation']
CODE='''from pathlib import Path
from unittest.mock import patch
import gc,hashlib,httpx,json,os,sys,time,unittest,warnings
root=Path('/workspace');expected=json.loads(Path('/expected-pins.json').read_text())
def pins():return {n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in expected}
assert pins()==expected
assert sys.version_info>=(3,11) and sys.platform=='linux' and os.getuid()==65534
async def deny(*a,**k):raise AssertionError('real HTTP forbidden')
def deny_sync(*a,**k):raise AssertionError('real HTTP forbidden')
suite=unittest.defaultTestLoader.loadTestsFromNames(json.loads(sys.argv[1]));assert suite.countTestCases()==1
tick=time.monotonic()
with warnings.catch_warnings(record=True) as caught:
 warnings.simplefilter('always',ResourceWarning)
 with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',deny),patch.object(httpx.HTTPTransport,'handle_request',deny_sync):
  result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite)
 gc.collect()
foreign=[name for name,module in list(sys.modules.items()) if (name.startswith('hermes_trading') or name.startswith('scripts.')) and getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(root)]
resource=[str(w.message) for w in caught if issubclass(w.category,ResourceWarning)]
value=dict(test_groups=json.loads(sys.argv[1]),tests=result.testsRun,passed=result.testsRun-len(result.errors)-len(result.failures)-len(result.skipped),failures=[(str(t),e) for t,e in result.failures],errors=[(str(t),e) for t,e in result.errors],skipped=[(str(t),e) for t,e in result.skipped],resource_warnings=resource,source_files=len(expected),source_pins_unchanged=pins()==expected,foreign_imports=foreign,elapsed_seconds=time.monotonic()-tick,python=sys.version,platform=sys.platform,uid=os.getuid(),scope='Exact native metadata process shutdown case recheck; not full Linux qualification',network='none',deployed=False,discord_requests=0)
print('HERMES_LINUX_RESULT='+json.dumps(value))
sys.exit(0 if result.wasSuccessful() and not result.skipped and not resource and not foreign and value['source_pins_unchanged'] else 1)
'''
args=['docker','run','--rm','--pull','never','--name',NAME,'--network','none','--read-only','--cap-drop','ALL',
 '--security-opt','no-new-privileges','--pids-limit','64','--memory','512m','--cpus','1','--user','65534:65534',
 '--tmpfs','/tmp:rw,nosuid,nodev,size=536870912,mode=1777',
 '--mount',f'type=bind,source={EXPORT},target=/workspace,readonly',
 '--mount',f'type=bind,source={PINS},target=/expected-pins.json,readonly',
 '--workdir','/workspace','--entrypoint','/usr/bin/env',
 'sha256:13054ce2ae8c23123504f8e7219187a8e3e436ceec15f2a004edf997374ad889',
 '-i','PATH=/usr/local/bin:/usr/bin:/bin','TMPDIR=/tmp','PYTHONPATH=/workspace:/workspace/tests',
 'PYTHONDONTWRITEBYTECODE=1','/app/.venv/bin/python','-B','-c',CODE,json.dumps(IDS)]
(OUTPUT/'command.json').write_text(json.dumps(dict(argv=args,started_at=datetime.now(timezone.utc).isoformat()),indent=2)+'\n')
with (OUTPUT/'stdout.log').open('x',encoding='utf-8') as out,(OUTPUT/'stderr.log').open('x',encoding='utf-8') as err:
    completed=subprocess.run(args,stdout=out,stderr=err,timeout=600)
lines=(OUTPUT/'stdout.log').read_text().splitlines()
record=next((line.split('=',1)[1] for line in reversed(lines) if line.startswith('HERMES_LINUX_RESULT=')),None)
value=json.loads(record) if record else dict(status='no_terminal_test_receipt')
value.update(exit_code=completed.returncode,observed_at=datetime.now(timezone.utc).isoformat())
remaining=subprocess.run(['docker','ps','-a','--filter','name=^/'+NAME+'$','--format','{{.ID}}'],capture_output=True,text=True,check=True)
value['owned_container_absent']=not remaining.stdout.strip()
(OUTPUT/'verification.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(json.dumps(value,indent=2))
raise SystemExit(completed.returncode if value['owned_container_absent'] else 1)
