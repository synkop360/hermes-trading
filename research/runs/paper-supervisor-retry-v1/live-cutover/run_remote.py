from pathlib import Path
from datetime import datetime,timezone
import json,subprocess,sys
HERE=Path(__file__).resolve().parent
RAILWAY='C:/Users/thoma/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe'
source=Path(sys.argv[1]).resolve();instance=sys.argv[2]
assert source.is_relative_to(HERE) and source.suffix=='.py'
receipt=source.with_suffix('.native-result.json');assert not receipt.exists()
args=[RAILWAY,'ssh','--project','f78f42d9-20ec-44f6-ae3f-f8bb79ee7000','--service','8b94cbc9-293e-4222-a8a7-698b656985f7','--environment','fdcc71a3-00a9-410c-8d6f-59af9bc98d36','--deployment-instance',instance,'--','timeout 38 /app/.venv/bin/python -B -E -']
try:
 result=subprocess.run(args,input=source.read_text(encoding='utf-8'),capture_output=True,text=True,encoding='utf-8',timeout=48)
 try: output=json.loads(result.stdout)
 except (ValueError,TypeError): output={'status':'non_json_remote_result','stdout_bytes':len(result.stdout)}
 value=dict(observed_at=datetime.now(timezone.utc).isoformat(),instance=instance,native_exit_code=result.returncode,result=output,stderr_bytes=len(result.stderr))
except subprocess.TimeoutExpired:value=dict(observed_at=datetime.now(timezone.utc).isoformat(),instance=instance,status='transport_outcome_uncertain_reconcile_actual_state_before_retry')
with receipt.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2);stream.write('\n')
print(json.dumps({k:v for k,v in value.items() if k!='result'}|{'receipt':str(receipt),'result_keys':list(value.get('result',{}))},indent=2))
raise SystemExit(value.get('native_exit_code',2))
