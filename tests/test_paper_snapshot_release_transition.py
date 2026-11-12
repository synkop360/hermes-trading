"""Snapshot retry admission is pinned to the deployed reporting predecessor."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_paper_release_transition_cross_host as cross_host
from release_transition_fixtures import NativeTransitionFixture
from test_paper_release_transition import api, closed_opening, TARGET
from hermes_trading.reflection_supervision import _hash

BASE = 'ed439e897a4dd388fdcf6425e7e8b6171d5a88a9'
PURPOSE = 'paper_snapshot_busy_release_transition_v1'


def snapshot_plan():
    path = Path(__file__).resolve().parents[1]/'research/runs/paper-discord-reporting-v1/live-cutover/frozen-live-transition-plan.json'
    old = json.loads(path.read_text(encoding='utf-8'))['release_plan']['new']
    new = deepcopy(old)
    new['runtime']['supervisor_sync.py'] = 'f'*64
    return dict(old_commit=BASE, new_commit='d'*40, target=deepcopy(TARGET), old=old, new=new)


class SnapshotReleaseValidationTests(unittest.TestCase):
    def test_exact_deployed_predecessor_and_one_snapshot_delta_is_admitted(self):
        plan = snapshot_plan()
        original = deepcopy(plan)
        try:
            api().validate_release_plan(plan)
        except ValueError as error:
            self.fail(f'approved snapshot predecessor is not admitted: {error}')
        self.assertEqual(plan, original)
        self.assertEqual(api()._purpose(plan), PURPOSE)

    def test_foreign_old_map_with_same_one_file_delta_is_rejected(self):
        for case in ('preserved_runtime', 'watcher', 'launcher'):
            plan = snapshot_plan()
            if case == 'preserved_runtime':
                for side in ('old','new'):plan[side]['runtime']['positions.py'] = '0'*64
            else:
                key = 'watcher' if case=='watcher' else 'native_launcher'
                for side in ('old','new'):plan[side][key] = '0'*64
            with self.subTest(case=case), self.assertRaises(ValueError):
                api().validate_release_plan(plan)

    def test_other_delta_inventory_base_watcher_and_native_changes_reject(self):
        for case in ('extra','missing','inventory','base','watcher','native','old_purpose_base'):
            plan = snapshot_plan()
            if case == 'extra':plan['new']['runtime']['positions.py'] = '0'*64
            elif case == 'missing':plan['new'] = deepcopy(plan['old'])
            elif case == 'inventory':plan['new']['runtime']['foreign.py'] = '0'*64
            elif case == 'base':plan['old_commit'] = '1'*40
            elif case == 'watcher':plan['new']['watcher'] = '0'*64
            elif case == 'native':plan['new']['native_launcher'] = '0'*64
            else:plan['old_commit'] = '49c851315b98bb33b1eb78180fb789afe2a49f3d'
            with self.subTest(case=case), self.assertRaises(ValueError):
                api().validate_release_plan(plan)


class SnapshotTransitionFixture(NativeTransitionFixture):
    seed_latch = True
    @staticmethod
    def _releases():
        plan = snapshot_plan()
        return plan['old'], plan['new']

    def _configure(self):
        super()._configure()
        config = {'schema_version':1, 'experiment_id':'paired-test', 'volume_root':str(self.worker_root.resolve()),
                  'capacity_bytes':500000000, 'alert_fraction':'0.70', 'max_worker_errors':3,
                  'max_supervisor_errors':3, 'supervisor_stale_seconds':300,
                  'diagnostics_max_bytes':1048576, 'diagnostics_generations':3}
        (self.worker_root/'paper_safety.json').write_text(json.dumps(config))

    @classmethod
    async def create(cls, testcase):
        fixture = await super().create(testcase)
        fixture.release_plan.update(old_commit=BASE, new_commit='d'*40)
        if not cls.seed_latch:
            return fixture
        from hermes_trading.paper_safety import record_supervisor_result
        with fixture.release_context(fixture.old_release), \
                patch('hermes_trading.paper_safety._utc_now',side_effect=lambda:fixture.at):
            for index in range(3):
                fixture.at += timedelta(seconds=1)
                record_supervisor_result(fixture.worker_root, success=False, error_kind='supervisor_exception',
                                         event_id=f'synthetic-error-{index}', now=fixture.at)
        latch = json.loads((fixture.worker_root/'paper_safety_state.json').read_text())
        testcase.assertTrue(latch['supervisor']['entry_halt'])
        return fixture


class UnlatchedSnapshotTransitionFixture(SnapshotTransitionFixture):
    # The reused baseline scenario explicitly verifies missing health, not a latch.
    seed_latch = False


class SnapshotClosedCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_purpose_preserves_original_evidence_and_resumes_only_checked_partial_transition(self):
        f = await SnapshotTransitionFixture.create(self)
        module = api()
        options = dict(worker_root=f.worker_root, supervisor_root=f.supervisor_root,
                       launch_file=f.launch_file, audit_dir=f.audit_dir)
        plan = module.prepare_plan(f.worker_root, f.supervisor_root, f.launch_file,
            release_plan=f.release_plan, expected_identity=f.expected_identity)
        self.assertEqual(plan['purpose'], PURPOSE)
        before = module._protected(module._layout(f.worker_root,f.supervisor_root,f.launch_file))
        bad = deepcopy(plan)
        bad['purpose'] = module.REPORTING_PURPOSE
        bad['transition_id'] = _hash({k:v for k,v in bad.items() if k!='transition_id'})
        with self.assertRaises(ValueError):
            module.apply_transition(bad, **options, apply=True, approval_ref='synthetic-only')
        self.assertFalse(f.audit_dir.exists())
        interrupted = []
        def crash(point, component):
            if point=='component_commit' and not interrupted:
                interrupted.append(component)
                raise OSError('synthetic lost receipt')
        with self.assertRaises(OSError):
            module.apply_transition(plan, **options, apply=True, approval_ref='synthetic-only', fault=crash)
        with self.assertRaises(ValueError):
            module.apply_transition(plan, **options, apply=True, approval_ref='synthetic-only')
        result = module.apply_transition(plan, **options, apply=True, reconcile=True, approval_ref='synthetic-only')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(module.apply_transition(plan, **options, apply=True, approval_ref='synthetic-only'), result)
        self.assertEqual(module._protected(module._layout(f.worker_root,f.supervisor_root,f.launch_file)), before)
        self.assertEqual(closed_opening(f.worker_root), f.opening)
        self.assertEqual(len(module.inspect_transition(plan, **options)['components']), 6)
        f.at += timedelta(seconds=1)
        f.renew_access(f.new_release)
        with self.assertRaises(ValueError):
            module.rollback_transition(plan, **options, apply=True, approval_ref='synthetic-refuse-after-work')


class SnapshotTwoHostTests(cross_host.CrossHostTransitionTests):
    async def test_two_host_views_require_two_scoped_receipts_and_actual_native_checks(self):
        # Reuse the full native two-host scenario with the new pinned purpose.
        with patch.object(cross_host, 'NativeTransitionFixture', UnlatchedSnapshotTransitionFixture):
            await super().test_two_host_views_require_two_scoped_receipts_and_actual_native_checks()


if __name__ == '__main__':
    unittest.main()
