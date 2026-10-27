"""Bounded read-only scheduler/status facts, excluding errors and command bodies."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2].parent / 'hermes-trading'
HOME = Path('C:/Users/thoma/AppData/Local/hermes')
JOB = '2f6c5088c6ff'

def utc(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc) if text else None

with sqlite3.connect((HOME / 'cron/executions.db').as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
    db.execute('PRAGMA query_only=ON')
    rows = [dict(zip(('status', 'started_at', 'finished_at'), row)) for row in db.execute(
        'SELECT status,started_at,finished_at FROM executions WHERE job_id=? ORDER BY rowid DESC LIMIT 5000', (JOB,))]
rows = sorted((r for r in rows if r['started_at']), key=lambda r: utc(r['started_at']))
groups = {}
for name, cutoff in [('post_cutover', '2026-10-26T17:11:30+00:00'), ('post_redeploy', '2026-10-26T20:57:19+00:00')]:
    selected = [r for r in rows if utc(r['started_at']) >= utc(cutoff)]
    streak = maximum = 0
    for row in selected:
        streak = streak + 1 if row['status'] == 'failed' else 0
        maximum = max(maximum, streak)
    groups[name] = {'status_counts': dict(Counter(r['status'] for r in selected)),
                    'max_failed_streak': maximum, 'last_failures': [r for r in selected if r['status'] == 'failed'][-8:]}
jobs = json.loads((HOME / 'cron/jobs.json').read_text(encoding='utf-8'))['jobs']
job = next(j for j in jobs if j['id'] == JOB)
status = ROOT / 'state/paired-supervisor/paper-beta-v1-20261021-6c9d520/adaptive_200'
at = datetime.now(timezone.utc)
result = {'observed_at': at.isoformat(), 'job': {k: job.get(k) for k in
          ('id', 'enabled', 'state', 'last_run_at', 'last_status', 'next_run_at')},
          'periods': groups, 'latest_runs': rows[-12:],
          'status': json.loads((status / 'supervisor_status.json').read_text(encoding='utf-8')),
          'lifecycle': json.loads((status / 'supervisor_lifecycle.json').read_text(encoding='utf-8'))}
path = HERE / (at.strftime('%Y%m%dT%H%M%SZ') + '-pc.json')
with path.open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
print(json.dumps(result))
