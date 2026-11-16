"""Synthetic chained lot 1 source update; never connects to Discord or Railway."""
from copy import deepcopy
import json,unittest,sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from scripts import paper_release_transition as transition
from scripts import paper_cloud_update as update
from hermes_trading.reflection_supervision import _hash
import test_paper_pilot_source_update as pilot_tests

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


def plans():
    return tuple(json.loads((SCRIPTS/name).read_text()) for name in
        ('discord_format_release_plan.json','pilot_release_plan.json','lot1_release_plan.json'))


class Lot1ValidationTests(unittest.TestCase):
    def test_lot1_is_chained_after_the_frozen_pilot_release(self):
        _,pilot,lot1=plans();transition.validate_release_plan(lot1)
        self.assertEqual(lot1['old_commit'],pilot['new_commit'])
        self.assertEqual(lot1['old_commit'],transition.LOT1_BASE_COMMIT)
        self.assertEqual(lot1['old'],pilot['new'])
        self.assertEqual(lot1['target'],pilot['target'])
        self.assertEqual(_hash(lot1['old']),transition.LOT1_BASE_RELEASE)
        self.assertEqual(_hash(lot1['old']['runtime']),transition.LOT1_BASE_RUNTIME)
        self.assertEqual(transition._purpose(lot1),'paper_lot1_release_transition_v1')
        from scripts.hermes_watch import _supervisor_release
        self.assertEqual(_supervisor_release(),lot1['new'])
        self.assertEqual(set(lot1['new']['runtime'])-set(lot1['old']['runtime']),transition.LOT1_ADDED_MODULES)
        changed={name for name in lot1['old']['runtime'] if lot1['old']['runtime'][name]!=lot1['new']['runtime'][name]}
        self.assertEqual(changed,transition.LOT1_MODULES)

    def test_exact_added_module_and_worker_delta(self):
        *_,plan=plans()
        for case in ('source','runtime','launcher','watcher','noop','extra','missing','removed','pilot_module'):
            bad=deepcopy(plan)
            if case=='source':bad['old']['runtime']['notifications.py']='0'*64
            if case=='runtime':bad['new']['runtime']['reflect.py']='0'*64
            if case=='launcher':bad['new']['native_launcher']='0'*64
            if case=='watcher':bad['new']['watcher']='0'*64
            if case=='noop':bad['new']['runtime']['paired_worker.py']=bad['old']['runtime']['paired_worker.py']
            if case=='extra':bad['new']['runtime']['unreviewed.py']='0'*64
            if case=='missing':del bad['new']['runtime']['paper_history_retention.py']
            if case=='removed':del bad['new']['runtime']['pilot_paper.py']
            if case=='pilot_module':bad['old']['runtime'].pop('pilot_paper.py')
            with self.subTest(case=case),self.assertRaises(ValueError):transition.validate_release_plan(bad)

    def test_pilot_base_still_refuses_the_lot1_module(self):
        _,pilot,_=plans();bad=deepcopy(pilot);bad['new']['runtime']['paper_history_retention.py']='0'*64
        with self.assertRaisesRegex(ValueError,'inventory'):transition.validate_release_plan(bad)

    def test_lot1_step_has_its_own_audit_and_approval(self):
        name,approval=update.STEPS[transition.LOT1_BASE_COMMIT]
        self.assertEqual(name,'lot1-source')
        self.assertNotIn(approval,{ref for key,(_,ref) in update.STEPS.items() if key!=transition.LOT1_BASE_COMMIT})


class InstallDefaultChainTests(unittest.TestCase):
    def test_install_default_applies_the_three_steps_in_order(self):
        from scripts import paper_cloud_service as service
        discord,pilot,lot1=plans()
        project=SCRIPTS.parent
        with patch('scripts.paper_cloud_update.update_installed',return_value={}) as step, \
                patch.object(service,'install',return_value={'status':'already_installed'}) as install, \
                patch('builtins.print'):
            service.install_default(project)
        self.assertEqual([(c.args[1],c.kwargs.get('successor')) for c in step.call_args_list],
            [(discord,pilot),(pilot,lot1),(lot1,None)])
        self.assertEqual(install.call_args.args[1],lot1)


class Lot1ChainedUpdateTests(unittest.IsolatedAsyncioTestCase):
    fixture=pilot_tests.PilotChainedUpdateTests.fixture

    async def test_full_chain_from_notification_predecessor_preserves_worker_state(self):
        discord,_,_=plans()
        f,root,discord,pilot,env=await self.fixture(discord['old'],discord['new'])
        lot1=plans()[2];lot1['target']['experiment_id']='paired-test'
        account=(root/'adaptive_200/wallet_account.json').read_bytes()
        def worker_state():
            with closing(sqlite3.connect(root/'worker_runtime.sqlite')) as db:return db.execute('SELECT state FROM lifecycle').fetchall()
        before=worker_state()
        with patch.dict(update.os.environ,env):
            self.assertEqual(update.update_installed(f.project_root,discord,successor=pilot)['status'],'updated')
            self.assertEqual(update.update_installed(f.project_root,pilot,successor=lot1)['status'],'updated')
            self.assertEqual(update.update_installed(f.project_root,lot1)['status'],'updated')
        self.assertEqual((root/'adaptive_200/wallet_account.json').read_bytes(),account)
        self.assertEqual(worker_state(),before)
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(lot1['new']))
        for name in ('discord-format','pilot-source','lot1-source'):
            self.assertTrue((root/'.paper-cloud-cutover'/name/'receipt.json').is_file())
        from scripts.paper_cloud_install import install
        with patch.dict(update.os.environ,env):
            self.assertEqual(install(f.project_root,lot1,env=env,experiment_id='paired-test')['status'],'already_installed')
            self.assertEqual(update.update_installed(f.project_root,discord,successor=pilot)['status'],'superseded')
            self.assertEqual(update.update_installed(f.project_root,pilot,successor=lot1)['status'],'superseded')
            self.assertEqual(update.update_installed(f.project_root,lot1)['status'],'already_updated')
            # Without its successor, the pilot step refuses the newer installation.
            with self.assertRaisesRegex(ValueError,'predecessor differs'):update.update_installed(f.project_root,pilot)

    async def test_interrupted_lot1_step_reconciles_under_original_identity(self):
        _,pilot,lot1=plans()
        f,root,_,_,env=await self.fixture(lot1['old'],lot1['new'])
        lot1['target']['experiment_id']='paired-test'
        def fail(stage,name):
            if stage=='component_commit':raise RuntimeError('synthetic interruption')
        with patch.dict(update.os.environ,env),self.assertRaises(RuntimeError):update.update_installed(f.project_root,lot1,fault=fail)
        with patch.dict(update.os.environ,env):self.assertEqual(update.update_installed(f.project_root,lot1)['status'],'updated')
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(lot1['new']))
        self.assertEqual(receipt['source_update'],json.loads((root/'.paper-cloud-cutover/lot1-source/intent.json').read_bytes())['plan']['transition_id'])


if __name__=='__main__':unittest.main()
