"""Local synthetic verification; run from this worktree using its existing venv."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
FILES = ('hermes_trading/notifications.py', 'tests/test_paper_notifications.py',
         'tests/test_paper_notification_capture.py')
SUITES = {
    'focused': ['-m', 'unittest', 'tests.test_paper_notifications', 'tests.test_notifications'],
    'native-capture': ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_paper_notification_capture.py'],
}

def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}

if __name__ == '__main__':
    suite, revision = sys.argv[1:]
    before = hashes()
    stamp = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    args = ['-B', '-X', 'utf8', *SUITES[suite]]
    result = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, timeout=180)
    output = result.stdout.decode('utf-8', errors='replace') + result.stderr.decode('utf-8', errors='replace')
    name = f'{suite}-{revision}'
    (OUT / f'{name}.log').write_text(output, encoding='utf-8')
    receipt = {'started_at_utc': stamp, 'exit_code': result.returncode,
               'elapsed_seconds': round(time.monotonic() - start, 3), 'arguments': args,
               'source_sha256': before, 'source_unchanged': before == hashes(),
               'log': f'{name}.log', 'result_lines': [line for line in output.splitlines()
                   if line.startswith(('Ran ', 'OK', 'FAILED', 'FAIL:', 'ERROR:'))]}
    (OUT / f'{name}.result.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))
    sys.exit(result.returncode or (0 if receipt['source_unchanged'] else 1))
