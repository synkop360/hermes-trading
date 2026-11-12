"""Diagnostic characterization of the native report-before-heartbeat window.

This records the current rejection, not a requirement to retain that race. It
uses invented eight-wallet inputs and the real worker/snapshot paths, with no
network, model invocation, wall-clock sleeps or production change.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from hermes_trading import paired_worker, supervisor_sync
from hermes_trading.paper_publication import assert_no_pending_paired_batch
from hermes_trading.reflection_supervision import _hash
from test_paired_primary_archive import fresh_selected


class WindowClock:
    def __init__(self, on_sleep=None):
        self.value = 0.0
        self.waits = []
        self.on_sleep = on_sleep

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds
        self.waits.append(seconds)
        if self.on_sleep is not None:
            action, self.on_sleep = self.on_sleep, None
            action()


class SnapshotPublicationWindowTests(unittest.IsolatedAsyncioTestCase):
    async def at_second_heartbeat(self, inspect):
        fixture, primary = fresh_selected(self, 200)
        fixture.limits['max_cycles'] = 2
        (fixture.root/'worker_limits.json').write_text(json.dumps(fixture.limits))
        original = paired_worker._write_heartbeat
        seen = []

        def intercept(*args, **kwargs):
            if not seen and args[2]['status'] == 'running' and args[4]['complete'] == 2:
                seen.append(True)
                inspect(fixture, primary, original, args, kwargs)
            return original(*args, **kwargs)

        with patch.object(paired_worker, '_write_heartbeat', side_effect=intercept), \
                patch.object(paired_worker, 'PublicQuoteCollector',
                             side_effect=AssertionError('network collector forbidden in synthetic diagnostic')):
            result = await fixture.invoke()
        self.assertEqual(seen, [True])
        self.assertEqual(result['completed_batches'], 2)
        self.assertEqual(fixture.market.collect.await_count, 2)

    async def test_retry_recollects_after_real_matching_heartbeat_is_published(self):
        def inspect(fixture, primary, publish, args, kwargs):
            at = kwargs['at']
            before = (fixture.primary/'wallet_metrics.json').read_bytes()
            published = []

            def advance_heartbeat():
                publish(*args, **kwargs)
                published.append(True)

            clock = WindowClock(advance_heartbeat)
            with patch.object(supervisor_sync, 'monotonic', clock), \
                    patch.object(supervisor_sync, 'sleep', clock.sleep):
                try:
                    snapshot = supervisor_sync.make_snapshot_with_retry(fixture.primary, now=at)
                except ValueError as error:
                    self.fail('transient native projection gap was not recollected: ' + type(error).__name__)
            account = json.loads((fixture.primary/'wallet_account.json').read_bytes())
            files = supervisor_sync.validate_snapshot(snapshot, fixture.policy, now=at,
                expected_primary=primary, expected_account_hash=_hash(account))
            heartbeat = json.loads(files['heartbeat.json'])
            self.assertEqual(heartbeat['worker_lifecycle']['completed_batches'], 2)
            self.assertEqual(heartbeat['wallet_report_hash'], _hash(json.loads(files['wallet_metrics.json'])))
            self.assertEqual((fixture.primary/'wallet_metrics.json').read_bytes(), before)
            self.assertEqual(published, [True])
            self.assertEqual(clock.waits, [0.2])

        await self.at_second_heartbeat(inspect)

    async def test_persistent_projection_mismatch_exhausts_one_shared_budget_without_repair(self):
        def inspect(fixture, primary, publish, args, kwargs):
            heartbeat = fixture.primary/'heartbeat.json'
            report = fixture.primary/'wallet_metrics.json'
            before = heartbeat.read_bytes(), report.read_bytes()
            clock = WindowClock()
            with patch.object(supervisor_sync, 'monotonic', clock), \
                    patch.object(supervisor_sync, 'sleep', clock.sleep), \
                    patch.object(supervisor_sync, 'make_snapshot', wraps=supervisor_sync.make_snapshot) as collect:
                with self.assertRaises(supervisor_sync.SnapshotReportHeartbeatMismatch) as caught:
                    supervisor_sync.make_snapshot_with_retry(fixture.primary, now=kwargs['at'])
            self.assertFalse(supervisor_sync._snapshot_busy(caught.exception))
            self.assertEqual(collect.call_count, 13)
            self.assertEqual(clock.waits, [0.2, 0.5, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2])
            self.assertAlmostEqual(clock.value, 19.7)
            self.assertEqual((heartbeat.read_bytes(), report.read_bytes()), before)
            assert_no_pending_paired_batch(fixture.primary)

        await self.at_second_heartbeat(inspect)

    async def test_invalid_report_status_experiment_or_json_is_not_retried(self):
        def inspect(fixture, primary, publish, args, kwargs):
            report_path = fixture.primary/'wallet_metrics.json'
            original = report_path.read_bytes()
            report = json.loads(original)
            cases = [('status', json.dumps(dict(report, status='unavailable')).encode()),
                     ('experiment', json.dumps(dict(report, experiment_id='foreign-experiment')).encode()),
                     ('json', b'{')]
            try:
                for label, invalid in cases:
                    with self.subTest(case=label):
                        report_path.write_bytes(invalid)
                        clock = WindowClock()
                        with patch.object(supervisor_sync, 'monotonic', clock), \
                                patch.object(supervisor_sync, 'sleep', clock.sleep), \
                                patch.object(supervisor_sync, 'make_snapshot', wraps=supervisor_sync.make_snapshot) as collect:
                            with self.assertRaises(ValueError) as caught:
                                supervisor_sync.make_snapshot_with_retry(fixture.primary, now=kwargs['at'])
                        self.assertNotIsInstance(caught.exception, supervisor_sync.SnapshotReportHeartbeatMismatch)
                        if label == 'json':
                            self.assertIsInstance(caught.exception, json.JSONDecodeError)
                        else:
                            self.assertEqual(str(caught.exception), 'wallet report is not a current experiment observation')
                        self.assertEqual(collect.call_count, 1)
                        self.assertEqual(clock.waits, [])
                        self.assertEqual(report_path.read_bytes(), invalid)
            finally:
                report_path.write_bytes(original)

        await self.at_second_heartbeat(inspect)

    def test_plain_value_error_with_same_message_cannot_claim_projection_retry(self):
        failure = ValueError('wallet report does not match the worker heartbeat')
        clock = WindowClock()
        with patch.object(supervisor_sync, 'make_snapshot', side_effect=failure) as collect, \
                patch.object(supervisor_sync, 'monotonic', clock), \
                patch.object(supervisor_sync, 'sleep', clock.sleep):
            with self.assertRaises(ValueError) as caught:
                supervisor_sync.make_snapshot_with_retry(Path('unused-synthetic-path'))
        self.assertIs(caught.exception, failure)
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(clock.waits, [])

    async def test_native_completed_report_rejects_until_matching_heartbeat_is_published(self):
        fixture, primary = fresh_selected(self, 200)
        fixture.limits['max_cycles'] = 2
        (fixture.root/'worker_limits.json').write_text(json.dumps(fixture.limits))
        account = json.loads((fixture.primary/'wallet_account.json').read_bytes())
        expected = dict(expected_primary=primary, expected_account_hash=_hash(account))
        original = paired_worker._write_heartbeat
        stages = []
        diagnostic = {}

        def collect(at):
            snapshot = supervisor_sync.make_snapshot_with_retry(fixture.primary, now=at)
            return supervisor_sync.validate_snapshot(snapshot, fixture.policy, now=at, **expected)

        def inspect_before_heartbeat(*args, **kwargs):
            state, stats, at = args[2], args[4], kwargs['at']
            running = state['status'] == 'running'
            if running and stats['complete'] == 2:
                # The native second batch has committed all account projections
                # and released its publication connection before this callback.
                heartbeat = json.loads((fixture.primary/'heartbeat.json').read_bytes())
                report = json.loads((fixture.primary/'wallet_metrics.json').read_bytes())
                self.assertEqual(heartbeat['worker_lifecycle']['completed_batches'], 1)
                self.assertEqual(heartbeat['status'], 'ok')
                self.assertIs(heartbeat['model_ready'], True)
                self.assertNotEqual(heartbeat['wallet_report_hash'], _hash(report))
                self.assertEqual(report['observed_at'], at.isoformat())
                self.assertEqual(len(stats['latest']['accounts']), 8)
                path = fixture.primary/'paper_publications.sqlite'
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=0)) as db:
                    self.assertEqual(dict(db.execute(
                        'SELECT status, COUNT(*) FROM paired_batches GROUP BY status')), {'complete': 2})
                assert_no_pending_paired_batch(fixture.primary)
                try:
                    supervisor_sync.make_snapshot(fixture.primary, now=at)
                except ValueError as error:
                    self.assertEqual(str(error), 'wallet report does not match the worker heartbeat')
                    self.assertIsNone(error.__cause__)
                    self.assertFalse(supervisor_sync._snapshot_busy(error))
                    frames = []
                    cursor = error.__traceback__
                    while cursor:
                        if (Path(cursor.tb_frame.f_code.co_filename).resolve() ==
                                Path(supervisor_sync.__file__).resolve()):
                            frames.append(dict(function=cursor.tb_frame.f_code.co_name,
                                               line=cursor.tb_lineno))
                        cursor = cursor.tb_next
                    self.assertTrue(any(frame['function'] == 'validate_snapshot' for frame in frames))
                    diagnostic.update(error_type=type(error).__name__,
                                      validation='wallet_report_hash_mismatch',
                                      typed_busy=False, frames=frames[-8:])
                else:
                    self.fail('the expected pre-heartbeat projection gap was not reproduced')
                # The rejection is not explained by pending work or a held DB
                # lease: the same native publication gate also passes afterward.
                assert_no_pending_paired_batch(fixture.primary)
                stages.append('completed_report_before_heartbeat_rejected')

            heartbeat = original(*args, **kwargs)
            if running and stats['complete'] in (1, 2):
                files = collect(at)
                observed = json.loads(files['heartbeat.json'])
                self.assertEqual(observed['worker_lifecycle']['completed_batches'], stats['complete'])
                self.assertEqual(observed['status'], 'ok')
                self.assertEqual(observed['wallet_report_hash'],
                                 _hash(json.loads(files['wallet_metrics.json'])))
                stages.append('batch_' + str(stats['complete']) + '_snapshot_validated')
            return heartbeat

        with patch.object(paired_worker, '_write_heartbeat', side_effect=inspect_before_heartbeat), \
                patch.object(paired_worker, 'PublicQuoteCollector',
                             side_effect=AssertionError('network collector forbidden in synthetic diagnostic')):
            result = await fixture.invoke()

        self.assertEqual(result['completed_batches'], 2)
        self.assertEqual(fixture.market.collect.await_count, 2)
        self.assertEqual(stages, ['batch_1_snapshot_validated',
                                  'completed_report_before_heartbeat_rejected',
                                  'batch_2_snapshot_validated'])
        print('snapshot_publication_window ' + json.dumps(dict(
            synthetic_only=True, primary_wallet_eur=200, stages=stages, **diagnostic), sort_keys=True))
