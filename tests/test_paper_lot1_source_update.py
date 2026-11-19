"""Synthetic lot 1 source update chained after the installed backup restart
release; never connects to Discord or Railway."""
from copy import deepcopy
import json,shutil,unittest,sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from scripts import paper_release_transition as transition
from scripts import paper_cloud_update as update
import release_transition_fixtures as fixtures
from hermes_trading.reflection_supervision import _hash

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


def plans():
    return tuple(json.loads((SCRIPTS/name).read_text()) for name in
        ('storage_limit_release_plan.json','backup_restart_release_plan.json',
         'pilot_release_plan.json','lot1_release_plan.json'))


class Lot1ValidationTests(unittest.TestCase):
    def test_lot1_is_chained_after_the_installed_backup_restart_release(self):
        _,backup,pilot,lot1=plans();transition.validate_release_plan(lot1)
        self.assertEqual(lot1['old_commit'],backup['new_commit'])
        self.assertEqual(lot1['old_commit'],transition.LOT1_BASE_COMMIT)
        self.assertEqual(lot1['old'],backup['new'])
        self.assertEqual(lot1['target'],backup['target'])
        self.assertEqual(_hash(lot1['old']),transition.LOT1_BASE_RELEASE)
        self.assertEqual(_hash(lot1['old']),transition.BACKUP_RESTART_TARGET_RELEASE)
        self.assertEqual(_hash(lot1['old']['runtime']),transition.LOT1_BASE_RUNTIME)
        self.assertEqual(transition._purpose(lot1),'paper_lot1_release_transition_v1')
        from scripts.hermes_watch import _supervisor_release
        self.assertEqual(_supervisor_release(),lot1['new'])
        self.assertEqual(set(lot1['new']['runtime'])-set(lot1['old']['runtime']),transition.LOT1_ADDED_MODULES)
        changed={name for name in lot1['old']['runtime'] if lot1['old']['runtime'][name]!=lot1['new']['runtime'][name]}
        self.assertEqual(changed,transition.LOT1_MODULES)
        # The reviewed pilot delta is carried unchanged by lot 1.
        pilot_delta=transition.PILOT_ADDED_MODULES|{'reflect.py'}
        self.assertEqual({name:lot1['new']['runtime'][name] for name in pilot_delta},
                         {name:pilot['new']['runtime'][name] for name in pilot_delta})

    def test_exact_added_modules_and_reviewed_delta(self):
        *_,plan=plans()
        for case in ('source','runtime','launcher','watcher','noop','extra','missing','missing_pilot','removed','old_storage'):
            bad=deepcopy(plan)
            if case=='source':bad['old']['runtime']['notifications.py']='0'*64
            if case=='runtime':bad['new']['runtime']['paper_safety.py']='0'*64
            if case=='launcher':bad['new']['native_launcher']='0'*64
            if case=='watcher':bad['new']['watcher']='0'*64
            if case=='noop':bad['new']['runtime']['paired_worker.py']=bad['old']['runtime']['paired_worker.py']
            if case=='extra':bad['new']['runtime']['unreviewed.py']='0'*64
            if case=='missing':del bad['new']['runtime']['paper_history_retention.py']
            if case=='missing_pilot':del bad['new']['runtime']['pilot_paper.py']
            if case=='removed':del bad['new']['runtime']['notifications.py']
            if case=='old_storage':bad['old']=plans()[0]['new']
            with self.subTest(case=case),self.assertRaises(ValueError):transition.validate_release_plan(bad)

    def test_backup_restart_base_still_refuses_any_added_module(self):
        _,backup,_,_=plans();bad=deepcopy(backup);bad['new']['runtime']['paper_history_retention.py']='0'*64
        with self.assertRaisesRegex(ValueError,'inventory'):transition.validate_release_plan(bad)

    def test_lot1_step_has_its_own_audit_and_approval(self):
        name,approval=update.STEPS[transition.LOT1_BASE_COMMIT]
        self.assertEqual(name,'lot1-source')
        self.assertNotIn(approval,{ref for key,(_,ref) in update.STEPS.items() if key!=transition.LOT1_BASE_COMMIT})


