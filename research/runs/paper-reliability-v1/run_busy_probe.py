from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
RUN=Path(__file__).resolve().parent
cli='C:/Users/thoma/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe'
args=[cli,'ssh','--project','f78f42d9-20ec-44f6-ae3f-f8bb79ee7000','--service','8b94cbc9-293e-4222-a8a7-698b656985f7',
      '--environment','fdcc71a3-00a9-410c-8d6f-59af9bc98d36','--deployment-instance','b295439f-053d-4911-8b55-5db4e01f00cc',
      '--','timeout 220 /app/.venv/bin/python -B -E -']
at=datetime.now(timezone.utc)
result=subprocess.run(args,input=(RUN/'probe_busy_onset.py').read_text(),capture_output=True,text=True,encoding='utf-8',timeout=235)
try:data=json.loads(result.stdout)
except ValueError:data=dict(status='non_json',stdout_bytes=len(result.stdout))
receipt=dict(started_at=at.isoformat(),finished_at=datetime.now(timezone.utc).isoformat(),exit_code=result.returncode,stderr_bytes=len(result.stderr),result=data)
path=RUN/(at.strftime('%Y%m%dT%H%M%SZ')+'-busy-onset.json')
with path.open('x') as f:json.dump(receipt,f,indent=2)
rows=data.get('observations',[])
print(json.dumps(dict(receipt=str(path),exit_code=result.returncode,observations=len(rows),results=rows)))
