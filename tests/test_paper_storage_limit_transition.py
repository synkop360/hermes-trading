import json
import os
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import release_transition_fixtures as fixtures
from hermes_trading.reflection_supervision import _hash


class StorageLimitTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        fixture = await fixtures.NativeTransitionFixture.create(self)
        root = fixture.worker_root
        old_limits = json.loads((root / 'storage_limits.json').read_text())
        old_safety = json.loads((root / 'paper_safety.json').read_text())
        old_safety_state = json.loads((root / 'paper_safety_state.json').read_text())
        self.assertEqual(old_safety['capacity_bytes'], 500000000)
        with closing(sqlite3.connect(root / 'worker_runtime.sqlite')) as db:
            before = json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
            before['storage_stop'] = True
            db.execute('UPDATE lifecycle SET state=? WHERE id=1', (json.dumps(before),))
            db.commit()
        halt = {'schema_version': 1, 'experiment_id': fixture.policy['experiment_id'],
                'policy_hash': _hash(fixture.policy), 'reason': 'storage_stop'}
        (fixture.primary / 'experiment_halt.json').write_text(json.dumps(halt))
        return fixture, before, old_limits, halt, old_safety, old_safety_state

    def transition(self, fixture, old_limits, **kwargs):
        from scripts.paper_storage_limit_transition import _transition_limit
        expected_runtime = kwargs.pop('expected_runtime', None)
        with patch.dict(os.environ, fixtures.PAPER_ENV):
            return _transition_limit(fixture.worker_root, approval_ref='user-2026-11-17-storage-limit-1gb',
                                     experiment_id=fixture.policy['experiment_id'], reserve_bytes=old_limits['reserve_bytes'],
                                     old_max=old_limits['max_state_bytes'], new_max=1000000000,
                                     protective_required=False, expected_runtime=expected_runtime, **kwargs)

    async def test_changes_only_budget_and_bound_hash_while_retaining_halt(self):
        fixture, before, old_limits, halt, old_safety, old_safety_state = await self.fixture()
        root = fixture.worker_root

        result = self.transition(fixture, old_limits)
        again = self.transition(fixture, old_limits)

        self.assertEqual(result['status'], 'transitioned')
        self.assertEqual(again['status'], 'already_transitioned')
        new_limits = json.loads((root / 'storage_limits.json').read_text())
        self.assertEqual(new_limits, dict(old_limits, max_state_bytes=1000000000))
        safety = json.loads((root / 'paper_safety.json').read_text())
        safety_state = json.loads((root / 'paper_safety_state.json').read_text())
        self.assertEqual(safety, dict(old_safety, capacity_bytes=1000000000))
        self.assertEqual(safety_state['worker'], old_safety_state['worker'])
        self.assertEqual(safety_state['supervisor'], old_safety_state['supervisor'])
        self.assertNotEqual(safety_state['policy_sha256'], old_safety_state['policy_sha256'])
        with closing(sqlite3.connect(root / 'worker_runtime.sqlite')) as db:
            after = json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        self.assertNotEqual(after['configuration_hash'], before['configuration_hash'])
        self.assertTrue(after['storage_stop'])
        for key in before:
            if key != 'configuration_hash':
                self.assertEqual(after[key], before[key])
        self.assertEqual(json.loads((fixture.primary / 'experiment_halt.json').read_text()), halt)

    async def test_each_interruption_resumes_without_clearing_halt(self):
        for stage in ('intent', 'storage_limits', 'paper_safety', 'paper_safety_state', 'lifecycle'):
            with self.subTest(stage=stage):
                fixture, before, old_limits, halt, old_safety, old_safety_state = await self.fixture()
                def interrupt(seen):
                    if seen == stage:
                        raise RuntimeError('synthetic interruption')
                with self.assertRaisesRegex(RuntimeError, 'synthetic interruption'):
                    self.transition(fixture, old_limits, fault=interrupt)
                self.assertEqual(self.transition(fixture, old_limits)['status'], 'transitioned')
                self.assertEqual(json.loads((fixture.primary / 'experiment_halt.json').read_text()), halt)

    async def test_unowned_edit_is_rejected_before_lifecycle_change(self):
        fixture, before, old_limits, halt, old_safety, old_safety_state = await self.fixture()
        path = fixture.worker_root / 'storage_limits.json'
        path.write_text(json.dumps(dict(old_limits, max_state_bytes=1000000000)))
        with self.assertRaisesRegex(ValueError, 'unowned storage transition state'):
            self.transition(fixture, old_limits)
        with closing(sqlite3.connect(fixture.worker_root / 'worker_runtime.sqlite')) as db:
            after = json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        self.assertEqual(after, before)

    async def test_native_storage_recovery_can_follow_transition(self):
        from scripts.paper_storage_recovery import recover_storage, recovery_status
        from hermes_trading.paper_publication import _release_hashes

        fixture, before, old_limits, halt, old_safety, old_safety_state = await self.fixture()
        self.transition(fixture, old_limits)
        with patch.dict(os.environ, fixtures.PAPER_ENV):
            result = recover_storage(fixture.worker_root, approval_ref='synthetic-storage-recovery',
                                     expected_runtime=_hash(_release_hashes()), recovery_tag='after-budget', now=fixture.at)
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(recovery_status(fixture.worker_root, approval_ref='synthetic-storage-recovery',
                         source_runtime=_hash(_release_hashes()), recovery_tag='after-budget')['status'], 'already_recovered')
        self.assertFalse((fixture.primary / 'experiment_halt.json').exists())
        self.assertEqual(self.transition(fixture, old_limits)['status'], 'already_transitioned')
        resumed = await fixture.advance_worker(seconds=60)
        self.assertEqual(resumed['completed_batches'], before['completed_batches'] + 1)

    async def test_wrong_runtime_is_rejected_without_intent(self):
        fixture, before, old_limits, halt, old_safety, old_safety_state = await self.fixture()
        with self.assertRaisesRegex(ValueError, 'source differs'):
            self.transition(fixture, old_limits, expected_runtime='0'*64)
        self.assertFalse((fixture.worker_root / '.paper-storage-limit-transition-2026-11-17').exists())

    async def test_changed_halt_marker_prevents_interrupted_transition_resume(self):
        fixture, before, old_limits, halt, old_safety, old_safety_state = await self.fixture()
        def interrupt(stage):
            if stage == 'intent':
                raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError):
            self.transition(fixture, old_limits, fault=interrupt)
        (fixture.primary / 'experiment_halt.json').write_text(json.dumps(dict(halt, observed_at='different')))
        with self.assertRaisesRegex(ValueError, 'halt changed'):
            self.transition(fixture, old_limits)
