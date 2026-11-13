"""Synthetic chained pilot source update; never connects to Discord or Railway."""
from copy import deepcopy
import json,shutil,tempfile,unittest,sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from scripts import paper_release_transition as transition
from scripts import paper_cloud_update as update
from scripts import paper_storage_recovery as recovery
import release_transition_fixtures as fixtures
from hermes_trading.reflection_supervision import _hash

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'


def plans():
    return (json.loads((SCRIPTS/'discord_format_release_plan.json').read_text()),
            json.loads((SCRIPTS/'pilot_release_plan.json').read_text()))


class PilotValidationTests(unittest.TestCase):
    def test_pilot_is_chained_after_the_installed_discord_release(self):
        discord,pilot=plans();transition.validate_release_plan(pilot)
        self.assertEqual(pilot['old_commit'],discord['new_commit'])
        self.assertEqual(pilot['old'],discord['new'])
        self.assertEqual(pilot['target'],discord['target'])
        self.assertEqual(transition._purpose(pilot),'paper_pilot_release_transition_v1')
        from scripts.hermes_watch import _supervisor_release
        self.assertEqual(_supervisor_release(),pilot['new'])
        self.assertEqual(set(pilot['new']['runtime'])-set(pilot['old']['runtime']),transition.PILOT_ADDED_MODULES)

    def test_exact_added_modules_and_reflect_delta(self):
        _,plan=plans()
        for case in ('source','runtime','launcher','watcher','noop','extra','missing','removed'):
            bad=deepcopy(plan)
            if case=='source':bad['old']['runtime']['notifications.py']='0'*64
            if case=='runtime':bad['new']['runtime']['paired_worker.py']='0'*64
            if case=='launcher':bad['new']['native_launcher']='0'*64
            if case=='watcher':bad['new']['watcher']='0'*64
            if case=='noop':bad['new']['runtime']['reflect.py']=bad['old']['runtime']['reflect.py']
            if case=='extra':bad['new']['runtime']['unreviewed.py']='0'*64
            if case=='missing':del bad['new']['runtime']['pilot_paper.py']
            if case=='removed':del bad['new']['runtime']['notifications.py']
            with self.subTest(case=case),self.assertRaises(ValueError):transition.validate_release_plan(bad)

    def test_other_bases_still_refuse_any_added_module(self):
        discord,_=plans();bad=deepcopy(discord);bad['new']['runtime']['pilot_paper.py']='0'*64
        with self.assertRaisesRegex(ValueError,'inventory'):transition.validate_release_plan(bad)

    def test_each_step_has_its_own_audit_and_approval(self):
        self.assertEqual(set(update.STEPS),{transition.DISCORD_FORMAT_BASE_COMMIT,transition.PILOT_BASE_COMMIT})
        self.assertEqual(len({name for name,_ in update.STEPS.values()}),2)
        self.assertEqual(len({ref for _,ref in update.STEPS.values()}),2)


class PredecessorRecoveryStatusTests(unittest.TestCase):
    def test_absent_and_pending_predecessor_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            options=dict(approval_ref='approval',source_runtime='0'*64,recovery_tag='after-test')
            self.assertEqual(recovery.recovery_status(root,**options),'absent')
            audit=root/'.paper-storage-recovery-after-test';audit.mkdir();(audit/'intent.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'reconciliation'):recovery.recovery_status(root,**options)


class PilotChainedUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self,old,new):
        discord,pilot=plans()
        for plan in (discord,pilot):plan['target']['experiment_id']='paired-test'
        with patch.object(fixtures,'OLD_COMMIT',discord['old_commit'] if old==discord['old'] else pilot['old_commit']), \
                patch.object(fixtures.NativeTransitionFixture,'_releases',return_value=(old,new)):
            f=await fixtures.NativeTransitionFixture.create(self)
        root=f.project_root/'state';shutil.copytree(f.worker_root,root,dirs_exist_ok=True)
        scripts=f.project_root/'scripts';scripts.mkdir(exist_ok=True)
        for name in ('paper_local_transport.py','paper_supervisor_service.py'):
            shutil.copyfile(Path(transition.__file__).with_name(name),scripts/name)
        launch=root/'cloud-supervisor-launch.json';shutil.copyfile(f.launch_file,launch)
        aux=root/'.paper-cloud-cutover';aux.mkdir()
        owner=dict(schema_version=1,purpose='paper_cloud_auxiliary_v1',worker_root=str(root),policy_hash=_hash(f.policy),account_hash=_hash(f.account))
        (aux/'owner.json').write_text(json.dumps(owner))
        (aux/'installed.json').write_text(json.dumps(dict(status='installed',release_hash=_hash(old),experiment_id='paired-test')))
        env=dict(fixtures.PAPER_ENV,**{'RAILWAY_'+k.upper()+'_ID':v for k,v in transition.TARGET.items()})
        return f,root,discord,pilot,env

    async def test_chain_from_notification_predecessor_preserves_worker_state(self):
        discord,_=plans()
        f,root,discord,pilot,env=await self.fixture(discord['old'],discord['new'])
        account=(root/'adaptive_200/wallet_account.json').read_bytes()
        def worker_state():
            with closing(sqlite3.connect(root/'worker_runtime.sqlite')) as db:return db.execute('SELECT state FROM lifecycle').fetchall()
        before=worker_state()
        with patch.dict(update.os.environ,env):
            self.assertEqual(update.update_installed(f.project_root,discord,successor=pilot)['status'],'updated')
            self.assertEqual(update.update_installed(f.project_root,pilot)['status'],'updated')
        self.assertEqual((root/'adaptive_200/wallet_account.json').read_bytes(),account)
        self.assertEqual(worker_state(),before)
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(pilot['new']))
        self.assertTrue((root/'.paper-cloud-cutover/discord-format/receipt.json').is_file())
        self.assertTrue((root/'.paper-cloud-cutover/pilot-source/receipt.json').is_file())
        from scripts.paper_cloud_install import install
        with patch.dict(update.os.environ,env):
            self.assertEqual(install(f.project_root,pilot,env=env,experiment_id='paired-test')['status'],'already_installed')
            self.assertEqual(update.update_installed(f.project_root,discord,successor=pilot)['status'],'superseded')
            self.assertEqual(update.update_installed(f.project_root,pilot)['status'],'already_updated')
            # Without its successor, the notification step refuses the newer installation.
            with self.assertRaisesRegex(ValueError,'predecessor differs'):update.update_installed(f.project_root,discord)

    async def test_interrupted_pilot_step_reconciles_under_original_identity(self):
        _,pilot=plans()
        f,root,discord,pilot,env=await self.fixture(pilot['old'],pilot['new'])
        def fail(stage,name):
            if stage=='component_commit':raise RuntimeError('synthetic interruption')
        with patch.dict(update.os.environ,env),self.assertRaises(RuntimeError):update.update_installed(f.project_root,pilot,fault=fail)
        with patch.dict(update.os.environ,env):self.assertEqual(update.update_installed(f.project_root,pilot)['status'],'updated')
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(pilot['new']))
        self.assertEqual(receipt['source_update'],json.loads((root/'.paper-cloud-cutover/pilot-source/intent.json').read_bytes())['plan']['transition_id'])


if __name__=='__main__':unittest.main()
