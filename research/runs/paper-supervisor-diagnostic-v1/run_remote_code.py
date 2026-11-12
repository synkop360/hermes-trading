"""Finite operator transport for this approved exact deployment; stdin is EOF."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess,sys

HERE=Path(__file__).resolve().parent
RAILWAY='C:/Users/thoma/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe'
INSTANCE=sys.argv[2] if len(sys.argv)>2 else 'b45705e2-e89a-49fe-89c5-f2f81bbac862'
source=Path(sys.argv[1]).resolve()
assert source.is_relative_to(HERE) and source.suffix=='.py'
receipt=HERE/(source.stem+'.native-result.json')
assert not receipt.exists(),'preserve prior execution receipt; reconcile before another action'
args=[RAILWAY,'ssh','--project','f78f42d9-20ec-44f6-ae3f-f8bb79ee7000',
      '--service','8b94cbc9-293e-4222-a8a7-698b656985f7',
      '--environment','fdcc71a3-00a9-410c-8d6f-59af9bc98d36',
      '--deployment-instance',INSTANCE,'--','timeout 38 /app/.venv/bin/python -E -']
try:
    result=subprocess.run(args,input=source.read_text(encoding='utf-8'),capture_output=True,
                          text=True,encoding='utf-8',timeout=48)
    value=dict(observed_at=datetime.now(timezone.utc).isoformat(),instance=INSTANCE,
               native_exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
except subprocess.TimeoutExpired:
    value=dict(observed_at=datetime.now(timezone.utc).isoformat(),instance=INSTANCE,
               status='transport_outcome_uncertain_reconcile_actual_state_before_retry')
receipt.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps(value if len(value.get('stdout',''))<4000 else
                 {k:v for k,v in value.items() if k!='stdout'}|{'stdout_bytes':len(value['stdout']),'full_result':str(receipt)},indent=2))
raise SystemExit(value.get('native_exit_code',2))
