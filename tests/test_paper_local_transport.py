"""Local supervision must use the same native receivers, without SSH or log leaks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from scripts.paper_local_transport import LocalPaperTransport, TransportError, _run_bounded


class LocalPaperTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.root = self.project / 'state'
        (self.root / 'adaptive_200').mkdir(parents=True)
        self.calls = []
        self.response = subprocess.CompletedProcess([], 0, '{"status":"recorded"}', '')
        def run(command, **kwargs):
            self.calls.append((command, kwargs))
            return self.response
        self.transport = LocalPaperTransport(self.project, self.root, 'adaptive_200',
            env={'HERMES_TRADING_MODE':'paper', 'HERMES_TRADING_ALLOW_REAL_ORDERS':'false'}, command_run=run)

    def test_snapshot_uses_native_receiver_and_primary(self):
        self.transport.snapshot()
        command, options = self.calls[0]
        self.assertEqual(command[1:4], ['-m', 'hermes_trading.supervisor_sync', '--state-dir'])
        self.assertEqual(command[4], str(self.root / 'adaptive_200'))
        self.assertEqual(options['cwd'], self.project)
        self.assertNotIn('shell', options)
        self.assertLessEqual(options['timeout'], 30)

    def test_writes_use_existing_receivers_and_strict_json_stdin(self):
        for method, module in [('publish','paper_publication'), ('usage','supervisor_usage_feed'), ('access','model_access_refresh')]:
            with self.subTest(method=method):
                getattr(self.transport, method)({'request':'private'})
                command, options = self.calls[-1]
                self.assertEqual(command[2], 'hermes_trading.' + module)
                self.assertEqual(json.loads(options['input']), {'request':'private'})
                self.assertNotIn('private', ' '.join(command))
                if method == 'access': self.assertEqual(command[3], 'install')

    def test_health_targets_whole_paired_root(self):
        self.transport.health({'event_id':'observation'})
        command, _ = self.calls[-1]
        self.assertEqual(command[2:5], ['hermes_trading.paper_supervisor_health','--root',str(self.root)])

    def test_paper_opt_in_is_required(self):
        for env in [{}, {'HERMES_TRADING_MODE':'live','HERMES_TRADING_ALLOW_REAL_ORDERS':'false'},
                    {'HERMES_TRADING_MODE':'paper','HERMES_TRADING_ALLOW_REAL_ORDERS':'true'}]:
            with self.subTest(env=env), self.assertRaises(ValueError):
                LocalPaperTransport(self.project, self.root, 'adaptive_200', env=env)

    def test_primary_is_explicit_and_cannot_escape_root(self):
        for primary in ['../adaptive_200','control_200','adaptive_999','/tmp/adaptive_200']:
            with self.subTest(primary=primary), self.assertRaises(ValueError):
                LocalPaperTransport(self.project, self.root, primary, env=self.transport.env)

    def test_receiver_error_redacts_both_output_streams(self):
        self.response = subprocess.CompletedProcess([], 2, 'private wallet', 'secret token')
        with self.assertRaises(TransportError) as raised: self.transport.snapshot()
        self.assertNotIn('private', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))

    def test_timeout_is_uncertain_and_is_never_retried(self):
        def timeout(*args, **kwargs):
            self.calls.append(args)
            raise subprocess.TimeoutExpired(['private'], 30, output='private', stderr='secret')
        self.transport.command_run = timeout
        with self.assertRaisesRegex(TransportError, 'outcome_uncertain') as raised:
            self.transport.publish({'review':'pending'})
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_or_oversize_response_is_rejected(self):
        for body in ['[]', 'not json', '{"value":NaN}', '{"value":1e999}', '{"a":1,"a":2}', ' ' * (self.transport.MAX_BYTES+1)]:
            with self.subTest(body=body[:25]), self.assertRaises(TransportError):
                self.response = subprocess.CompletedProcess([], 0, body, '')
                self.transport.snapshot()

    def test_nonfinite_input_never_starts_receiver(self):
        with self.assertRaises(ValueError): self.transport.publish({'value':float('nan')})
        self.assertEqual(self.calls, [])

    def test_real_child_stdout_and_stderr_are_bounded(self):
        for stream in ['stdout', 'stderr']:
            with self.subTest(stream=stream), self.assertRaisesRegex(TransportError, 'exceeds_bound'):
                _run_bounded([sys.executable, '-c', f'import sys; sys.{stream}.write("x" * 100000)'],
                    input=None, cwd=self.project, env=self.transport.env, timeout=5, max_bytes=2048)

    def test_real_child_decodes_private_response_without_logging(self):
        result = _run_bounded([sys.executable, '-c', 'import sys; print(sys.stdin.read()); print("hidden", file=sys.stderr)'],
            input='{"private":true}', cwd=self.project, env=self.transport.env, timeout=5, max_bytes=2048)
        self.assertEqual(json.loads(result.stdout), {'private':True})
        self.assertEqual(result.stderr, '')

    def test_real_child_cancellation_is_uncertain(self):
        stopped=threading.Event();stopped.set()
        with self.assertRaisesRegex(TransportError,'outcome_uncertain'):
            _run_bounded([sys.executable,'-c','import time; time.sleep(10)'], input=None,
                cwd=self.project,env=self.transport.env,timeout=5,max_bytes=2048,stop_event=stopped)

    def test_active_invocation_budget_caps_receiver_wait(self):
        self.transport.timeout_seconds=lambda:2.5
        self.transport.snapshot()
        self.assertEqual(self.calls[0][1]['timeout'],2.5)
        for value in [0,31,float('nan'),True]:
            self.transport.timeout_seconds=lambda:value
            with self.subTest(value=value),self.assertRaises(ValueError):self.transport.snapshot()

    def test_cancellation_stops_a_real_grandchild(self):
        started=self.project/'descendant-started';late=self.project/'descendant-after-cancel'
        grandchild='import pathlib,time; pathlib.Path('+repr(str(started))+').touch(); time.sleep(.8); pathlib.Path('+repr(str(late))+').touch()'
        child='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(grandchild)+']); time.sleep(10)'
        stop=threading.Event()
        def cancel_after_spawn():
            deadline=time.monotonic()+4
            while not started.exists() and time.monotonic()<deadline:time.sleep(.01)
            stop.set()
        watcher=threading.Thread(target=cancel_after_spawn);watcher.start()
        try:
            with self.assertRaisesRegex(TransportError,'outcome_uncertain'):
                _run_bounded([sys.executable,'-c',child],cwd=self.project,env=self.transport.env,
                    timeout=5,max_bytes=2048,stop_event=stop)
            self.assertTrue(started.exists(),'grandchild must start before cancellation')
            time.sleep(1)
            self.assertFalse(late.exists(),'grandchild continued after cancellation')
        finally:watcher.join(timeout=5)

    def test_nested_receivers_cannot_escape_outer_cancellation(self):
        started=self.project/'nested-started';late=self.project/'nested-after-cancel'
        nested='import pathlib,time; pathlib.Path('+repr(str(started))+').touch(); time.sleep(.8); pathlib.Path('+repr(str(late))+').touch()'
        source=Path(__file__).resolve().parents[1]
        child=('import sys,os; sys.path.insert(0,'+repr(str(source))+'); '
            'from scripts.paper_local_transport import _run_bounded; '
            '_run_bounded([sys.executable,"-c",'+repr(nested)+'],cwd='+repr(str(self.project))+',env=os.environ.copy(),timeout=5,max_bytes=2048)')
        stop=threading.Event()
        def cancel_after_spawn():
            deadline=time.monotonic()+4
            while not started.exists() and time.monotonic()<deadline:time.sleep(.01)
            stop.set()
        watcher=threading.Thread(target=cancel_after_spawn);watcher.start()
        try:
            with self.assertRaisesRegex(TransportError,'outcome_uncertain'):
                _run_bounded([sys.executable,'-c',child],cwd=self.project,env=self.transport.env,
                    timeout=5,max_bytes=4096,stop_event=stop)
            self.assertTrue(started.exists(),'nested receiver must start before cancellation')
            time.sleep(1)
            self.assertFalse(late.exists(),'nested receiver escaped outer cancellation')
        finally:watcher.join(timeout=5)


if __name__ == '__main__': unittest.main()
