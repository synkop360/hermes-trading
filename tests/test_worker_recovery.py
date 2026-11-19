"""Audited automatic recovery of the paper worker error latch (LAT-121).

``paper_safety.recover_worker`` lifts the worker latch only, in paper mode,
after N = 3 consecutive successful cycles, when every precondition holds:
cause gone (no other entry blocker), today's verified daily backup, ready
storage without storage stop, eight flat accounts, no pending batch. Each
refusal leaves the latch and writes no audit file. Synthetic state only.

Run: ``PYTHONPATH=$PWD/tests:$PWD uv run python -m unittest test_worker_recovery``
"""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_paired_worker as worker_fixtures
from venue_fixtures import venue_record
from hermes_trading import paper_safety as safety

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
ACCOUNTS = sorted(f'{arm}_{wallet}' for arm in ('adaptive', 'control') for wallet in (50, 100, 150, 200))
POLICY = dict(schema_version=1, experiment_id='paired-test', capacity_bytes=500000000,
              alert_fraction='0.70', max_worker_errors=3, max_supervisor_errors=3,
              supervisor_stale_seconds=300, diagnostics_max_bytes=1048576, diagnostics_generations=3)


class RecoverWorkerTransitionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'paper'
        self.root.mkdir()
        (self.root / 'paper_safety.json').write_text(json.dumps(dict(POLICY, volume_root=str(self.root))))
        self.policy = safety.load_policy(self.root, 'paired-test')
        receiver = patch.object(safety, '_utc_now', return_value=NOW + timedelta(seconds=1))
        receiver.start()
        self.addCleanup(receiver.stop)
        for index in range(3):
            safety.record_worker_result(self.root, success=False, error_kind='daily_backup_failed',
                                        now=NOW - timedelta(minutes=10, seconds=-index))
        self.successes = []
        for index, minutes in enumerate((2, 1, 0)):
            at = NOW - timedelta(minutes=minutes)
            safety.record_worker_result(self.root, success=True, now=at)
            self.successes.append(dict(at=at.isoformat(), batch_id=f'batch-{index}'))
        safety.record_supervisor_result(self.root, success=True, event_id='synthetic-1', now=NOW)

    # --- fixtures -------------------------------------------------------
    def state(self):
        return json.loads((self.root / 'paper_safety_state.json').read_text())

    def bindings(self, **changes):
        value = dict(accounts=dict.fromkeys(ACCOUNTS, 0), pending_batch=False,
                     daily_backup=dict(date=NOW.date().isoformat(), sha256='a' * 64,
                                       status='completed', verified=True),
                     daily_backup_error=None, storage=dict(status='ready', storage_stop=False),
                     local_archive_capture_error=None)
        value.update(changes)
        return value

    def request(self, bindings=None, **changes):
        value = dict(schema_version=1, purpose=safety.WORKER_RECOVERY_PURPOSE, request_id='1' * 32,
                     experiment_id='paired-test', mode='paper', prepared_at=NOW.isoformat(),
                     valid_until=(NOW + timedelta(seconds=120)).isoformat(),
                     state_hash=safety._recovery_hash(self.state()), successes=list(self.successes),
                     bindings=self.bindings() if bindings is None else bindings)
        value.update(changes)
        return value

    def recover(self, request=None, *, measured=None, now=NOW, env=None):
        request = self.request() if request is None else request
        measured = request['bindings'] if measured is None else measured
        return safety.recover_worker(self.root, request, preflight=lambda: nullcontext(measured),
                                     now=now, enabled=True, env={} if env is None else env)

    def audit(self):
        directory = self.root / safety.WORKER_RECOVERY_DIRECTORY
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []

    def assertRefused(self, code, **kwargs):
        before = self.state()
        with self.assertRaises(safety.WorkerRecoveryRefused) as raised:
            self.recover(**kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.state(), before)
        self.assertTrue(self.state()['worker']['entry_halt'])
        self.assertEqual(self.audit(), [])
        self.assertFalse((self.root / safety._RECOVERY_PENDING).exists())

    # --- acceptance -----------------------------------------------------
    def test_n_is_three(self):
        self.assertEqual(safety.WORKER_RECOVERY_SUCCESSES, 3)

    def test_lift_writes_intent_and_receipt_and_only_clears_the_worker_latch(self):
        before = self.state()
        self.assertIn('worker_error_halt', safety.entry_status(self.root, policy=self.policy, now=NOW)['reasons'])
        result = self.recover()
        self.assertEqual((result['status'], result['outcome'], result['current_worker_halted']),
                         ('completed', 'completed', False))
        after = self.state()
        self.assertEqual(after['worker'], dict(before['worker'], entry_halt=False, consecutive_errors=0))
        self.assertEqual(after['supervisor'], before['supervisor'])
        self.assertEqual(self.audit(), ['1' * 32 + '.intent.json', '1' * 32 + '.receipt.json'])
        receipt = json.loads((self.root / safety.WORKER_RECOVERY_DIRECTORY / ('1' * 32 + '.receipt.json')).read_text())
        intent = json.loads((self.root / safety.WORKER_RECOVERY_DIRECTORY / ('1' * 32 + '.intent.json')).read_text())
        self.assertEqual(receipt['intent_hash'], safety._recovery_hash(intent))
        self.assertEqual(intent['request']['successes'], self.successes)
        self.assertEqual((receipt['before_worker'], receipt['after_worker']), (before['worker'], after['worker']))
        self.assertFalse((self.root / safety._RECOVERY_PENDING).exists())
        self.assertEqual(safety.entry_status(self.root, policy=self.policy, now=NOW)['reasons'], [])
        # The next success keeps the counter at zero; the next failure counts from one.
        safety.record_worker_result(self.root, success=False, now=NOW + timedelta(seconds=1))
        self.assertEqual(self.state()['worker']['consecutive_errors'], 1)

    def test_one_refusal_per_precondition(self):
        accounts = dict.fromkeys(ACCOUNTS, 0)
        accounts['control_150'] = 1
        stale_backup = self.bindings()['daily_backup']
        cases = {
            'accounts_not_flat': self.bindings(accounts=accounts),
            'batch_pending': self.bindings(pending_batch=True),
            'daily_backup_missing': self.bindings(daily_backup=dict(stale_backup, verified=False)),
            'daily_backup_yesterday': self.bindings(daily_backup=dict(stale_backup, date='2026-11-18')),
            'daily_backup_failed_status': self.bindings(daily_backup=dict(stale_backup, status='failed')),
            'daily_backup_error': self.bindings(daily_backup_error='OSError'),
            'storage_blocked': self.bindings(storage=dict(status='blocked', storage_stop=True)),
            'storage_stop_latched': self.bindings(storage=dict(status='ready', storage_stop=True)),
            'archive_capture_error': self.bindings(local_archive_capture_error='OSError'),
        }
        codes = dict(daily_backup_yesterday='daily_backup_missing', daily_backup_failed_status='daily_backup_missing',
                     daily_backup_error='daily_backup_missing', storage_blocked='storage_not_ready',
                     storage_stop_latched='storage_not_ready')
        for name, bindings in cases.items():
            with self.subTest(precondition=name):
                self.assertRefused(codes.get(name, name), request=self.request(bindings))

    def test_series_must_be_three_distinct_consecutive_successes(self):
        with self.subTest('two successes'):
            self.assertRefused('successes_insufficient', request=self.request(successes=self.successes[1:]))
        with self.subTest('same batch counted twice'):
            successes = [self.successes[0], dict(self.successes[1], batch_id='batch-0'), self.successes[2]]
            self.assertRefused('successes_not_consecutive', request=self.request(successes=successes))
        with self.subTest('series not ending at the latest observation'):
            successes = self.successes[:2] + [dict(self.successes[2], at=(NOW - timedelta(seconds=30)).isoformat())]
            self.assertRefused('worker_last_cycle_not_successful', request=self.request(successes=successes))

    def test_interrupted_series_resets(self):
        request = self.request()
        safety.record_worker_result(self.root, success=False, now=NOW + timedelta(milliseconds=1))
        # A failure after the request changes the state: refused, latch kept.
        self.assertRefused('safety_state_changed', request=request)
        self.assertRefused('worker_last_cycle_not_successful', request=self.request())

    def test_cause_still_present_or_health_stale_refuses(self):
        with self.subTest('volume threshold still reached'):
            blocked = dict(blocked=True, reasons=['volume_threshold'], used_bytes=1, capacity_bytes=1, threshold_bytes=1)
            with patch.object(safety, 'inspect_volume', return_value=blocked):
                self.assertRefused('entry_blocker_present')
        with self.subTest('stale worker observation'):
            request = self.request(prepared_at=(NOW + timedelta(seconds=61)).isoformat(),
                                   valid_until=(NOW + timedelta(seconds=181)).isoformat())
            self.assertRefused('worker_health_stale', request=request, now=NOW + timedelta(seconds=61))
        with self.subTest('preflight measured something else'):
            self.assertRefused('preflight_changed', measured=self.bindings(pending_batch=True))

    def test_mode_other_than_paper_is_refused(self):
        for mode in ('live', 'training', 'LIVE'):
            with self.subTest(env=mode):
                self.assertRefused('mode_not_paper', env={'HERMES_TRADING_MODE': mode})
        with self.subTest(request_mode='live'):
            self.assertRefused('mode_not_paper', request=self.request(mode='live'))
        with self.assertRaisesRegex(ValueError, 'opt-in'):
            safety.recover_worker(self.root, self.request(), preflight=lambda: nullcontext(self.bindings()),
                                  now=NOW, env={})
        self.assertTrue(self.state()['worker']['entry_halt'])

    def test_unlatched_worker_is_not_touched(self):
        self.recover()
        files = self.audit()
        request = self.request(request_id='2' * 32)
        with self.assertRaises(safety.WorkerRecoveryRefused) as raised:
            self.recover(request)
        self.assertEqual(raised.exception.code, 'worker_not_latched')
        self.assertEqual(self.audit(), files)

    def test_retry_after_receipt_is_idempotent(self):
        request = self.request()
        first = self.recover(request)
        files = {name: (self.root / safety.WORKER_RECOVERY_DIRECTORY / name).read_bytes() for name in self.audit()}
        state = (self.root / 'paper_safety_state.json').read_bytes()
        again = self.recover(request)
        self.assertEqual(again['status'], 'already_completed')
        self.assertEqual(again['request_id'], first['request_id'])
        self.assertEqual({name: (self.root / safety.WORKER_RECOVERY_DIRECTORY / name).read_bytes()
                          for name in self.audit()}, files)
        self.assertEqual((self.root / 'paper_safety_state.json').read_bytes(), state)
        self.assertIsNone(safety.reconcile_worker_recovery(self.root, experiment_id='paired-test', enabled=True, env={}))

    def test_shared_pending_marker_excludes_concurrent_recoveries(self):
        with self.subTest('supervisor recovery in flight'):
            marker = self.root / safety._RECOVERY_PENDING
            marker.write_text(json.dumps(dict(schema_version=1, request_id='f' * 32,
                                              request_hash='0' * 64, root_hash='0' * 64)))
            before = self.state()
            with self.assertRaises(safety.WorkerRecoveryRefused) as raised:
                self.recover()
            self.assertEqual(raised.exception.code, 'recovery_pending')
            self.assertEqual(self.state(), before)
            # Startup reconciliation leaves a foreign marker to its own procedure.
            self.assertIsNone(safety.reconcile_worker_recovery(self.root, experiment_id='paired-test', enabled=True, env={}))
            self.assertTrue(marker.exists())
            marker.unlink()
        with self.subTest('unfinished 17/11 manual release'):
            audit = self.root / '.manual-latch-release-2026-11-17'
            audit.mkdir()
            (audit / 'intent.json').write_text('{}')
            self.assertRefused('manual_release_unfinished')
            (audit / 'receipt.json').write_text('{}')
            self.assertEqual(self.recover()['status'], 'completed')

    def test_supervisor_latch_keeps_its_own_procedure(self):
        for index in range(3):
            safety.record_supervisor_result(self.root, success=False, event_id=f'failed-{index}',
                                            now=NOW + timedelta(milliseconds=index + 1))
        request = self.request()
        self.recover(request, now=NOW + timedelta(milliseconds=10))
        state = self.state()
        self.assertFalse(state['worker']['entry_halt'])
        self.assertTrue(state['supervisor']['entry_halt'])
        self.assertEqual(safety.entry_status(self.root, policy=self.policy, now=NOW + timedelta(milliseconds=10))['reasons'],
                         ['supervisor_error_halt'])

    def test_crash_before_state_write_is_aborted_at_restart(self):
        with patch.object(safety, '_write', side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):
                self.recover()
        self.assertTrue(self.state()['worker']['entry_halt'])
        status = safety.entry_status(self.root, policy=self.policy, now=NOW)
        self.assertIn('supervisor_recovery_pending', status['reasons'])
        self.assertTrue(status['blocked'])
        # The supervisor keeps recording meanwhile; reconciliation uses the worker row only.
        safety.record_supervisor_result(self.root, success=True, event_id='synthetic-2', now=NOW + timedelta(seconds=1))
        result = safety.reconcile_worker_recovery(self.root, experiment_id='paired-test', enabled=True, env={})
        self.assertEqual((result['status'], result['outcome']), ('reconciled_aborted', 'aborted'))
        self.assertTrue(self.state()['worker']['entry_halt'])
        self.assertEqual(self.audit(), ['1' * 32 + '.abort.json', '1' * 32 + '.intent.json'])
        self.assertFalse((self.root / safety._RECOVERY_PENDING).exists())
        self.assertEqual(safety.reconcile_worker_recovery(self.root, experiment_id='paired-test', enabled=True, env={}), None)

    def test_crash_after_state_write_is_receipted_at_restart(self):
        original = safety._recovery_once

        def crash(path, value):
            if path.name.endswith('.receipt.json'):
                raise OSError('synthetic crash')
            return original(path, value)
        with patch.object(safety, '_recovery_once', side_effect=crash):
            with self.assertRaises(OSError):
                self.recover()
        self.assertFalse(self.state()['worker']['entry_halt'])
        self.assertIn('supervisor_recovery_pending', safety.entry_status(self.root, policy=self.policy, now=NOW)['reasons'])
        result = safety.reconcile_worker_recovery(self.root, experiment_id='paired-test', enabled=True, env={})
        self.assertEqual((result['status'], result['outcome']), ('reconciled_completed', 'completed'))
        self.assertEqual(self.audit(), ['1' * 32 + '.intent.json', '1' * 32 + '.receipt.json'])
        self.assertEqual(safety.entry_status(self.root, policy=self.policy, now=NOW)['reasons'], [])

    def test_tampered_intent_fails_closed(self):
        with patch.object(safety, '_write', side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):
                self.recover()
        path = self.root / safety.WORKER_RECOVERY_DIRECTORY / ('1' * 32 + '.intent.json')
        intent = json.loads(path.read_text())
        intent['after_worker']['consecutive_errors'] = 1
        path.write_text(json.dumps(intent))
        with self.assertRaises(ValueError):
            safety.reconcile_worker_recovery(self.root, experiment_id='paired-test', enabled=True, env={})
        self.assertTrue(self.state()['worker']['entry_halt'])
        self.assertTrue((self.root / safety._RECOVERY_PENDING).exists())


