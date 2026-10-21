from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock
import hashlib,json,subprocess,tempfile,unittest


class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.now=datetime.now(timezone.utc)
        self.state=self.root/'state/paired-supervisor/test/adaptive_100';self.state.mkdir(parents=True)
        python=self.root/'.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_text('placeholder')
        self.path=self.root/'launch.json'
    def tearDown(self):self.tmp.cleanup()
    def config(self,**changes):
        from scripts import native_hermes_watch
        result={'status':'activated','enabled':True,'project_dir':str(self.root),'local_state_dir':str(self.state),
                'expires_at':(self.now+timedelta(seconds=600)).isoformat(),'max_script_seconds':240,
                'wrapper_sha256':hashlib.sha256(Path(native_hermes_watch.__file__).read_bytes()).hexdigest()}
        result.update(changes);self.path.write_text(json.dumps(result));return result
    def invoke(self,run):
        from scripts.native_hermes_watch import run_launch
        return run_launch(self.path,now=self.now,command_run=run)
    def test_disabled_expired_and_maintenance_spawn_nothing(self):
        run=Mock()
        for change in ({'enabled':False},{'status':'prepared_draft_not_activated'},{'expires_at':self.now.isoformat()}):
            self.config(**change);self.invoke(run)
        for marker in (self.root/'state/watcher_maintenance.json',self.state/'watcher_maintenance.json'):
            self.config();marker.write_text('{}');self.invoke(run);marker.unlink()
        run.assert_not_called()
    def test_exact_module_command_bounded_by_deadline_no_shell(self):
        self.config(expires_at=(self.now+timedelta(seconds=10)).isoformat())
        run=Mock(return_value=subprocess.CompletedProcess([],0,'private output','private error'))
        result=self.invoke(run)
        self.assertEqual(run.call_args.args[0],[str(self.root/'.venv/Scripts/python.exe'),'-m','scripts.hermes_watch','--launch-file',str(self.path)])
        self.assertEqual(run.call_args.kwargs['timeout'],10)
        self.assertEqual(run.call_args.kwargs['cwd'],self.root)
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertNotIn('private',json.dumps(result));self.assertFalse(result['wakeAgent'])

    def test_native_deadline_is_propagated_before_child_startup(self):
        from scripts import native_hermes_watch
        from unittest.mock import patch
        self.config();run=Mock(return_value=subprocess.CompletedProcess([],0,'',''))
        with patch.object(native_hermes_watch.time,'monotonic',return_value=1000):self.invoke(run)
        self.assertEqual(float(run.call_args.kwargs['env']['HERMES_TRADING_INVOCATION_DEADLINE']),1235)
        self.assertEqual(run.call_args.kwargs['timeout'],240)
    def test_invalid_hash_and_timeout_never_echo_child_payload(self):
        self.config(wrapper_sha256='wrong');run=Mock()
        with self.assertRaises(ValueError):self.invoke(run)
        run.assert_not_called()
        self.config();run.side_effect=subprocess.TimeoutExpired(['private arguments'],240,output='private output')
        result=self.invoke(run)
        self.assertEqual(result['status'],'child_outcome_uncertain');self.assertNotIn('private',json.dumps(result))
