"""Observe exact native child import/body timing; no source edits or network."""
from pathlib import Path
import json,subprocess

ROOT=Path.cwd().resolve();RUN=ROOT/'research/runs/paper-discord-reporting-v1'
OUT=RUN/'linux-child-diagnostic';OUT.mkdir(exist_ok=False)
args=json.loads((RUN/'linux-metadata-recheck-ed439e8/command.json').read_text())['argv']
args[args.index('--name')+1]='hermes-discord-child-diagnostic-ed439e8'
args[-2]='''import ast,json,subprocess,sys,textwrap,time
from pathlib import Path
tree=ast.parse(Path('tests/test_paper_notifications.py').read_text())
method=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='test_stalled_metadata_never_keeps_asyncio_run_alive_after_cancellation')
assignment=next(node for node in method.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='script' for t in node.targets))
script=textwrap.dedent(ast.literal_eval(assignment.value.args[0]))
script='import faulthandler;faulthandler.dump_traceback_later(5,repeat=True)\\n'+script
for operation in ('_load','_persist'):
 tick=time.monotonic()
 try:
  result=subprocess.run([sys.executable,'-B','-X','importtime','-c',script,operation],capture_output=True,text=True,timeout=25)
  print(json.dumps(dict(operation=operation,total_seconds=time.monotonic()-tick,exit_code=result.returncode,stdout=result.stdout,stderr_tail=result.stderr[-16000:])),flush=True)
 except subprocess.TimeoutExpired as error:
  print(json.dumps(dict(operation=operation,total_seconds=time.monotonic()-tick,timed_out=True,stdout=(error.stdout or b'').decode(),stderr_tail=(error.stderr or b'')[-16000:].decode())),flush=True)
'''.replace("\\n'+script","\n'+script") if False else '''import ast,json,subprocess,sys,textwrap,time
from pathlib import Path
tree=ast.parse(Path('tests/test_paper_notifications.py').read_text())
method=next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='test_stalled_metadata_never_keeps_asyncio_run_alive_after_cancellation')
assignment=next(node for node in method.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='script' for t in node.targets))
script=textwrap.dedent(ast.literal_eval(assignment.value.args[0]))
script='import faulthandler;faulthandler.dump_traceback_later(5,repeat=True)'+chr(10)+script
for operation in ('_load','_persist'):
 tick=time.monotonic()
 try:
  result=subprocess.run([sys.executable,'-B','-X','importtime','-c',script,operation],capture_output=True,text=True,timeout=25)
  print(json.dumps(dict(operation=operation,total_seconds=time.monotonic()-tick,exit_code=result.returncode,stdout=result.stdout,stderr_tail=result.stderr[-16000:])),flush=True)
 except subprocess.TimeoutExpired as error:
  print(json.dumps(dict(operation=operation,total_seconds=time.monotonic()-tick,timed_out=True,stdout=(error.stdout or b'').decode(),stderr_tail=(error.stderr or b'')[-16000:].decode())),flush=True)
'''
(OUT/'command.json').write_text(json.dumps(dict(argv=args),indent=2)+'\n')
with (OUT/'stdout.log').open('x',encoding='utf-8') as out,(OUT/'stderr.log').open('x',encoding='utf-8') as err:
    result=subprocess.run(args,stdout=out,stderr=err,timeout=80)
print('diagnostic exit',result.returncode)
raise SystemExit(result.returncode)
