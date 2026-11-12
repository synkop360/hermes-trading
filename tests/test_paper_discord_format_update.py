"""Synthetic source binding update; never connects to Discord or Railway."""
from copy import deepcopy
import json,shutil,unittest,sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from scripts import paper_release_transition as transition
from scripts import paper_cloud_update as update
import release_transition_fixtures as fixtures
from hermes_trading.reflection_supervision import _hash


def source_plan():
    return json.loads((Path(__file__).resolve().parents[1]/'scripts/discord_format_release_plan.json').read_text())


class DiscordFormatValidationTests(unittest.TestCase):
    def test_exact_single_notification_module_delta(self):
        plan=source_plan();transition.validate_release_plan(plan)
        for case in ('source','runtime','launcher','noop'):
            bad=deepcopy(plan)
            if case=='source':bad['old']['runtime']['notifications.py']='0'*64
            if case=='runtime':bad['new']['runtime']['paired_worker.py']='0'*64
            if case=='launcher':bad['new']['native_launcher']='0'*64
            if case=='noop':bad['new']=deepcopy(bad['old'])
            with self.subTest(case=case),self.assertRaises(ValueError):transition.validate_release_plan(bad)


class DiscordFormatUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        plan=source_plan();plan['target']['experiment_id']='paired-test'
        with patch.object(fixtures,'OLD_COMMIT',plan['old_commit']),patch.object(fixtures.NativeTransitionFixture,'_releases',return_value=(plan['old'],plan['new'])):
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
        return f,root,plan,env

    async def test_update_preserves_worker_state_and_replays_after_new_supervisor_work(self):
        f,root,plan,env=await self.fixture()
        account=(root/'adaptive_200/wallet_account.json').read_bytes()
        def worker_state():
            with closing(sqlite3.connect(root/'worker_runtime.sqlite')) as db:return db.execute('SELECT state FROM lifecycle').fetchall()
        before=worker_state()
        with patch.dict(update.os.environ,env):
            result=update.update_installed(f.project_root,plan)
        self.assertEqual(result['status'],'updated')
        self.assertEqual((root/'adaptive_200/wallet_account.json').read_bytes(),account)
        self.assertEqual(worker_state(),before)
        from scripts.paper_cloud_install import install
        with patch.dict(update.os.environ,env):
            self.assertEqual(install(f.project_root,plan,env=env,experiment_id='paired-test')['status'],'already_installed')
        life=root/'paired-supervisor/paired-test/adaptive_200/supervisor_lifecycle.json'
        body=json.loads(life.read_bytes());body['test_new_observation']=True;life.write_text(json.dumps(body))
        with patch.dict(update.os.environ,env):self.assertEqual(update.update_installed(f.project_root,plan)['status'],'already_updated')
        self.assertTrue(json.loads(life.read_bytes())['test_new_observation'])

    async def test_interrupted_update_reconciles_under_original_identity(self):
        f,root,plan,env=await self.fixture()
        def fail(stage,name):
            if stage=='component_commit':raise RuntimeError('synthetic interruption')
        with patch.dict(update.os.environ,env),self.assertRaises(RuntimeError):update.update_installed(f.project_root,plan,fault=fail)
        with patch.dict(update.os.environ,env):self.assertEqual(update.update_installed(f.project_root,plan)['status'],'updated')
        receipt=json.loads((root/'.paper-cloud-cutover/installed.json').read_bytes())
        self.assertEqual(receipt['release_hash'],_hash(plan['new']))
