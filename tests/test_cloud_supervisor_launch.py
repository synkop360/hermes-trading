"""Local mode still enters the existing bound supervisor and health journal."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock, patch
import unittest

from scripts import hermes_watch as watch, native_hermes_watch as native
from test_supervisor_launch import SupervisorLaunchTests


class CloudSupervisorLaunchTests(SupervisorLaunchTests):
    def local(self):
        self.config['transport']='local'
        self.write()
        self.worker=self.root/'state/adaptive_100'
        self.worker.mkdir()
        destination=self.root/'scripts/paper_local_transport.py'
        destination.parent.mkdir()
        shutil.copyfile(Path(watch.__file__).with_name('paper_local_transport.py'),destination)
        shutil.copyfile(Path(watch.__file__).with_name('paper_supervisor_service.py'),destination.with_name('paper_supervisor_service.py'))

    def test_local_transports_never_invoke_railway(self):
        self.local()
        env={'HERMES_TRADING_MODE':'paper','HERMES_TRADING_ALLOW_REAL_ORDERS':'false'}
        result=subprocess.CompletedProcess([],0,'{}','')
        with patch.dict(os.environ,env), patch.object(watch,'PROJECT_DIR',self.root), \
                patch('scripts.paper_local_transport._run_bounded',return_value=result) as run, \
                patch.object(watch,'_run',side_effect=AssertionError('SSH called')):
            with watch.supervised_launch(self.path):
                watch._remote_snapshot();watch._publish_remote({});watch._install_remote_usage({});watch._install_remote_access({})
        self.assertEqual(run.call_count,4)
        self.assertTrue(all(str(self.worker) in call.args[0] for call in run.call_args_list))
        self.assertIsNone(watch.WORKER_TRANSPORT)

    def test_local_mode_skips_dotenv_and_preserves_native_checks(self):
        self.local()
        env={'HERMES_TRADING_MODE':'paper','HERMES_TRADING_ALLOW_REAL_ORDERS':'false'}
        with patch.dict(os.environ,env), patch.object(watch,'PROJECT_DIR',self.root), \
                patch.object(watch,'_load_dotenv') as dotenv, patch.object(watch,'_run_supervised_watch',return_value={'status':'not_due'}) as run:
            self.assertEqual(watch.main(['--launch-file',str(self.path)]),0)
        dotenv.assert_not_called();run.assert_called_once()
        self.config['release_hash']='bad';self.write()
        with patch.dict(os.environ,env), patch.object(watch,'PROJECT_DIR',self.root), patch.object(watch,'_main') as main:
            with self.assertRaises(ValueError): watch.main(['--launch-file',str(self.path)])
        main.assert_not_called()

    def test_unknown_mode_and_tampered_local_source_fail_closed(self):
        self.local()
        for mode in ['automatic','local']:
            self.config['transport']=mode;self.write()
            (self.root/'scripts/paper_local_transport.py').write_text('tampered')
            with self.subTest(mode=mode), patch.object(watch,'PROJECT_DIR',self.root), \
                    patch.object(watch,'_main') as main:
                with self.assertRaises(ValueError): watch.main(['--launch-file',str(self.path)])
            main.assert_not_called()

    def test_local_health_keeps_exact_pending_observation_until_ack(self):
        self.local()
        request={'schema_version':1,'purpose':'paper_supervisor_health_v1',
            'experiment_id':'test','policy_hash':self.config['policy_hash'],'event_id':'a'*32,
            'observed_at':self.now.isoformat(),'success':True,'error_kind':None}
        # Build using the native validator instead of relying on an assumed schema.
        from hermes_trading.paper_supervisor_health import build_request
        request=build_request('test',self.config['policy_hash'],event_id='a'*32,now=self.now,success=True)
        pending=self.state/'paper_supervisor_health_pending.json';pending.write_text(json.dumps(request))
        env={'HERMES_TRADING_MODE':'paper','HERMES_TRADING_ALLOW_REAL_ORDERS':'false'}
        with patch.dict(os.environ,env),patch.object(watch,'PROJECT_DIR',self.root),watch.supervised_launch(self.path):
            with patch.object(watch.WORKER_TRANSPORT,'health',return_value={'status':'wrong'}):
                with self.assertRaises(ValueError):watch._deliver_supervisor_health(pending)
            self.assertEqual(json.loads(pending.read_text()),request)
            with patch.object(watch.WORKER_TRANSPORT,'health',return_value=dict(request,status='recorded')) as health:
                watch._deliver_supervisor_health(pending)
            health.assert_called_once_with(request)
        self.assertFalse(pending.exists())


if __name__ == '__main__': unittest.main()