class Lot1ChainedUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self,plan):
        with patch.object(fixtures,'OLD_COMMIT',plan['old_commit']), \
                patch.object(fixtures.NativeTransitionFixture,'_releases',return_value=(plan['old'],plan['new'])):
            f=await fixtures.NativeTransitionFixture.create(self)
        root=f.project_root/'state';shutil.copytree(f.worker_root,root,dirs_exist_ok=True)
        scripts=f.project_root/'scripts';scripts.mkdir(exist_ok=True)
        for name in ('paper_local_transport.py','paper_supervisor_service.py'):
            shutil.copyfile(Path(transition.__file__).with_name(name),scripts/name)
        launch=root/'cloud-supervisor-launch.json';shutil.copyfile(f.launch_file,launch)
        aux=root/'.paper-cloud-cutover';aux.mkdir()
        owner=dict(schema_version=1,purpose='paper_cloud_auxiliary_v1',worker_root=str(root),policy_hash=_hash(f.policy),account_hash=_hash(f.account))
        (aux/'owner.json').write_text(json.dumps(owner))
        (aux/'installed.json').write_text(json.dumps(dict(status='installed',release_hash=_hash(plan['old']),experiment_id='paired-test')))
        env=dict(fixtures.PAPER_ENV,**{'RAILWAY_'+k.upper()+'_ID':v for k,v in transition.TARGET.items()})
        return f,root,env

    def paired(self):
        result=plans()
        for plan in result:plan['target']['experiment_id']='paired-test'
        return result

    async def test_chain_from_storage_limit_release_preserves_worker_state(self):
        _,backup,_,lot1=self.paired()
        f,root,env=await self.fixture(backup)
        account=(root/'adaptive_200/wallet_account.json').read_bytes()
        def worker_state():
            with closing(sqlite3.connect(root/'worker_runtime.sqlite')) as db:return db.execute('SELECT state FROM lifecycle').fetchall()
        before=worker_state()
        with patch.dict(update.os.environ,env):
            self.assertEqual(update.update_installed(f.project_root,backup)['status'],'updated')
            self.assertEqual(update.update_installed(f.project_root,lot1)['status'],'updated')
        self.assertEqual((root/'adaptive_200/wallet_account.json').read_bytes(),account)
        self.assertEqual(worker_state(),before)
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(lot1['new']))
        for name in ('backup-restart','lot1-source'):
            self.assertTrue((root/'.paper-cloud-cutover'/name/'receipt.json').is_file())
        from scripts.paper_cloud_install import install
        with patch.dict(update.os.environ,env):
            self.assertEqual(install(f.project_root,lot1,env=env,experiment_id='paired-test')['status'],'already_installed')
            self.assertEqual(update.update_installed(f.project_root,backup,successor=lot1)['status'],'superseded')
            self.assertEqual(update.update_installed(f.project_root,lot1)['status'],'already_updated')
            # Without its successor, the backup restart step refuses the newer installation.
            with self.assertRaisesRegex(ValueError,'predecessor differs'):update.update_installed(f.project_root,backup)

    async def test_interrupted_lot1_step_reconciles_under_original_identity(self):
        *_,lot1=self.paired()
        f,root,env=await self.fixture(lot1)
        def fail(stage,name):
            if stage=='component_commit':raise RuntimeError('synthetic interruption')
        with patch.dict(update.os.environ,env),self.assertRaises(RuntimeError):update.update_installed(f.project_root,lot1,fault=fail)
        with patch.dict(update.os.environ,env):self.assertEqual(update.update_installed(f.project_root,lot1)['status'],'updated')
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(lot1['new']))
        self.assertEqual(receipt['source_update'],json.loads((root/'.paper-cloud-cutover/lot1-source/intent.json').read_bytes())['plan']['transition_id'])


if __name__=='__main__':unittest.main()
