"""Affected synthetic/native checks only. Never opens installed trading state."""
from pathlib import Path
import hashlib
import json
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
PATTERNS=(
    'test_paper_supervisor_recovery.py','test_paper_supervisor_health.py',
    'test_paper_safety.py','test_paper_safety_worker.py','test_supervisor_snapshot_retry.py',
    'test_supervisor_sync.py','test_paired_worker.py','test_paired_primary_supervision.py',
    'test_paper_boot.py','test_paper_backup.py','test_pull_paper_backup.py',
    'test_paper_publication.py','test_supervisor_launch.py','test_native_supervisor_launcher.py',
    'test_paper_notification_capture.py','test_paper_notifications.py',
)
def pins():
    files=list((ROOT/'hermes_trading').rglob('*.py'))+list((ROOT/'scripts').rglob('*.py'))
    files += [ROOT/'tests'/pattern for pattern in PATTERNS]
    return {path.relative_to(ROOT).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}

if __name__=='__main__':
    before=pins();suite=unittest.TestSuite()
    for pattern in PATTERNS:suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    evidence=dict(scope='affected synthetic/native tests; not full suite or profitability evidence',patterns=PATTERNS,
        run=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=[dict(test=str(test),reason=reason) for test,reason in result.skipped],
        source_unchanged=before==pins(),source_sha256=before,independent_review='unavailable: account usage limit')
    Path(__file__).with_name('local-verification.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() and evidence['source_unchanged'] else 1)
