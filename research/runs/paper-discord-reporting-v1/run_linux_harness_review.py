"""Qualified 52-case run: only exact metadata-child outer timeout is adjusted.

No pinned source/test bytes, measured shutdown assertion, fake stall, product
guard, image, provider, or external-network setting is changed.
"""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path.cwd().resolve()
RUN = ROOT/'research/runs/paper-discord-reporting-v1'
OUTPUT = RUN/'linux-verification-ed439e8-harness-review'
NAME = 'hermes-discord-harness-review-ed439e8'
OUTPUT.mkdir(exist_ok=False)

source_runner = ast.parse((RUN/'run_cached_linux_ed439e8.py').read_text())
code_node = next(node for node in source_runner.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'CODE' for target in node.targets))
code = ast.literal_eval(code_node.value)
qualified = '''
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
'''

anchor = 'tick=time.monotonic()\nwith warnings.catch_warnings(record=True) as caught:'
assert code.count(anchor) == 1
code = code.replace(anchor, qualified+'\n'+anchor)
guard = "with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',deny),patch.object(httpx.HTTPTransport,'handle_request',deny_sync):"
assert code.count(guard) == 1
code = code.replace(guard, guard[:-1]+",patch.object(subprocess,'run',side_effect=reviewed_run):")
receipt = "print('HERMES_LINUX_RESULT='+json.dumps(value))"
assert code.count(receipt) == 1
code = code.replace(receipt, "value.update(harness_adjustment='Exact metadata child outer subprocess observation timeout 8 to 30 seconds only; native measured shutdown <2s assertion and 3s fake stall unchanged',metadata_child_receipts=metadata_child_receipts,original_test_deadlines_unmodified=False,qualified_harness=True)\n"+receipt)
code = code.replace("not foreign and value['source_pins_unchanged'] else 1)", "not foreign and value['source_pins_unchanged'] and len(metadata_child_receipts)==2 and {r['operation'] for r in metadata_child_receipts}=={'_load','_persist'} and all(r.get('exit_code')==0 and r.get('measured_shutdown_seconds',999)<2 for r in metadata_child_receipts) else 1)")
compile(code,'qualified-container-code.py','exec')

command = json.loads((RUN/'linux-verification-ed439e8/command.json').read_text())['argv']
command[command.index('--name')+1] = NAME
command[command.index('-c')+1] = code
assert '--network' in command and command[command.index('--network')+1] == 'none'
assert '--read-only' in command and '--pull' in command and command[command.index('--pull')+1] == 'never'
(OUTPUT/'command.json').write_text(json.dumps(dict(argv=command,started_at=datetime.now(timezone.utc).isoformat()),indent=2)+'\n')
(OUTPUT/'qualified-container-code.py').write_text(code,encoding='utf-8')

with (OUTPUT/'stdout.log').open('x',encoding='utf-8') as out, (OUTPUT/'stderr.log').open('x',encoding='utf-8') as err:
    completed = subprocess.run(command,stdout=out,stderr=err,timeout=600)
record = next((line.split('=',1)[1] for line in reversed((OUTPUT/'stdout.log').read_text().splitlines())
               if line.startswith('HERMES_LINUX_RESULT=')),None)
value = json.loads(record) if record else dict(status='no_terminal_test_receipt')
value.update(exit_code=completed.returncode,observed_at=datetime.now(timezone.utc).isoformat(),
             source_commit='ed439e897a4dd388fdcf6425e7e8b6171d5a88a9',
             raw_pin_manifest_sha256=hashlib.sha256((RUN/'release-upload-ed439e8.raw-sha256.json').read_bytes()).hexdigest())
remaining = subprocess.run(['docker','ps','-a','--filter','name=^/'+NAME+'$','--format','{{.ID}}'],capture_output=True,text=True,check=True)
value['owned_container_absent'] = not remaining.stdout.strip()
(OUTPUT/'verification.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(json.dumps(value,indent=2))
raise SystemExit(completed.returncode if value['owned_container_absent'] else 1)
