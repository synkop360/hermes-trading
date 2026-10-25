
"""Read-only reproduction of the failed snapshot command; retains sanitized output only."""
from pathlib import Path
from datetime import datetime,timezone
import base64,importlib.util,json,subprocess,time
HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[3]/'hermes-trading'
spec=importlib.util.spec_from_file_location('safe_operator_observer',HERE/'capture_guarded_invocation.py')
observer=importlib.util.module_from_spec(spec);spec.loader.exec_module(observer)
registry,_=observer.trusted_sources()
aliases={}
for local,entry in registry.items():
 if entry['source'].startswith(('hermes_trading/','scripts/')):
  aliases['/app/'+entry['source']]=str(MAIN/entry['source'])
def remote_safe(text):
 for remote,local in aliases.items():text=text.replace('File "'+remote+'"','File "'+local+'"')
 return observer.sanitized(text)
started=time.monotonic()
command=['railway.cmd','ssh','--','/app/.venv/bin/python -m hermes_trading.supervisor_sync --state-dir /app/state/adaptive_200']
r=subprocess.run(command,cwd=MAIN,text=True,capture_output=True,timeout=30)
classifications=[]
combined=(r.stdout+'\n'+r.stderr).lower()
for phrase,label in [('an error occurred connecting to your service','railway_service_connection_error'),
 ('container is not running','railway_container_not_running'),('you are not logged in','cli_login_required'),
 ('no project linked','cli_project_not_linked'),('permission denied','transport_authentication_denied')]:
 if phrase in combined:classifications.append(label)
result=dict(observed_at=datetime.now(timezone.utc).isoformat(),kind='read_only_native_snapshot_subcommand',
 exit_code=r.returncode,elapsed_seconds=time.monotonic()-started,stderr=remote_safe(r.stderr),
 stdout_bytes=len(r.stdout.encode('utf-8')),transport_classifications=classifications,
 original_native_command_unchanged=True,raw_output_retained=False,
 limitations=['Standalone operator read-only command, not a supervisor cycle or full cron environment reproduction.'])
if r.returncode==0:
 try:
  payload=json.loads(r.stdout);heartbeat=json.loads(base64.b64decode(payload['files']['heartbeat.json']))
  result['snapshot']=dict(schema_version=payload['schema_version'],files=len(payload['files']),
                          heartbeat_status=heartbeat['status'],timestamp=heartbeat['timestamp'])
 except Exception as error:result['parse_error_type']=type(error).__name__
target=HERE/'snapshot-readonly-probe2.json'
with target.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
print(json.dumps(result))
