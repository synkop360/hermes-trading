"""A retry may recover BUSY, but must recollect and validate the whole snapshot."""
from contextlib import contextmanager
from datetime import timedelta
import io
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_global_reflection as fixtures
import test_supervisor_sync as snapshot_fixtures
from hermes_trading import supervisor_sync as sync

NOW = fixtures.NOW


class Clock:
    def __init__(self, on_wait=None):
        self.value = 0.0
        self.waits = []
        self.on_wait = on_wait

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.value += seconds
        if self.on_wait is not None:
            self.on_wait()


class SnapshotRetryTests(unittest.TestCase):
    setUp = fixtures.GlobalReviewTests.setUp
    write_trades = fixtures.GlobalReviewTests.write_trades
    heartbeat = snapshot_fixtures.SnapshotTests.heartbeat

    def tearDown(self):
        # Windows requires closing the test's writer before removing its directory.
        self.doCleanups()
        fixtures.GlobalReviewTests.tearDown(self)

    def retry(self):
        wrapper = getattr(sync, 'make_snapshot_with_retry', None)
        self.assertTrue(callable(wrapper), 'CLI snapshot collection has no bounded BUSY retry')
        return wrapper(self.state, now=NOW)

    @contextmanager
    def clocked(self, clock):
        with patch.object(sync, 'monotonic', clock, create=True), \
                patch.object(sync, 'sleep', clock.sleep, create=True):
            yield

    def lock(self):
        db = sqlite3.connect(self.state/'paper_publications.sqlite')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE paired_batches (batch_id TEXT PRIMARY KEY, status TEXT)')
        db.commit()
        db.execute('BEGIN EXCLUSIVE')
        return db

    def test_real_busy_then_release_returns_a_fully_validated_snapshot(self):
        self.heartbeat()
        db = self.lock()
        clock = Clock(db.rollback)
        with self.clocked(clock):
            result = self.retry()
        policy = json.loads((self.state/'reflection_owner.json').read_text())
        files = sync.validate_snapshot(result, policy, now=NOW)
        self.assertEqual(files['strategy.yaml'], (self.state/'strategy.yaml').read_bytes())
        self.assertEqual(clock.waits, [0.2])

    def test_real_permanent_busy_preserves_final_sqlite_cause_and_three_attempt_bound(self):
        self.heartbeat()
        self.lock()
        clock = Clock()
        with self.clocked(clock), self.assertRaises(ValueError) as caught:
            self.retry()
        self.assertIsInstance(caught.exception.__cause__, sqlite3.OperationalError)
        self.assertEqual(caught.exception.__cause__.sqlite_errorcode, 5)
        self.assertEqual(clock.waits, [0.2, 0.5])

    def test_retry_reads_new_files_and_runs_both_publication_gates(self):
        self.heartbeat()
        import hermes_trading.paper_publication as publication
        gate = publication.assert_no_pending_paired_batch
        checks = []
        def observed_gate(state):
            checks.append(str(state))
            return gate(state)
        db = self.lock()
        def release_and_replace():
            db.rollback()
            (self.state/'trades.jsonl').write_bytes(b'{"position_id":"new-generation"}\n')
        with self.clocked(Clock(release_and_replace)), \
                patch.object(publication, 'assert_no_pending_paired_batch', observed_gate):
            result = self.retry()
        files = sync.validate_snapshot(result, json.loads((self.state/'reflection_owner.json').read_text()), now=NOW)
        self.assertEqual(files['trades.jsonl'], b'{"position_id":"new-generation"}\n')
        self.assertEqual(len(checks), 3)  # failed first gate, then both gates of a fresh collection

    def test_busy_at_final_gate_discards_already_read_files(self):
        self.heartbeat()
        import hermes_trading.paper_publication as publication
        db = self.lock()
        db.rollback()
        gate = publication.assert_no_pending_paired_batch
        checks = []
        def lock_at_final_gate(state):
            checks.append(str(state))
            if len(checks)==2:db.execute('BEGIN EXCLUSIVE')
            return gate(state)
        def release_and_replace():
            db.rollback()
            (self.state/'trades.jsonl').write_bytes(b'{"position_id":"after-final-gate"}\n')
        with self.clocked(Clock(release_and_replace)), \
                patch.object(publication,'assert_no_pending_paired_batch',lock_at_final_gate):
            result = self.retry()
        import base64
        self.assertEqual(base64.b64decode(result['files']['trades.jsonl']), b'{"position_id":"after-final-gate"}\n')
        self.assertEqual(len(checks), 4)

    def test_later_unhealthy_report_or_release_cannot_be_hidden_by_first_busy(self):
        original = self.heartbeat()
        db = self.lock()
        for case in ('health', 'source', 'report', 'json', 'pending', 'risk'):
            db.rollback()
            db.execute('DELETE FROM paired_batches')
            db.execute('DROP TABLE IF EXISTS paired_risk_state')
            db.commit()
            (self.state/'heartbeat.json').write_text(json.dumps(original))
            (self.state/'wallet_metrics.json').unlink(missing_ok=True)
            db.execute('BEGIN EXCLUSIVE')
            def corrupt():
                db.rollback()
                heartbeat = json.loads(json.dumps(original))
                if case == 'health': heartbeat['status'] = 'degraded'
                elif case == 'source': heartbeat['worker_release']['hashes']['positions.py'] = '0'*64
                elif case == 'report':
                    report = {'status':'observed', 'experiment_id':json.loads((self.state/'reflection_owner.json').read_text())['experiment_id'],
                              'observed_at':NOW.isoformat()}
                    heartbeat['wallet_report_hash'] = '0'*64
                    (self.state/'wallet_metrics.json').write_text(json.dumps(report))
                elif case == 'json':
                    (self.state/'heartbeat.json').write_text('{')
                    return
                elif case == 'pending':
                    db.execute("INSERT INTO paired_batches VALUES ('unfinished','pending')")
                    db.commit()
                elif case == 'risk':
                    db.execute('CREATE TABLE paired_risk_state (id INTEGER PRIMARY KEY,state TEXT)')
                    db.execute('INSERT INTO paired_risk_state VALUES (1,?)', (json.dumps({'global_halted':True}),))
                    db.commit()
                (self.state/'heartbeat.json').write_text(json.dumps(heartbeat))
            clock = Clock(corrupt)
            with self.subTest(case=case), self.clocked(clock), self.assertRaises(ValueError):
                self.retry()
            self.assertEqual(clock.waits, [0.2])

    def test_nonbusy_invalid_state_fails_without_waiting(self):
        good = self.heartbeat()
        for stamp in ((NOW-timedelta(hours=1)).isoformat(), (NOW+timedelta(hours=1)).isoformat()):
            (self.state/'heartbeat.json').write_text(json.dumps(dict(good,timestamp=stamp)))
            clock = Clock()
            with self.subTest(stamp=stamp), self.clocked(clock), self.assertRaises(ValueError):
                self.retry()
            self.assertEqual(clock.waits, [])

    def test_success_does_not_change_existing_supervisor_error_latch(self):
        self.heartbeat()
        from hermes_trading.paper_safety import record_supervisor_result
        config = {'schema_version':1, 'experiment_id':'test', 'volume_root':str(self.state.resolve()),
                  'capacity_bytes':500000000, 'alert_fraction':'0.70', 'max_worker_errors':3,
                  'max_supervisor_errors':3, 'supervisor_stale_seconds':300,
                  'diagnostics_max_bytes':1048576, 'diagnostics_generations':3}
        (self.state/'paper_safety.json').write_text(json.dumps(config))
        for index in range(3):
            record_supervisor_result(self.state, success=False, error_kind='supervisor_exception',
                                     event_id=f'failure-{index}', now=NOW+timedelta(seconds=index))
        path = self.state/'paper_safety_state.json'
        before = path.read_bytes()
        self.assertTrue(json.loads(before)['supervisor']['entry_halt'])
        db = self.lock()
        with self.clocked(Clock(db.rollback)):
            self.retry()
        self.assertEqual(path.read_bytes(), before)

    def test_cli_uses_retry_and_preserves_snapshot_wire_schema(self):
        self.heartbeat()
        db = self.lock()
        output = io.StringIO()
        with self.clocked(Clock(db.rollback)), patch.object(sync, '_now', return_value=NOW), \
                patch.object(sys, 'argv', ['supervisor_sync','--state-dir',str(self.state)]), \
                patch('sys.stdout', output):
            sync.main()
        value = json.loads(output.getvalue())
        self.assertEqual(set(value), {'schema_version','files','disk_release','worker_process_live'})
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(set(value['files']), set(sync.FILES))

    def test_cli_permanent_busy_exits_nonzero_without_a_snapshot(self):
        self.heartbeat()
        self.lock()
        result = subprocess.run([sys.executable,'-B','-X','utf8','-m','hermes_trading.supervisor_sync',
                                 '--state-dir',str(self.state)], stdin=subprocess.DEVNULL,
                                capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('paired batch/publication lease is busy', result.stderr)


class SelectedAccountRetryTests(unittest.TestCase):
    def test_account_changed_after_busy_is_rejected_on_fresh_collection(self):
        import test_paired_primary_supervision as selected
        f = selected.SelectedSnapshotTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        now, primary, policy, account = f.prepare(200)
        db = sqlite3.connect(f.f.state/'paper_publications.sqlite')
        self.addCleanup(db.close)
        db.execute('BEGIN EXCLUSIVE')
        def release_with_foreign_account():
            db.rollback()
            account['starting_balance'] = '100'
            (f.f.state/'wallet_account.json').write_text(json.dumps(account))
        clock = Clock(release_with_foreign_account)
        wrapper = getattr(sync,'make_snapshot_with_retry',None)
        self.assertTrue(callable(wrapper), 'CLI snapshot collection has no bounded BUSY retry')
        with patch.object(sync,'monotonic',clock,create=True), patch.object(sync,'sleep',clock.sleep,create=True), \
                self.assertRaises(ValueError):
            wrapper(f.f.state, now=now)
        self.assertEqual(clock.waits,[0.2])


class RetryBoundariesTests(unittest.TestCase):
    def retry(self):
        wrapper = getattr(sync, 'make_snapshot_with_retry', None)
        self.assertTrue(callable(wrapper), 'CLI snapshot collection has no bounded BUSY retry')
        return wrapper('unused-synthetic-path')

    def error(self, code=5):
        error = sqlite3.OperationalError('synthetic diagnostic text')
        if code is not None: error.sqlite_errorcode = code
        return error

    def run_failure(self, error, clock=None):
        clock = clock or Clock()
        attempts = []
        def fail(*args, **kwargs):
            attempts.append(True)
            raise error
        with patch.object(sync,'make_snapshot',fail), patch.object(sync,'monotonic',clock,create=True), \
                patch.object(sync,'sleep',clock.sleep,create=True), self.assertRaises(type(error)) as caught:
            self.retry()
        self.assertIs(caught.exception,error)
        return len(attempts), clock.waits

    def test_only_numeric_busy_direct_or_explicit_cause_retries(self):
        for code in (5, 261, 517):
            for wrapped in (False, True):
                error = self.error(code)
                if wrapped:
                    outer = ValueError('publication rejected')
                    outer.__cause__ = error
                    error = outer
                with self.subTest(code=code,wrapped=wrapped):
                    self.assertEqual(self.run_failure(error), (3,[0.2,0.5]))

    def test_other_codes_messages_context_and_cause_cycles_never_create_retry(self):
        errors = [self.error(code) for code in (None, 6, 10, 11, 14, '5', True)]
        errors.append(ValueError('SQLITE_BUSY database is locked'))
        implicit = ValueError('unrelated validation failure')
        implicit.__context__ = self.error()
        errors.append(implicit)
        cyclic = ValueError('cyclic explicit causes')
        other = RuntimeError('cycle')
        cyclic.__cause__ = other
        other.__cause__ = cyclic
        errors.append(cyclic)
        for error in errors:
            with self.subTest(kind=type(error).__name__,code=getattr(error,'sqlite_errorcode',None)):
                self.assertEqual(self.run_failure(error), (1,[]))

    def test_slow_failed_attempt_cannot_admit_a_second_attempt(self):
        error = self.error()
        clock = Clock()
        attempts = []
        def fail(*args, **kwargs):
            attempts.append(True)
            clock.value = 2.0
            raise error
        with patch.object(sync,'make_snapshot',fail), patch.object(sync,'monotonic',clock,create=True), \
                patch.object(sync,'sleep',clock.sleep,create=True), self.assertRaises(sqlite3.OperationalError):
            self.retry()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(clock.waits, [])

    def test_wait_that_reaches_deadline_does_not_admit_another_attempt(self):
        clock = Clock(lambda: setattr(clock, 'value', 2.0))
        self.assertEqual(self.run_failure(self.error(),clock), (1,[0.2]))

    def test_wait_is_capped_at_remaining_admission_budget(self):
        clock = Clock()
        error = self.error()
        attempts = []
        def fail(*args, **kwargs):
            attempts.append(True)
            clock.value = 1.9
            raise error
        with patch.object(sync,'make_snapshot',fail), patch.object(sync,'monotonic',clock,create=True), \
                patch.object(sync,'sleep',clock.sleep,create=True), self.assertRaises(sqlite3.OperationalError):
            self.retry()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(clock.waits), 1)
        self.assertAlmostEqual(clock.waits[0], 0.1)


if __name__ == '__main__':
    unittest.main()
