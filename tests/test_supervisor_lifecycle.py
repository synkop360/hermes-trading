from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,tempfile,unittest

from hermes_trading.reflection_supervision import _hash


class SupervisorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.now=datetime(2026,10,12,tzinfo=timezone.utc)
        self.policy={'experiment_id':'test','supervisor_lifecycle_required':True}
        self.release={'worker':'hash','watcher':'hash'}
        self.limits={'schema_version':1,'experiment_id':'test','policy_hash':_hash(self.policy),
                     'activated_at':self.now.isoformat(),'max_cycles':3,'max_runtime_seconds':600,
                     'interval_seconds':60,'max_model_attempts':1,'release_hash':_hash(self.release)}
        (self.root/'wallet_account.json').write_text(json.dumps({'experiment_id':'test','activated_at':self.now.isoformat()}))
        self.write_limits()
    def tearDown(self):self.tmp.cleanup()
    def write_limits(self):(self.root/'supervisor_limits.json').write_text(json.dumps(self.limits))
    def admit(self,seconds=0):
        from hermes_trading.supervisor_lifecycle import admit_cycle
        return admit_cycle(self.root,self.policy,self.release,now=self.now+timedelta(seconds=seconds))
    def state(self):return json.loads((self.root/'supervisor_lifecycle.json').read_text())
    def test_spacing_and_restart_preserve_cycle_bound(self):
        self.assertEqual(self.admit().status,'admitted')
        self.assertEqual(self.admit(59).status,'not_due')
        self.assertEqual(self.admit(60).status,'admitted')
        self.assertEqual(self.admit(120).status,'admitted')
        self.assertEqual(self.admit(180).status,'stopped')
        self.assertEqual(self.state()['cycles_started'],3)
        self.assertEqual(self.admit(240).status,'stopped')
    def test_deadline_rechecked_before_model_and_survives_restart(self):
        cycle=self.admit()
        self.assertFalse(cycle.reserve_model(now=self.now+timedelta(seconds=600)))
        self.assertEqual(self.state()['stop_reason'],'deadline')
        self.assertEqual(self.admit(601).status,'stopped')
    def test_model_reservation_survives_failure_and_restart(self):
        first=self.admit();self.assertTrue(first.reserve_model(now=self.now))
        second=self.admit(60)
        self.assertFalse(second.reserve_model(now=self.now+timedelta(seconds=60)))
        self.assertEqual(self.state()['model_attempts_reserved'],1)
        self.assertEqual(self.state()['stop_reason'],'model_attempt_budget')
    def test_changed_limits_removed_limits_and_release_rejected(self):
        self.admit();self.limits['max_cycles']=4;self.write_limits()
        with self.assertRaises(ValueError):self.admit(60)
        self.limits['max_cycles']=3;self.write_limits();self.release['watcher']='changed'
        with self.assertRaises(ValueError):self.admit(60)
        (self.root/'supervisor_limits.json').unlink()
        with self.assertRaises(ValueError):self.admit(60)
    def test_invalid_bounds_and_future_activation_rejected(self):
        for key,value in [('max_cycles',True),('max_runtime_seconds',0),('interval_seconds',300),('max_model_attempts',-1)]:
            original=self.limits[key];self.limits[key]=value;self.write_limits()
            with self.subTest(key=key),self.assertRaises(ValueError):self.admit()
            self.limits[key]=original
        self.write_limits()
        with self.assertRaises(ValueError):self.admit(-1)
    def test_operator_stop_is_terminal_without_consuming_cycle(self):
        (self.root/'supervisor_operator_stop.json').write_text(json.dumps({'experiment_id':'test'}))
        self.assertEqual(self.admit().status,'stopped')
        self.assertEqual(self.state()['cycles_started'],0)
        (self.root/'supervisor_operator_stop.json').unlink()
        self.assertEqual(self.admit(60).status,'stopped')
    def test_clock_regression_rejected(self):
        self.admit(60)
        with self.assertRaises(ValueError):self.admit(59)