class WorkerRecoveryLoopTests(unittest.IsolatedAsyncioTestCase):
    """The worker itself counts the series and calls recover_worker."""
    setUp = worker_fixtures.WorkerTests.setUp
    tearDown = worker_fixtures.WorkerTests.tearDown
    positions = worker_fixtures.WorkerTests.positions
    clock = worker_fixtures.WorkerTests.clock
    sleep = worker_fixtures.WorkerTests.sleep
    invoke = worker_fixtures.WorkerTests.invoke

    def prepare(self):
        (self.root / 'paper_safety.json').write_text(json.dumps(dict(POLICY, volume_root=str(self.root.resolve()))))
        receiver = patch('hermes_trading.paper_safety._utc_now', side_effect=lambda: self.at + timedelta(seconds=1))
        receiver.start()
        self.addCleanup(receiver.stop)
        self.daily = (self.root.parent / (self.root.name + '-daily')).resolve()
        self.addCleanup(__import__('shutil').rmtree, self.daily, True)
        self.limits.update(max_cycles=40, max_runtime_seconds=60 * 86400,
                           daily_backup=dict(schema_version=1, destination=str(self.daily),
                                             max_bytes=500000000, timeout_seconds=30, keep=2))
        (self.root / 'worker_limits.json').write_text(json.dumps(self.limits))
        self.events = 0
        self.supervisor_ok()

    def supervisor_ok(self):
        self.events += 1
        safety.record_supervisor_result(self.root, success=True, event_id=f'synthetic-{self.events}', now=self.at)

    def refresh(self, *, bid='80', ask='80'):
        candle = replace(self.snapshot.candle, open_timestamp=self.at - timedelta(hours=1), close_timestamp=self.at)
        price = dict(self.snapshot.price, bid=bid, ask=ask, received_at=self.at.isoformat())
        self.market.collect.return_value = {'ETH/EUR': replace(self.snapshot, candle=candle, price=price,
                                                               venue_rules=venue_record('ETH/EUR', self.at))}
        for profile in self.manifest['profiles']:
            path = self.root / profile['directory'] / 'wallet_costs.json'
            value = json.loads(path.read_text())
            value['as_of'] = self.at.isoformat()
            path.write_text(json.dumps(value))
        self.access['valid_until'] = (self.at + timedelta(hours=1)).isoformat()
        (self.primary / 'model_access.json').write_text(json.dumps(self.access))

    async def run_cycles(self, cycles, **prices):
        seen = []

        class Crash(Exception):
            pass

        async def sleep(seconds):
            worker = json.loads((self.root / 'paper_safety_state.json').read_text())['worker']
            heartbeat = json.loads((self.root / 'heartbeat.json').read_text())
            seen.append(dict(halt=worker['entry_halt'], recovery=heartbeat['worker_lifecycle'].get('worker_recovery')))
            if len(seen) == cycles:
                raise Crash()
            self.at += timedelta(seconds=seconds)
            self.refresh(**prices)
            self.supervisor_ok()
        with self.assertRaises(Crash):
            await self.invoke(sleep=sleep)
        return seen

    def latch(self):
        for index in range(1, 4):
            safety.record_worker_result(self.root, success=False, error_kind='daily_backup_failed',
                                        now=self.at + timedelta(milliseconds=index))
        self.at += timedelta(seconds=60)
        self.refresh()
        self.supervisor_ok()

    def files(self):
        directory = self.root / safety.WORKER_RECOVERY_DIRECTORY
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []

    async def flat(self):
        await self.run_cycles(2, bid='77', ask='78')
        self.assertEqual(sum(len([p for p in self.positions(name) if p.status.value != 'closed'])
                             for name in ACCOUNTS), 0)

    async def test_worker_lifts_after_three_successes_and_reports_it(self):
        self.prepare()
        await self.flat()
        self.latch()
        seen = await self.run_cycles(4)
        self.assertEqual([row['halt'] for row in seen], [True, True, False, False])
        self.assertEqual([row['recovery']['successes'] for row in seen[:2]], [1, 2])
        self.assertEqual(seen[2]['recovery']['status'], 'completed')
        self.assertEqual(len(self.files()), 2)
        events = [json.loads(line)['event'] for line in (self.root / 'paper_runtime.log').read_text().splitlines()]
        self.assertEqual(events.count('worker_recovery_completed'), 1)

    async def test_explicit_non_paper_mode_keeps_the_latch(self):
        self.prepare()
        await self.flat()
        self.latch()
        with patch.dict('os.environ', {'HERMES_TRADING_MODE': 'training'}):
            seen = await self.run_cycles(4)
        self.assertEqual([row['halt'] for row in seen], [True] * 4)
        self.assertEqual((seen[-1]['recovery']['status'], seen[-1]['recovery']['reason']), ('refused', 'mode_not_paper'))
        self.assertEqual(self.files(), [])

    async def test_restart_reconciles_an_interrupted_lift_then_counts_again(self):
        self.prepare()
        await self.flat()
        self.latch()
        original = safety._write

        def crash(root, value):
            # Only the lift itself fails, after its intent and pending marker.
            if value['worker'] is not None and value['worker']['entry_halt'] is False and \
                    (root / safety._RECOVERY_PENDING).exists():
                raise OSError('synthetic crash')
            return original(root, value)
        with patch.object(safety, '_write', side_effect=crash):
            seen = await self.run_cycles(3)
        self.assertEqual(seen[-1]['recovery']['status'], 'failed')
        self.assertTrue(seen[-1]['halt'])
        self.assertTrue((self.root / safety._RECOVERY_PENDING).exists())
        seen = await self.run_cycles(3)
        self.assertEqual(seen[0]['recovery']['successes'], 1)
        self.assertFalse((self.root / safety._RECOVERY_PENDING).exists())
        self.assertEqual([row['halt'] for row in seen], [True, True, False])
        self.assertEqual(sorted(name.split('.')[1] for name in self.files()), ['abort', 'intent', 'intent', 'receipt'])


if __name__ == '__main__':
    unittest.main()
