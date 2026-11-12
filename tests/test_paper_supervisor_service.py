import io
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import Mock
import unittest

from scripts.paper_supervisor_service import supervise


class SupervisorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/'supervisor';self.state.mkdir()
        self.now=datetime.now(timezone.utc)
        self.config={'transport':'local','enabled':True,'status':'activated','local_state_dir':str(self.state),
            'expires_at':(self.now+timedelta(hours=1)).isoformat()}
        self.launch=self.root/'launch.json';self.launch.write_text(json.dumps(self.config))
        (self.state/'supervisor_limits.json').write_text(json.dumps({'interval_seconds':60}))
        (self.state/'supervisor_lifecycle.json').write_text(json.dumps({'status':'running'}))
        self.stop=threading.Event();self.stream=io.StringIO()

    def run_service(self, run, wait):
        return supervise(self.launch,stop=self.stop,launch_run=run,wait=wait,clock=lambda:self.now,stream=self.stream)

    def test_runs_bounded_native_cycles_and_waits_configured_interval(self):
        run=Mock(return_value={'status':'completed','exit_code':0,'wakeAgent':False})
        waits=[]
        def wait(seconds):waits.append(seconds);self.stop.set()
        self.assertEqual(self.run_service(run,wait),'stopped')
        self.assertEqual(waits,[60]);self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0],self.launch)

    def test_uncertain_child_is_not_retried(self):
        run=Mock(return_value={'status':'child_outcome_uncertain','exit_code':1,'wakeAgent':False})
        wait=Mock()
        self.assertEqual(self.run_service(run,wait),'outcome_uncertain')
        run.assert_called_once();wait.assert_not_called()

    def test_finite_deadline_and_existing_stopped_lifecycle_prevent_launch(self):
        run=Mock();wait=Mock()
        self.config['expires_at']=self.now.isoformat();self.launch.write_text(json.dumps(self.config))
        self.assertEqual(self.run_service(run,wait),'expired')
        self.config['expires_at']=(self.now+timedelta(hours=1)).isoformat();self.launch.write_text(json.dumps(self.config))
        (self.state/'supervisor_lifecycle.json').write_text(json.dumps({'status':'stopped'}))
        self.assertEqual(self.run_service(run,wait),'stopped')
        run.assert_not_called()

    def test_missing_state_never_initializes_a_new_experiment(self):
        (self.state/'supervisor_lifecycle.json').unlink()
        run=Mock()
        with self.assertRaises(ValueError):self.run_service(run,Mock())
        run.assert_not_called()
        self.assertFalse((self.state/'supervisor_lifecycle.json').exists())

    def test_output_allowlist_never_logs_returned_private_fields(self):
        run=Mock(return_value={'status':'completed','exit_code':0,'wallet':'private','token':'secret'})
        self.run_service(run,lambda seconds:self.stop.set())
        self.assertNotIn('private',self.stream.getvalue());self.assertNotIn('secret',self.stream.getvalue())

    def test_unknown_mode_or_unbounded_interval_never_starts(self):
        for mode,interval in [('ssh',60),('local',0),('local',True),('local',100000)]:
            self.config['transport']=mode;self.launch.write_text(json.dumps(self.config))
            (self.state/'supervisor_limits.json').write_text(json.dumps({'interval_seconds':interval}))
            run=Mock()
            with self.subTest(mode=mode,interval=interval),self.assertRaises(ValueError):self.run_service(run,Mock())
            run.assert_not_called()

    def test_waiting_for_auth_does_not_consume_cycles_or_send_false_health(self):
        self.config['project_dir']=str(self.root);self.launch.write_text(json.dumps(self.config))
        run=Mock()
        result=supervise(self.launch,stop=self.stop,launch_run=run,
            wait=lambda _:self.stop.set(),clock=lambda:self.now,stream=self.stream,wait_for_auth=True)
        self.assertEqual(result,'stopped');run.assert_not_called()
        self.assertIn('auth_pending',self.stream.getvalue())


if __name__ == '__main__':unittest.main()
