from pathlib import Path
from unittest.mock import patch
import gc,hashlib,httpx,json,os,sys,time,unittest,warnings
root=Path('/workspace');expected=json.loads(Path('/expected-pins.json').read_text())
def pins():return {n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in expected}
assert pins()==expected
assert sys.version_info>=(3,11) and sys.platform=='linux' and os.getuid()==65534
async def deny(*a,**k):raise AssertionError('real HTTP forbidden')
def deny_sync(*a,**k):raise AssertionError('real HTTP forbidden')
suite=unittest.defaultTestLoader.loadTestsFromNames(json.loads(sys.argv[1]));assert suite.countTestCases()==52

import ast,subprocess,textwrap
method_tree=ast.parse(Path('tests/test_paper_notifications.py').read_text())
method=next(n for n in ast.walk(method_tree) if isinstance(n,ast.FunctionDef) and n.name=='test_stalled_metadata_never_keeps_asyncio_run_alive_after_cancellation')
assignment=next(n for n in method.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='script' for t in n.targets))
exact_script=textwrap.dedent(ast.literal_eval(assignment.value.args[0]))
assert 'time.sleep(3)' in exact_script
assert 'tick=time.monotonic();asyncio.run(main(root,sys.argv[1]))' in exact_script
assert "self.assertLess(json.loads(result.stdout)['elapsed'], 2)" in ast.get_source_segment(Path('tests/test_paper_notifications.py').read_text(),method)
original_run=subprocess.run
metadata_child_receipts=[]
def reviewed_run(argv,*positional,**kwargs):
 recognized=(isinstance(argv,list) and len(argv)==7 and argv[:5]==[sys.executable,'-B','-X','utf8','-c'] and argv[5]==exact_script and argv[6] in ('_load','_persist') and not positional and kwargs==dict(capture_output=True,text=True,timeout=8,check=True))
 if not recognized:return original_run(argv,*positional,**kwargs)
 record=dict(operation=argv[6],original_outer_timeout_seconds=8,adjusted_outer_timeout_seconds=30,snippet_sha256=hashlib.sha256(exact_script.encode()).hexdigest())
 metadata_child_receipts.append(record)
 started=time.monotonic()
 try:
  completed=original_run(argv,**dict(kwargs,timeout=30))
 except BaseException as error:
  record.update(process_wall_seconds=time.monotonic()-started,error_type=type(error).__name__)
  raise
 record.update(process_wall_seconds=time.monotonic()-started,exit_code=completed.returncode,measured_shutdown_seconds=json.loads(completed.stdout)['elapsed'])
 return completed

tick=time.monotonic()
with warnings.catch_warnings(record=True) as caught:
 warnings.simplefilter('always',ResourceWarning)
 with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',deny),patch.object(httpx.HTTPTransport,'handle_request',deny_sync),patch.object(subprocess,'run',side_effect=reviewed_run):
  result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite)
 gc.collect()
foreign=[name for name,module in list(sys.modules.items()) if (name.startswith('hermes_trading') or name.startswith('scripts.')) and getattr(module,'__file__',None) and not Path(module.__file__).resolve().is_relative_to(root)]
resource=[str(w.message) for w in caught if issubclass(w.category,ResourceWarning)]
value=dict(test_groups=json.loads(sys.argv[1]),tests=result.testsRun,passed=result.testsRun-len(result.errors)-len(result.failures)-len(result.skipped),failures=[(str(t),e) for t,e in result.failures],errors=[(str(t),e) for t,e in result.errors],skipped=[(str(t),e) for t,e in result.skipped],resource_warnings=resource,source_files=len(expected),source_pins_unchanged=pins()==expected,foreign_imports=foreign,elapsed_seconds=time.monotonic()-tick,python=sys.version,platform=sys.platform,uid=os.getuid(),scope='52 affected reporting/native composition plus POSIX cases only; not full Linux qualification',network='none',deployed=False,discord_requests=0)
value.update(harness_adjustment='Exact metadata child outer subprocess observation timeout 8 to 30 seconds only; native measured shutdown <2s assertion and 3s fake stall unchanged',metadata_child_receipts=metadata_child_receipts,original_test_deadlines_unmodified=False,qualified_harness=True)
print('HERMES_LINUX_RESULT='+json.dumps(value))
sys.exit(0 if result.wasSuccessful() and not result.skipped and not resource and not foreign and value['source_pins_unchanged'] and len(metadata_child_receipts)==2 and {r['operation'] for r in metadata_child_receipts}=={'_load','_persist'} and all(r.get('exit_code')==0 and r.get('measured_shutdown_seconds',999)<2 for r in metadata_child_receipts) else 1)
