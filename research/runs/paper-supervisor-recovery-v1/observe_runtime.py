"""Read-only native runtime observation. Never invokes a watcher, model or reset."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
source_path = HERE.parent / 'paper-supervisor-retry-v1/live-cutover/observe_successor2.py'
source = source_path.read_text(encoding='utf-8')
ending = 'print(json.dumps(result,sort_keys=True,allow_nan=False))'
assert source.count(ending) == 1
source = source.replace(ending, "import os\nresult['deployment_id'] = os.environ.get('RAILWAY_DEPLOYMENT_ID')\n" + ending)
args = ['C:/Users/thoma/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe', 'ssh',
        '--project', 'f78f42d9-20ec-44f6-ae3f-f8bb79ee7000',
        '--service', '8b94cbc9-293e-4222-a8a7-698b656985f7',
        '--environment', 'fdcc71a3-00a9-410c-8d6f-59af9bc98d36',
        '--', 'timeout 38 /app/.venv/bin/python -B -E -']
at = datetime.now(timezone.utc)
receipt = HERE / (at.strftime('%Y%m%dT%H%M%SZ') + '-runtime.json')
try:
    process = subprocess.run(args, input=source, capture_output=True, text=True, encoding='utf-8', timeout=48)
    try:
        result = json.loads(process.stdout)
    except ValueError:
        result = {'status': 'non_json_output', 'stdout_bytes': len(process.stdout)}
    data = {'observed_at': at.isoformat(), 'exit_code': process.returncode,
            'stderr_bytes': len(process.stderr), 'result': result}
except subprocess.TimeoutExpired:
    data = {'observed_at': at.isoformat(), 'status': 'read_only_transport_timeout'}
with receipt.open('x', encoding='utf-8') as stream:
    json.dump(data, stream, indent=2)
print(json.dumps({'receipt': str(receipt), **{k: v for k, v in data.items() if k != 'result'},
                  'observation': {k: data.get('result', {}).get(k) for k in
                    ('deployment_id', 'runtime', 'heartbeat', 'cohort', 'trading')}}))
