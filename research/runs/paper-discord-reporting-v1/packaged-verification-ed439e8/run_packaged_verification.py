"""Source-frozen synthetic correctness checks of one exact raw export."""
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import unittest
import warnings

import sitecustomize as guard

OUTPUT = Path(__file__).resolve().parent
EXPORT = Path(os.environ['HERMES_PACKAGED_VERIFY_EXPORT']).resolve()
RUN = OUTPUT.parent
EXPECTED_COMMIT = 'ed439e897a4dd388fdcf6425e7e8b6171d5a88a9'
GROUPS = [
    'test_notifications', 'test_paper_notifications',
    'test_paper_notification_capture', 'test_paper_discord_transition',
    'test_paired_worker', 'test_paper_boot', 'test_paired_paper',
    'test_paired_risk', 'test_archive_protective_worker',
    'test_paper_release_transition',
]
EXPECTED_DELTA = ['notifications.py', 'paired_paper.py', 'paired_worker.py', 'paper_boot.py']
sys.path[:] = [str(EXPORT / 'tests'), str(EXPORT)] + [
    entry for entry in sys.path if entry not in (str(EXPORT / 'tests'), str(EXPORT))
]

class Tee:
    def __init__(self, original, log):
        self.original, self.log = original, log
    def write(self, value):
        self.original.write(value)
        self.log.write(value)
        self.flush()
    def flush(self):
        self.original.flush()
        self.log.flush()

def disk(path):
    return Path('\\\\?\\' + str(path)) if os.name == 'nt' else path

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')

def verify_export(label):
    pins_path = RUN / 'release-upload-ed439e8.raw-sha256.json'
    pins = json.loads(pins_path.read_text(encoding='utf-8'))
    found = set()
    for root, _, filenames in os.walk(str(disk(EXPORT))):
        for name in filenames:
            found.add(os.path.relpath(os.path.join(root, name), str(disk(EXPORT))).replace('\\', '/'))
    mismatches = [name for name, expected in pins.items()
        if sha(disk(EXPORT / name).read_bytes()) != expected]
    value = dict(phase=label, observed_at=datetime.now(timezone.utc).isoformat(),
        manifest_sha256=sha(pins_path.read_bytes()), expected_files=len(pins),
        observed_files=len(found), mismatches=mismatches,
        unexpected_files=sorted(found - pins.keys()), missing_files=sorted(pins.keys() - found))
    save('raw-pins-' + label + '.json', value)
    assert len(pins) == 603, value
    assert not mismatches and found == pins.keys(), value
    return value, pins

def verify_release(pins):
    plan_raw = (RUN / 'proposed-release-plan.json').read_bytes()
    plan = json.loads(plan_raw)
    original = json.loads((RUN / 'export-verification.json').read_text(encoding='utf-8'))
    runtime = {name.removeprefix('hermes_trading/'): digest for name, digest in pins.items()
        if name.startswith('hermes_trading/') and name.endswith('.py')}
    assert len(runtime) == 129
    assert set(runtime) == set(plan['old']['runtime']) == set(plan['new']['runtime'])
    assert runtime == plan['new']['runtime']
    delta = sorted(name for name, digest in runtime.items() if digest != plan['old']['runtime'][name])
    assert delta == EXPECTED_DELTA == original['runtime_delta']
    assert plan['new_commit'] == original['source_commit'] == EXPECTED_COMMIT
    assert plan['old_commit'] == original['previous_commit'] == '49c851315b98bb33b1eb78180fb789afe2a49f3d'
    assert plan['old']['watcher'] == plan['new']['watcher'] == pins['scripts/hermes_watch.py']
    assert plan['old']['native_launcher'] == plan['new']['native_launcher'] == pins['scripts/native_hermes_watch.py']
    from hermes_trading.reflection_supervision import _hash
    from scripts.paper_release_transition import validate_release_plan
    validate_release_plan(plan)
    for key, measured in (
        ('old_runtime_map', _hash(plan['old']['runtime'])),
        ('new_runtime_map', _hash(runtime)),
        ('old_supervisor_release', _hash(plan['old'])),
        ('new_supervisor_release', _hash(plan['new'])),
        ('release_plan_sha256', sha(plan_raw)),
    ):
        assert original[key] == measured, key
    value = dict(source_commit=EXPECTED_COMMIT, runtime_modules=len(runtime),
        runtime_keyset_unchanged=True, runtime_delta=delta,
        unchanged_runtime_modules=len(runtime)-len(delta),
        watcher_sha256=plan['new']['watcher'], native_launcher_sha256=plan['new']['native_launcher'],
        watcher_and_native_launcher_unchanged=True,
        old_runtime_map=_hash(plan['old']['runtime']), new_runtime_map=_hash(runtime),
        old_supervisor_release=_hash(plan['old']), new_supervisor_release=_hash(plan['new']),
        release_plan_sha256=sha(plan_raw), release_plan_validated=True)
    save('release-map-verification.json', value)
    return value

