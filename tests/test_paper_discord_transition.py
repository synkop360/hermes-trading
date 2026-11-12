"""The reporting successor has a separate, exact four-module admission."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import unittest

from test_paper_release_transition import api, release_plan, closed_opening
from release_transition_fixtures import NativeTransitionFixture
from hermes_trading.reflection_supervision import _hash


BASE = '49c851315b98bb33b1eb78180fb789afe2a49f3d'
FILES = {'notifications.py','paired_paper.py','paired_worker.py','paper_boot.py'}


def reporting_plan():
    plan = release_plan(); plan['old_commit'] = BASE
    plan['old'] = deepcopy(plan['new'])
    for name in FILES:plan['old']['runtime'][name] = hashlib.sha256(('synthetic-old-'+name).encode()).hexdigest()
    return plan


class ReportingReleaseValidationTests(unittest.TestCase):
    def test_exact_reporting_delta_is_admitted_from_installed_successor_only(self):
        plan = reporting_plan(); before = deepcopy(plan)
        api().validate_release_plan(plan)
        self.assertEqual(plan, before)

    def test_reporting_admission_does_not_expand_backup_or_other_runtime_permission(self):
        for case in ('missing','extra','backup','watcher','launcher','base','noop'):
            plan = reporting_plan()
            if case == 'missing':plan['new']['runtime']['paper_boot.py'] = plan['old']['runtime']['paper_boot.py']
            elif case == 'extra':plan['new']['runtime']['positions.py'] = 'f'*64
            elif case == 'backup':plan['new']['runtime']['archive_snapshot.py'] = 'f'*64
            elif case == 'watcher':plan['new']['watcher'] = 'f'*64
            elif case == 'launcher':plan['new']['native_launcher'] = 'f'*64
            elif case == 'base':plan['old_commit'] = '6c9d520da5f8473b51c912d1eb0e269ee94d4b1b'
            else:plan['new_commit'] = BASE
            with self.subTest(case=case),self.assertRaises(ValueError):api().validate_release_plan(plan)


class ReportingTransitionFixture(NativeTransitionFixture):
    @staticmethod
    def _releases():
        plan = reporting_plan()
        from scripts.hermes_watch import _supervisor_release
        # Actual native watcher/launcher hashes; only the four runtime modules differ.
        new = _supervisor_release(); old = deepcopy(new)
        for name in FILES:old['runtime'][name] = plan['old']['runtime'][name]
        return old,new


class ReportingClosedCopyTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        f = await ReportingTransitionFixture.create(self)
        f.release_plan.update(old_commit=BASE,new_commit='d'*40)
        return f

    async def test_exact_both_host_transition_preserves_original_state_and_retries(self):
        f = await self.fixture(); module = api()
        options = dict(worker_root=f.worker_root,supervisor_root=f.supervisor_root,
                       launch_file=f.launch_file,audit_dir=f.audit_dir)
        plan = module.prepare_plan(f.worker_root,f.supervisor_root,f.launch_file,
            release_plan=f.release_plan,expected_identity=f.expected_identity)
        self.assertEqual(plan['purpose'],'paper_discord_release_transition_v1')
        bad = deepcopy(plan); bad['purpose'] = module.PURPOSE
        bad['transition_id'] = _hash({k:v for k,v in bad.items() if k!='transition_id'})
        with self.assertRaises(ValueError):module.apply_transition(bad,**options,apply=True,approval_ref='synthetic')
        self.assertFalse(f.audit_dir.exists())
        before = module._protected(module._layout(f.worker_root,f.supervisor_root,f.launch_file))
        result = module.apply_transition(plan,**options,apply=True,approval_ref='synthetic-local-only')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(module.apply_transition(plan,**options,apply=True,approval_ref='synthetic-local-only'),result)
        self.assertEqual(module._protected(module._layout(f.worker_root,f.supervisor_root,f.launch_file)),before)
        self.assertEqual(closed_opening(f.worker_root),f.opening)
        self.assertTrue(all(c['state']=='after' for c in module.inspect_transition(plan,**options)['components']))
        f.at += timedelta(seconds=1)
        f.renew_access(f.new_release)
        with self.assertRaises(ValueError):
            module.rollback_transition(plan,**options,apply=True,approval_ref='synthetic-must-refuse-after-work')


if __name__ == '__main__':unittest.main()
