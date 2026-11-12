"""Entry admission and diagnostic-only mutation boundaries for paper launch."""
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


class PaperSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'paper'
        self.root.mkdir()
        self.volume = self.base / 'volume'
        self.volume.mkdir()
        self.config = {
            'schema_version': 1, 'experiment_id': 'paper-launch-test',
            'volume_root': str(self.volume.resolve()), 'capacity_bytes': 500000000,
            'alert_fraction': '0.70', 'max_worker_errors': 3,
            'max_supervisor_errors': 3, 'supervisor_stale_seconds': 300,
            'diagnostics_max_bytes': 1048576, 'diagnostics_generations': 3,
        }
        # A missing implementation is an explicit RED, rather than an import error.
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.paper_safety'),
                             'paper entry safety implementation is absent')
        self.api = importlib.import_module('hermes_trading.paper_safety')
        self.receiver = patch.object(self.api, '_utc_now', return_value=NOW + timedelta(hours=1))
        self.receiver.start()
        self.addCleanup(self.receiver.stop)

    def policy(self, **changes):
        value = dict(self.config, **changes)
        (self.root / 'paper_safety.json').write_text(json.dumps(value), encoding='utf-8')
        return self.api.load_policy(self.root, 'paper-launch-test')

    def inventory(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_absent_policy_is_disabled_without_creating_state(self):
        self.assertIsNone(self.api.load_policy(self.root, 'paper-launch-test'))
        self.assertIsNone(self.api.record_worker_result(self.root, success=True, now=NOW))
        self.assertIsNone(self.api.record_supervisor_result(self.root, success=True, event_id='one', now=NOW))
        self.assertFalse(self.api.entry_status(self.root, policy=None, now=NOW)['blocked'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_policy_binds_experiment_fixed_limits_and_absolute_volume(self):
        loaded = self.policy()
        self.assertTrue(loaded['supervisor_health_required'])
        with self.assertRaises(ValueError):
            self.api.load_policy(self.root, 'another-experiment')
        for changes in ({'schema_version': True}, {'capacity_bytes': 500000001},
                        {'alert_fraction': .7}, {'volume_root': 'relative'},
                        {'max_worker_errors': 4}, {'max_supervisor_errors': True},
                        {'diagnostics_generations': 4}, {'diagnostics_max_bytes': 2},
                        {'supervisor_stale_seconds': 0}, {'unknown': 'ignored'},
                        {'supervisor_health_required': 'false'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.policy(**changes)

    def test_volume_counts_legacy_files_and_blocks_exactly_at_seventy_percent(self):
        policy = self.policy(supervisor_health_required=False)
        legacy = self.volume / 'old-release'
        legacy.mkdir()
        data = legacy / 'old-trading.sqlite'
        with data.open('wb') as stream:
            stream.truncate(349999999)
        first = self.api.inspect_volume(policy)
        self.assertEqual(first['used_bytes'], 349999999)
        self.assertEqual(first['threshold_bytes'], 350000000)
        self.assertFalse(first['blocked'])
        with (self.volume / 'stray.log').open('wb') as stream:
            stream.write(b'x')
        second = self.api.entry_status(self.root, policy=policy, now=NOW)
        self.assertTrue(second['blocked'])
        self.assertIn('volume_threshold', second['reasons'])
        self.assertEqual(data.stat().st_size, 349999999)

    def test_unknown_or_unbounded_volume_measurement_blocks_entries(self):
        policy = self.policy(supervisor_health_required=False)
        self.volume.rmdir()
        self.assertTrue(self.api.inspect_volume(policy)['blocked'])
        self.volume.mkdir()
        (self.volume / 'one').write_bytes(b'1')
        (self.volume / 'two').write_bytes(b'2')
        with patch.object(self.api, 'MAX_VOLUME_ENTRIES', 1):
            result = self.api.entry_status(self.root, policy=policy, now=NOW)
        self.assertTrue(result['blocked'])
        self.assertIn('volume_measurement_unknown', result['reasons'])
        with patch.object(self.api, 'time') as clock:
            clock.monotonic.side_effect = [0, 11, 11, 11]
            self.assertTrue(self.api.inspect_volume(policy)['blocked'])

    def test_volume_symlink_is_not_followed_and_reports_unknown(self):
        policy = self.policy()
        external = self.base / 'outside'
        external.write_bytes(b'private-content')
        link = self.volume / 'link'
        try:
            link.symlink_to(external)
        except OSError:
            self.skipTest('symbolic links unavailable for this Windows user')
        before = external.read_bytes()
        result = self.api.inspect_volume(policy)
        self.assertTrue(result['blocked'])
        self.assertEqual(external.read_bytes(), before)

    def test_worker_three_error_halt_survives_success_and_module_restart(self):
        policy = self.policy(supervisor_health_required=False)
        for _ in range(2):
            self.api.record_worker_result(self.root, success=False, error_kind='worker_failure', now=NOW)
        self.assertFalse(self.api.entry_status(self.root, policy=policy, now=NOW)['blocked'])
        self.api.record_worker_result(self.root, success=True, now=NOW)
        for index in range(3):
            self.api.record_worker_result(self.root, success=False, error_kind='worker_failure',
                                          now=NOW + timedelta(seconds=index))
        importlib.reload(self.api)
        with patch.object(self.api, '_utc_now', return_value=NOW + timedelta(hours=1)):
            self.api.record_worker_result(self.root, success=True, now=NOW + timedelta(seconds=4))
        result = self.api.entry_status(self.root, policy=policy, now=NOW + timedelta(seconds=4))
        self.assertTrue(result['blocked'])
        self.assertIn('worker_error_halt', result['reasons'])

    def test_supervisor_latest_retry_is_idempotent_conflict_rejects_and_stale_blocks(self):
        policy = self.policy()
        self.assertIn('supervisor_health_missing', self.api.entry_status(self.root, policy=policy, now=NOW)['reasons'])
        self.api.record_supervisor_result(self.root, success=True, event_id='cycle-1', now=NOW)
        before = self.inventory()
        self.api.record_supervisor_result(self.root, success=True, event_id='cycle-1', now=NOW)
        self.assertEqual(self.inventory(), before)
        with self.assertRaises(ValueError):
            self.api.record_supervisor_result(self.root, success=False, error_kind='review_failure',
                                              event_id='cycle-1', now=NOW)
        self.assertEqual(self.inventory(), before)
        self.assertFalse(self.api.entry_status(self.root, policy=policy, now=NOW + timedelta(seconds=300))['blocked'])
        self.assertIn('supervisor_health_stale', self.api.entry_status(self.root, policy=policy,
                      now=NOW + timedelta(seconds=301))['reasons'])

    def test_supervisor_counter_resets_before_threshold_and_latches_after_three(self):
        policy = self.policy()
        events = [(False, 'a'), (False, 'b'), (True, 'c'), (False, 'd'), (False, 'e')]
        for seconds, (success, event_id) in enumerate(events):
            self.api.record_supervisor_result(self.root, success=success, event_id=event_id,
                now=NOW + timedelta(seconds=seconds), error_kind=None if success else 'review_failure')
        self.assertFalse(self.api.entry_status(self.root, policy=policy, now=NOW + timedelta(seconds=4))['blocked'])
        self.api.record_supervisor_result(self.root, success=False, event_id='f',
            now=NOW + timedelta(seconds=5), error_kind='review_failure')
        self.api.record_supervisor_result(self.root, success=True, event_id='g', now=NOW + timedelta(seconds=6))
        self.assertIn('supervisor_error_halt', self.api.entry_status(self.root, policy=policy,
                      now=NOW + timedelta(seconds=6))['reasons'])

    def test_old_future_and_secret_diagnostic_reports_reject_without_state_mutation(self):
        self.policy()
        self.api.record_supervisor_result(self.root, success=True, event_id='latest', now=NOW)
        before = self.inventory()
        for when in (NOW - timedelta(seconds=1), NOW + timedelta(hours=2)):
            with self.subTest(when=when), self.assertRaises(ValueError):
                self.api.record_supervisor_result(self.root, success=True, event_id='other', now=when)
            self.assertEqual(self.inventory(), before)
        for kind in ('token=SECRET', 'exception with private detail', 'x' * 65):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.api.record_worker_result(self.root, success=False, error_kind=kind, now=NOW)
            self.assertEqual(self.inventory(), before)

    def test_optional_error_kind_uses_bounded_default_and_policy_rebinding_does_not_reset_latch(self):
        self.policy(supervisor_health_required=False)
        for _ in range(3):
            self.api.record_worker_result(self.root, success=False, now=NOW)
        policy = self.policy(supervisor_health_required=True)
        self.assertIn('safety_state_unknown', self.api.entry_status(self.root, policy=policy, now=NOW)['reasons'])
        before = self.inventory()
        with self.assertRaises(ValueError):
            self.api.record_worker_result(self.root, success=True, now=NOW)
        self.assertEqual(self.inventory(), before)

    def test_malformed_health_state_never_reports_entries_healthy(self):
        policy = self.policy(supervisor_health_required=False)
        self.api.record_worker_result(self.root, success=True, now=NOW)
        state_path = self.root / 'paper_safety_state.json'
        original = json.loads(state_path.read_text(encoding='utf-8'))
        for field, value in (('schema_version', True), ('worker', dict(original['worker'], consecutive_errors=2)),
                             ('worker', dict(original['worker'], entry_halt=True))):
            with self.subTest(field=field, value=value):
                altered = dict(original, **{field: value})
                state_path.write_text(json.dumps(altered), encoding='utf-8')
                self.assertIn('safety_state_unknown', self.api.entry_status(self.root, policy=policy, now=NOW)['reasons'])

    def test_corrupt_or_rebound_state_fails_entry_admission_without_replacing_it(self):
        policy = self.policy(supervisor_health_required=False)
        self.api.record_worker_result(self.root, success=False, error_kind='worker_failure', now=NOW)
        state = self.root / 'paper_safety_state.json'
        state.write_text('{broken', encoding='utf-8')
        before = state.read_bytes()
        self.assertIn('safety_state_unknown', self.api.entry_status(self.root, policy=policy, now=NOW)['reasons'])
        with self.assertRaises(ValueError):
            self.api.record_worker_result(self.root, success=True, now=NOW)
        self.assertEqual(state.read_bytes(), before)

    def test_diagnostic_rotation_keeps_whole_bytes_and_leaves_financial_files_untouched(self):
        policy = self.policy(supervisor_health_required=False)
        protected = {'trades.jsonl': b'trade\n', 'decisions.jsonl': b'decision\n',
                     'hypotheses.jsonl': b'hypothesis\n', 'trading.db': b'sqlite',
                     'strategy.yaml': b'version: 1\n'}
        for name, raw in protected.items():
            (self.root / name).write_bytes(raw)
        log = self.root / 'paper_runtime.log'
        log.write_bytes(b'x' * 1048575)
        self.api.rotate_diagnostics(self.root, policy=policy)
        self.assertFalse((self.root / 'paper_runtime.log.1').exists())
        expected = []
        for byte in (b'a', b'b', b'c', b'd'):
            raw = byte * 1048576
            log.write_bytes(raw)
            expected.insert(0, raw)
            self.api.rotate_diagnostics(self.root, policy=policy)
            self.assertEqual(log.read_bytes(), b'')
            for index, prior in enumerate(expected[:3], 1):
                self.assertEqual((self.root / f'paper_runtime.log.{index}').read_bytes(), prior)
        self.assertFalse((self.root / 'paper_runtime.log.4').exists())
        for name, raw in protected.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_log_generation_link_rejects_before_any_rotation(self):
        policy = self.policy()
        target = self.base / 'protected'
        target.write_bytes(b'outside')
        link = self.root / 'paper_runtime.log.2'
        try:
            os.link(target, link)
        except OSError:
            self.skipTest('hard links unavailable')
        log = self.root / 'paper_runtime.log'
        log.write_bytes(b'x' * 1048576)
        before = log.read_bytes()
        with self.assertRaises(ValueError):
            self.api.rotate_diagnostics(self.root, policy=policy)
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual(target.read_bytes(), b'outside')


if __name__ == '__main__':
    unittest.main()