class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
    def startTest(self, test):
        self._started_test = time.monotonic()
        self._record = dict(test_id=test.id(), status='running')
        self.records.append(self._record)
        super().startTest(test)
    def stopTest(self, test):
        self._record['elapsed_seconds'] = time.monotonic()-self._started_test
        super().stopTest(test)
    def addSuccess(self, test):
        self._record['status'] = 'passed'
        super().addSuccess(test)
    def addSkip(self, test, reason):
        self._record.update(status='skipped', reason=reason)
        super().addSkip(test, reason)
    def addError(self, test, err):
        self._record.update(status='error', detail=self._exc_info_to_string(err, test))
        super().addError(test, err)
    def addFailure(self, test, err):
        self._record.update(status='failed', detail=self._exc_info_to_string(err, test))
        super().addFailure(test, err)
    def addExpectedFailure(self, test, err):
        self._record.update(status='expected_failure')
        super().addExpectedFailure(test, err)
    def addUnexpectedSuccess(self, test):
        self._record.update(status='unexpected_success')
        super().addUnexpectedSuccess(test)

def run():
    started = time.monotonic()
    initial_at = datetime.now(timezone.utc).isoformat()
    assert Path.cwd().resolve() == EXPORT
    assert sys.path[:2] == [str(EXPORT / 'tests'), str(EXPORT)]
    before, pins = verify_export('before')
    release = verify_release(pins)
    suite = unittest.TestSuite()
    groups = {}
    for name in GROUPS:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(EXPORT)
        group = unittest.defaultTestLoader.loadTestsFromModule(module)
        groups[name] = group.countTestCases()
        suite.addTest(group)
    imports, violations = guard.module_receipt()
    assert not violations, violations
    print(json.dumps(dict(kind='packaged_native_unit_tests', export=str(EXPORT),
        source_commit=EXPECTED_COMMIT, groups=groups, discovered=suite.countTestCases(),
        raw_http_guard='all interpreters via inherited sitecustomize',
        before_raw_pins=before, release_map=release), indent=2), flush=True)
    collected_warnings = []
    original_warning = warnings.showwarning
    def showwarning(message, category, filename, lineno, file=None, line=None):
        collected_warnings.append(dict(category=category.__name__, message=str(message),
            filename=filename, lineno=lineno))
        original_warning(message, category, filename, lineno, file=file, line=line)
    with warnings.catch_warnings():
        warnings.simplefilter('always', ResourceWarning)
        warnings.showwarning = showwarning
        test_started = time.monotonic()
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2,
            resultclass=RecordingResult, warnings=None).run(suite)
        test_elapsed = time.monotonic()-test_started
        gc.collect()
    imports, violations = guard.module_receipt()
    after, after_pins = verify_export('after')
    assert pins == after_pins
    assert not violations, violations
    assert not guard._denials, guard._denials
    assert guard.httpx.HTTPTransport.handle_request is guard.deny_sync
    assert guard.httpx.AsyncHTTPTransport.handle_async_request is guard.deny_async
    children = []
    for path in sorted(OUTPUT.glob('process-*.json')):
        child = json.loads(path.read_text(encoding='utf-8'))
        if child['pid'] == os.getpid():
            continue
        assert not child['import_provenance_violations'], child
        assert not child['raw_http_denials'], child
        assert child['raw_transport_guards_active'], child
        children.append(dict(receipt=path.name, pid=child['pid'], argv=child['argv'],
            elapsed_seconds=child['elapsed_seconds'], imported_module_count=len(child['imported_modules']),
            import_provenance_violations=child['import_provenance_violations'],
            raw_http_denials=child['raw_http_denials'], raw_transport_guards_active=child['raw_transport_guards_active']))
    # These are the unchanged native whole-process metadata and CLI checks.
    child_operations = [child['argv'][1:] for child in children if child['argv'][0] == '-c']
    assert ['_load'] in child_operations and ['_persist'] in child_operations, children
    assert len(children) >= 3, children
    value = dict(kind='source_frozen_packaged_correctness_verification',
        observed_at=initial_at, finished_at=datetime.now(timezone.utc).isoformat(),
        source_commit=EXPECTED_COMMIT, export=str(EXPORT),
        executable=sys.executable, python_version=sys.version,
        cwd=str(Path.cwd()), sys_path_first_two=sys.path[:2],
        groups=groups, tests_run=result.testsRun, passed=sum(r['status']=='passed' for r in result.records),
        failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
        skip_details=[dict(test_id=t.id(), reason=reason) for t,reason in result.skipped],
        expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses),
        test_elapsed_seconds=test_elapsed, total_elapsed_seconds=time.monotonic()-started,
        resource_warning_count=sum(w['category']=='ResourceWarning' for w in collected_warnings),
        warnings=collected_warnings, per_test=result.records,
        imported_modules=imports, import_provenance_violations=violations,
        child_processes=children, raw_http_denials=guard._denials,
        raw_transport_guards_active=True, before_raw_pins=before, after_raw_pins=after,
        release_map=release, deployed=False, restarted=False, live_discord_requests=0,
        live_state_touched=False, synthetic_only=True, result='passed' if result.wasSuccessful() else 'failed')
    save('packaged-verification-results.json', value)
    print('\nRECEIPT ' + json.dumps({k:value[k] for k in (
        'result','tests_run','passed','failures','errors','skips','resource_warning_count',
        'test_elapsed_seconds','total_elapsed_seconds')}, sort_keys=True), flush=True)
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    with (OUTPUT / 'packaged-terminal.txt').open('w', encoding='utf-8') as log:
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)
        try:
            code = run()
        except BaseException:
            traceback.print_exc()
            code = 2
        finally:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    raise SystemExit(code)
