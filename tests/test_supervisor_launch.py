from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest

from scripts import hermes_watch
from hermes_trading.reflection_supervision import _hash


class SupervisorLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.now=datetime.now(timezone.utc)
        self.state=self.root/'state/paired-supervisor/test/adaptive_100';self.state.mkdir(parents=True)
        self.policy={'schema_version':1,'owner':'external_llm','experiment_id':'test','cadence_closed_trades':5,
                     'one_variable_only':True,'wallet_required':True,'model_access_renewal_required':True,
                     'supervisor_lifecycle_required':True,'model':{'provider':'nous','name':'upstage/solar-mini4:free'}}
        self.account={'schema_version':1,'experiment_id':'test','currency':'EUR','starting_balance':'100','activated_at':self.now.isoformat()}
        (self.state/'reflection_owner.json').write_text(json.dumps(self.policy))
        (self.state/'wallet_account.json').write_text(json.dumps(self.account))
        self.limits={'schema_version':1,'experiment_id':'test','policy_hash':_hash(self.policy),'activated_at':self.now.isoformat(),
                     'max_cycles':10,'max_runtime_seconds':600,'interval_seconds':60,'max_model_attempts':1,
                     'release_hash':_hash(hermes_watch._supervisor_release())}
        (self.state/'supervisor_limits.json').write_text(json.dumps(self.limits))
        self.config={'schema_version':1,'status':'activated','enabled':True,'experiment_id':'test','project_dir':str(self.root),
                     'local_state_dir':str(self.state),'mirror_dir':str(self.state.parent/'mirror'),
                     'remote_state_dir':'/app/state/adaptive_100','policy_hash':_hash(self.policy),
                     'account_hash':_hash(self.account),'release_hash':_hash(hermes_watch._supervisor_release()),
                     'expires_at':(self.now+timedelta(seconds=600)).isoformat(),'max_script_seconds':240}
        self.path=self.root/'launch.json';self.write()
    def tearDown(self):self.tmp.cleanup()
    def write(self):self.path.write_text(json.dumps(self.config))
    def validate(self):
        from hermes_trading.supervisor_launch import validate_launch
        return validate_launch(self.path,self.root,hermes_watch._supervisor_release())
    def test_bound_launch_and_scoped_primary_paths(self):
        self.assertEqual(self.validate()['local_state_dir'],self.state)
        previous=(hermes_watch.STATE_DIR,hermes_watch.REMOTE_STATE_DIR,hermes_watch.MIRROR_DIR)
        with patch.object(hermes_watch,'PROJECT_DIR',self.root):
            with hermes_watch.supervised_launch(self.path):
                self.assertEqual(hermes_watch.STATE_DIR,self.state)
                self.assertEqual(hermes_watch.REMOTE_STATE_DIR,'/app/state/adaptive_100')
        self.assertEqual((hermes_watch.STATE_DIR,hermes_watch.REMOTE_STATE_DIR,hermes_watch.MIRROR_DIR),previous)

    def test_main_inherits_deadline_and_honors_shorter_launcher_cap(self):
        from hermes_trading.supervisor_budget import active_budget
        import os,time
        self.config['max_script_seconds']=90;self.write()
        start=time.monotonic();observed=[]
        def main():observed.append(active_budget().deadline);return 0
        with patch.object(hermes_watch,'PROJECT_DIR',self.root),patch.object(hermes_watch,'_main',side_effect=main), \
                patch.dict(os.environ,{'HERMES_TRADING_INVOCATION_DEADLINE':str(start+120)}):
            self.assertEqual(hermes_watch.main(['--launch-file',str(self.path)]),0)
        self.assertTrue(start+84<observed[0]<start+86)
        self.assertIsNone(active_budget())

    def test_nonfinite_inherited_deadline_never_runs_watcher(self):
        import os
        for value in ('nan','inf','-inf'):
            with patch.object(hermes_watch,'_main') as main,patch.dict(os.environ,{'HERMES_TRADING_INVOCATION_DEADLINE':value}):
                with self.subTest(value=value),self.assertRaises(ValueError):hermes_watch.main(['--launch-file',str(self.path)])
                main.assert_not_called()
    def test_invalid_binding_paths_draft_and_expiry_rejected(self):
        for key,value in [('local_state_dir',str(self.root/'state')),('remote_state_dir','/app/state'),
                          ('mirror_dir',str(self.root/'state')),('policy_hash','wrong'),('account_hash','wrong'),
                          ('release_hash','wrong'),('status','prepared_draft_not_activated'),('expires_at',self.now.isoformat())]:
            original=self.config[key];self.config[key]=value;self.write()
            with self.subTest(key=key),self.assertRaises(ValueError):self.validate()
            self.config[key]=original
    def test_global_maintenance_blocks_new_primary_state(self):
        (self.root/'state/watcher_maintenance.json').write_text('{}')
        with patch.object(hermes_watch,'PROJECT_DIR',self.root),patch.object(hermes_watch,'_load_dotenv') as dotenv, \
                patch.object(hermes_watch,'_run_supervised_watch') as run:
            self.assertEqual(hermes_watch.main(['--launch-file',str(self.path)]),0)
        dotenv.assert_not_called();run.assert_not_called()
        self.assertFalse((self.state/'watcher_cycle.sqlite').exists())
    def test_all_remote_transports_target_primary_profile(self):
        import subprocess
        with patch.object(hermes_watch,'PROJECT_DIR',self.root),hermes_watch.supervised_launch(self.path), \
                patch.object(hermes_watch,'_run',return_value=subprocess.CompletedProcess([],0,'{}','')) as run:
            hermes_watch._remote_snapshot();hermes_watch._publish_remote({});hermes_watch._install_remote_usage({});hermes_watch._install_remote_access({})
        self.assertTrue(all('--state-dir /app/state/adaptive_100' in call.args[0][-1] for call in run.call_args_list))

    def test_launched_owner_disappearance_cannot_fall_back_to_legacy_writer(self):
        with patch.object(hermes_watch,'PROJECT_DIR',self.root),hermes_watch.supervised_launch(self.path), \
                patch.object(hermes_watch,'_load_dotenv'),patch.object(hermes_watch,'_download_remote_file') as download, \
                patch.object(hermes_watch,'_run_reflection') as reflection,patch.object(hermes_watch,'_upload_remote_file') as upload:
            (self.state/'reflection_owner.json').unlink()
            with self.assertRaises(ValueError):hermes_watch._main()
        download.assert_not_called();reflection.assert_not_called();upload.assert_not_called()
