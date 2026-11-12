"""Opt-in health transport exercises the actual identity-bound receiver."""
import base64
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hermes_trading.reflection_supervision import _hash
from scripts import hermes_watch


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


class PaperSupervisorHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'worker'
        self.root.mkdir()
        self.local = self.base/'watcher'
        self.local.mkdir()
        selection = dict(schema_version=1, purpose='paired_primary_selection_v1', wallet_eur=200,
                         rationale_sha256='a'*64)
        manifest = dict(primary_wallet_eur=200, primary_selection=selection,
                        reporting_protocol=dict(schema_version=2, primary_wallet_eur=200,
                                                primary_selection_sha256=_hash(selection)),
                        profiles=[dict(arm='adaptive', wallet_eur=200, directory='adaptive_200')])
        (self.root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        primary = self.root/'adaptive_200'
        primary.mkdir()
        self.owner = dict(schema_version=1, owner='external_llm', experiment_id='health-test',
                          cadence_closed_trades=5, one_variable_only=True,
                          model=dict(provider='local', name='pinned-test-model'))
        for target in (primary, self.local):
            (target/'reflection_owner.json').write_text(json.dumps(self.owner), encoding='utf-8')
        policy = dict(schema_version=1, experiment_id='health-test', volume_root=str(self.root),
                      capacity_bytes=500000000, alert_fraction='0.70', max_worker_errors=3,
                      max_supervisor_errors=3, supervisor_stale_seconds=300,
                      diagnostics_max_bytes=1048576, diagnostics_generations=3)
        (self.root/'paper_safety.json').write_text(json.dumps(policy), encoding='utf-8')
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.paper_supervisor_health'),
                             'identity-bound supervisor health feed is absent')
        self.api = importlib.import_module('hermes_trading.paper_supervisor_health')
        self.config = dict(paper_safety_enabled=True, experiment_id='health-test', policy_hash=_hash(self.owner))
        self.requests = []

    def request(self, **changes):
        value = dict(schema_version=1, purpose='paper_supervisor_health_v1', experiment_id='health-test',
                     policy_hash=_hash(self.owner), event_id='cycle-1', observed_at=NOW.isoformat(),
                     success=True, error_kind=None)
        return dict(value, **changes)

    def health(self):
        return json.loads((self.root/'paper_safety_state.json').read_text(encoding='utf-8'))['supervisor']

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def transport(self, command, **kwargs):
        self.assertEqual(command[:3], ['railway.cmd', 'ssh', '--'])
        tokens = shlex.split(command[-1])
        self.assertEqual(tokens[:3], ['/app/.venv/bin/python', '-m', 'hermes_trading.paper_supervisor_health'])
        self.assertEqual(tokens[tokens.index('--root')+1], '/app/state')
        self.assertNotIn('input_text', kwargs)
        self.assertGreater(kwargs['timeout_seconds'], 0)
        self.assertLessEqual(kwargs['timeout_seconds'], 30)
        encoded = tokens[tokens.index('--request-base64')+1]
        self.assertLessEqual(len(encoded), 4096)
        request = json.loads(base64.b64decode(encoded, validate=True))
        self.requests.append(request)
        receipt = self.api.receive_health(self.root, request)
        return subprocess.CompletedProcess(command, 0, json.dumps(receipt), '')

    def test_receiver_binds_selected_primary_and_latest_retry_does_not_refresh(self):
        self.api.receive_health(self.root, self.request())
        first = self.inventory()
        self.api.receive_health(self.root, self.request())
        self.assertEqual(self.inventory(), first)
        self.assertEqual(self.health()['last_seen'], NOW.isoformat())
        with self.assertRaises(ValueError):
            self.api.receive_health(self.root, self.request(success=False, error_kind='supervisor_failure'))
        self.assertEqual(self.inventory(), first)

    def test_receiver_rejects_identity_or_secret_payload_before_any_state_mutation(self):
        first = self.inventory()
        for changes in ({'experiment_id': 'other'}, {'policy_hash': 'b'*64},
                        {'reason': 'token=SECRET'}, {'success': 1}, {'error_kind': 'token=SECRET'},
                        {'observed_at': '2026-10-09T12:00:00'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.api.receive_health(self.root, self.request(**changes))
            self.assertEqual(self.inventory(), first)
        (self.root/'paper_safety.json').unlink()
        first = self.inventory()
        with self.assertRaises(ValueError):
            self.api.receive_health(self.root, self.request())
        self.assertEqual(self.inventory(), first)

    def test_watcher_sends_only_code_outcome_after_each_completed_cycle(self):
        outcomes = [('not_due', True), ('no_change', True), ('published', True), ('rejected', True),
                    ('deferred', False), ('supervisor_stopped', False), ('failed', False),
                    ('awaiting_publication', False), ('unrecognized', False)]
        with patch.object(hermes_watch, 'STATE_DIR', self.local), \
                patch.object(hermes_watch, 'LAUNCH_CONFIG', self.config), \
                patch.object(hermes_watch, '_run', side_effect=self.transport):
            for index, (status, healthy) in enumerate(outcomes):
                with self.subTest(status=status), patch.object(hermes_watch, '_run_supervised_cycle',
                        return_value=dict(status=status, reason='private-response-must-not-transfer')):
                    hermes_watch._run_supervised_watch(now=NOW+timedelta(seconds=index))
                self.assertEqual(self.health()['success'], healthy)
                self.assertEqual(len(self.requests), index+1)
                self.assertNotIn('private-response', json.dumps(self.requests[-1]))
                self.assertEqual(self.requests[-1]['error_kind'] is None, healthy)

    def test_cycle_exception_is_observed_once_without_recording_exception_text(self):
        with patch.object(hermes_watch, 'STATE_DIR', self.local), \
                patch.object(hermes_watch, 'LAUNCH_CONFIG', self.config), \
                patch.object(hermes_watch, '_run_supervised_cycle', side_effect=RuntimeError('token=SECRET')), \
                patch.object(hermes_watch, '_run', side_effect=self.transport):
            with self.assertRaises(RuntimeError):
                hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.health()['success'])
        self.assertNotIn('SECRET', json.dumps(self.requests))
        self.assertNotIn('SECRET', ''.join(p.read_text(encoding='utf-8') for p in self.local.iterdir() if p.suffix=='.json'))

    def test_lost_health_ack_retries_same_event_before_new_cycle(self):
        attempts = [0]
        def lost_ack(command, **kwargs):
            result = self.transport(command, **kwargs)
            attempts[0] += 1
            if attempts[0] == 1:
                raise subprocess.TimeoutExpired(command, 30)
            return result
        def next_cycle(**kwargs):
            self.assertEqual(self.health()['consecutive_errors'], 1)
            self.assertEqual(self.health()['last_seen'], NOW.isoformat())
            return {'status': 'not_due'}
        with patch.object(hermes_watch, 'STATE_DIR', self.local), \
                patch.object(hermes_watch, 'LAUNCH_CONFIG', self.config), \
                patch.object(hermes_watch, '_run', side_effect=lost_ack):
            with patch.object(hermes_watch, '_run_supervised_cycle', return_value={'status':'deferred'}):
                with self.assertRaises(subprocess.TimeoutExpired):
                    hermes_watch._run_supervised_watch(now=NOW)
            with patch.object(hermes_watch, '_run_supervised_cycle', side_effect=next_cycle):
                hermes_watch._run_supervised_watch(now=NOW+timedelta(seconds=60))
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertEqual(self.health()['consecutive_errors'], 0)
        self.assertEqual(self.health()['last_seen'], (NOW+timedelta(seconds=60)).isoformat())

    def test_absent_or_false_launch_flag_preserves_cycle_and_no_health_side_effects(self):
        before = set(self.local.iterdir())
        for config in (None, {}, {'paper_safety_enabled': False}):
            with self.subTest(config=config), patch.object(hermes_watch, 'STATE_DIR', self.local), \
                    patch.object(hermes_watch, 'LAUNCH_CONFIG', config), \
                    patch.object(hermes_watch, '_run_supervised_cycle', return_value={'status':'not_due'}), \
                    patch.object(hermes_watch, '_run', side_effect=AssertionError('disabled feed must not use transport')):
                self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['status'], 'not_due')
            self.assertEqual(set(self.local.iterdir()), before)
        self.assertFalse((self.root/'paper_safety_state.json').exists())

    def test_launched_remote_python_uses_project_venv_and_legacy_stays_unchanged(self):
        for config, python in ((None, 'python'), (self.config, '/app/.venv/bin/python')):
            with self.subTest(config=config), patch.object(hermes_watch, 'LAUNCH_CONFIG', config), \
                    patch.object(hermes_watch, '_run', return_value=subprocess.CompletedProcess([],0,'{}','')) as run:
                hermes_watch._remote_snapshot()
                hermes_watch._publish_remote({})
                hermes_watch._install_remote_usage({})
                hermes_watch._install_remote_access({})
            for call in run.call_args_list:
                self.assertEqual(shlex.split(call.args[0][-1])[0], python)


if __name__ == '__main__':
    unittest.main()
