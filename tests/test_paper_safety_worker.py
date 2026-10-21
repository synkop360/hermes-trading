"""Paper entry halts must preserve collection, protective exits and replay."""
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

import test_paired_worker as fixtures
from hermes_trading.paper_safety import record_supervisor_result


class PaperSafetyWorkerTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.WorkerTests.setUp
    tearDown = fixtures.WorkerTests.tearDown
    positions = fixtures.WorkerTests.positions
    sleep = fixtures.WorkerTests.sleep
    clock = fixtures.WorkerTests.clock
    invoke = fixtures.WorkerTests.invoke

    def safety(self):
        policy = dict(schema_version=1, experiment_id='paired-test',
            volume_root=str(self.root.resolve()), capacity_bytes=500000000,
            alert_fraction='0.70', max_worker_errors=3, max_supervisor_errors=3,
            supervisor_stale_seconds=300, diagnostics_max_bytes=1048576,
            diagnostics_generations=3)
        (self.root/'paper_safety.json').write_text(json.dumps(policy))
        receiver = patch('hermes_trading.paper_safety._utc_now', side_effect=lambda:self.at+timedelta(hours=1))
        receiver.start()
        self.addCleanup(receiver.stop)
        record_supervisor_result(self.root, success=True, event_id='opening', now=self.at)

    def costs(self):
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'wallet_costs.json'
            value=json.loads(path.read_text());value['as_of']=self.at.isoformat()
            path.write_text(json.dumps(value))

    async def test_volume_alert_after_collection_skips_entries_without_stopping_worker(self):
        self.safety()
        flag=[]
        async def collect(assets):
            flag.append(True)
            return {'ETH/EUR':self.snapshot}
        self.market.collect.side_effect=collect
        def inspect(policy):
            return dict(blocked=bool(flag), reasons=['volume_threshold'] if flag else [],
                        used_bytes=350000000 if flag else 1000,
                        capacity_bytes=500000000,threshold_bytes=350000000)
        with patch('hermes_trading.paper_safety.inspect_volume',side_effect=inspect):
            result=await self.invoke()
        self.assertEqual(result['completed_batches'],1)
        self.assertEqual(result['stop_reason'],'cycle_bound')
        self.assertTrue(result['entry_admission']['blocked'])
        self.assertIn('volume_threshold',result['entry_admission']['reasons'])
        for profile in self.manifest['profiles']:
            self.assertFalse(self.positions(profile['directory']))

    async def test_storage_entry_halt_keeps_monitor_alive_until_operator_stop(self):
        self.safety()
        waits=[]
        async def monitor(seconds):
            waits.append(True)
            if len(waits)>1:raise RuntimeError('operator stop was ignored by storage monitor')
            heartbeat=json.loads((self.root/'heartbeat.json').read_text())
            self.assertEqual(heartbeat['worker_lifecycle']['status'],'running')
            self.assertIn('storage_stop',heartbeat['entry_admission']['reasons'])
            (self.root/'paper_stop.json').write_text(json.dumps({'experiment_id':'paired-test'}))
            await self.sleep(seconds)
        blocked=lambda *args:dict(status='blocked',reason='state_budget',state_bytes=400000000,free_bytes=1000000000)
        result=await self.invoke(sleep=monitor,storage_probe=blocked)
        self.assertEqual(result['stop_reason'],'operator_stop')
        self.assertEqual(len(waits),1)
        self.assertEqual(self.market.collect.await_count,0)

    async def test_missing_first_supervisor_health_blocks_entries_without_snapshot_deadlock(self):
        from hermes_trading.supervisor_sync import make_snapshot
        self.safety()
        (self.root/'paper_safety_state.json').unlink()
        self.limits['max_cycles']=2
        (self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        observed=[]
        async def supervisor_bootstrap(seconds):
            if not observed:
                heartbeat=json.loads((self.root/'heartbeat.json').read_text())
                self.assertIn('supervisor_health_missing',heartbeat['entry_admission']['reasons'])
                self.assertEqual(heartbeat['status'],'ok')
                snapshot=make_snapshot(self.primary,now=self.at)
                self.assertTrue(snapshot['worker_process_live'])
                observed.append(True)
                record_supervisor_result(self.root,success=True,event_id='first-success',now=self.at)
            await self.sleep(seconds)
        result=await self.invoke(sleep=supervisor_bootstrap)
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(len(observed),1)
        self.assertNotIn('supervisor_health_missing',result['entry_admission']['reasons'])

    async def test_daily_backup_continues_on_each_day_of_storage_entry_halt(self):
        from hermes_trading.paper_backup import create_daily_backup
        self.safety()
        self.limits.update(max_runtime_seconds=3*86400,daily_backup=dict(schema_version=1,
            destination=str((self.root.parent/'daily').resolve()),max_bytes=500000000,timeout_seconds=30,keep=2))
        (self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        captured=[];waits=[]
        def capture(*args,**kwargs):
            result=create_daily_backup(*args,**kwargs);captured.append(result);return result
        async def monitor(seconds):
            waits.append(True)
            if len(waits)==1:self.at+=timedelta(days=1)
            else:(self.root/'paper_stop.json').write_text(json.dumps({'experiment_id':'paired-test'}))
        blocked=lambda *args:dict(status='blocked',reason='state_budget',state_bytes=400000000,free_bytes=1000000000)
        with patch('hermes_trading.paper_backup.create_daily_backup',side_effect=capture):
            result=await self.invoke(sleep=monitor,storage_probe=blocked)
        self.assertEqual(result['stop_reason'],'operator_stop')
        self.assertEqual(len(captured),2,'entry-halted monitor must still make the required daily copy')
        self.assertNotEqual(captured[0]['name'],captured[1]['name'])
        self.assertEqual(self.market.collect.await_count,0)

    async def test_three_worker_errors_latch_entries_and_retry_without_duplicate_batches(self):
        self.safety()
        self.market.collect.side_effect=[RuntimeError('untrusted request detail')]*3+[{'ETH/EUR':self.snapshot}]
        result=await self.invoke()
        self.assertEqual(result['completed_batches'],1)
        self.assertEqual(self.market.collect.await_count,4)
        self.assertIn('worker_error_halt',result['entry_admission']['reasons'])
        state=json.loads((self.root/'paper_safety_state.json').read_text())
        self.assertEqual(state['worker']['consecutive_errors'],3)
        self.assertTrue(state['worker']['entry_halt'])
        for profile in self.manifest['profiles']:
            self.assertFalse(self.positions(profile['directory']))
        log=(self.root/'paper_runtime.log').read_text()
        self.assertNotIn('untrusted request detail',log)
        self.assertEqual(sum(json.loads(line)['event']=='cycle_failed' for line in log.splitlines()),3)

    async def test_supervisor_error_halt_preserves_due_exit_on_all_eight_accounts(self):
        self.safety()
        self.limits['max_cycles']=2
        (self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        async def fail_supervisor(seconds):
            await self.sleep(seconds)
            for index in range(3):
                record_supervisor_result(self.root, success=False,event_id=f'failed-{index}',
                    now=self.at+timedelta(microseconds=index))
            self.at+=timedelta(seconds=1)
            snapshot=replace(self.snapshot,price=dict(self.snapshot.price,bid='77',ask='78',received_at=self.at.isoformat()))
            self.market.collect.return_value={'ETH/EUR':snapshot}
            self.costs()
        result=await self.invoke(sleep=fail_supervisor)
        self.assertEqual(result['completed_batches'],2)
        self.assertIn('supervisor_error_halt',result['entry_admission']['reasons'])
        for profile in self.manifest['profiles']:
            positions=self.positions(profile['directory'])
            self.assertEqual(len(positions),1)
            self.assertEqual(positions[0].status.value,'closed')

    async def test_daily_backup_uses_held_worker_lease_once_per_day(self):
        from pathlib import Path
        from hermes_trading.paper_backup import create_daily_backup
        self.safety()
        self.limits['max_cycles']=2
        self.limits['daily_backup']=dict(schema_version=1,destination=str((self.root.parent/'daily').resolve()),
            max_bytes=500000000,timeout_seconds=30,keep=2)
        (self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        calls=[]
        def capture(*args,**kwargs):
            self.assertIsNotNone(kwargs.get('owner_db'))
            result=create_daily_backup(*args,**kwargs)
            calls.append(result)
            return result
        with patch('hermes_trading.paper_backup.create_daily_backup',side_effect=capture):
            result=await self.invoke()
        self.assertEqual(len(calls),1)
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(result['daily_backup']['date'],self.at.date().isoformat())
        self.assertFalse(result['daily_backup']['off_host_verified'])
        self.assertTrue((Path(result['daily_backup']['path'])/'paper_backup_receipt.json').is_file())

    async def test_backup_failure_blocks_next_entries_but_preserves_exit_and_retries(self):
        from hermes_trading.paper_backup import create_daily_backup
        self.safety()
        self.limits['max_cycles']=2
        self.limits['daily_backup']=dict(schema_version=1,destination=str((self.root.parent/'daily').resolve()),
            max_bytes=500000000,timeout_seconds=30,keep=2)
        (self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        calls=[]
        def capture(*args,**kwargs):
            calls.append(True)
            if len(calls)==1:raise OSError('private location must not be logged')
            return create_daily_backup(*args,**kwargs)
        async def observe_failure(seconds):
            state=json.loads((self.root/'heartbeat.json').read_text())['worker_lifecycle']
            if len(calls)==1:
                self.assertIn('daily_backup_failed',state['entry_admission']['reasons'])
            await self.sleep(seconds)
            snapshot=replace(self.snapshot,price=dict(self.snapshot.price,bid='77',ask='78',received_at=self.at.isoformat()))
            self.market.collect.return_value={'ETH/EUR':snapshot}
            self.costs()
        with patch('hermes_trading.paper_backup.create_daily_backup',side_effect=capture):
            result=await self.invoke(sleep=observe_failure)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['completed_batches'],2)
        self.assertIsNone(result.get('daily_backup_error'))
        for profile in self.manifest['profiles']:
            positions=self.positions(profile['directory'])
            self.assertEqual(len(positions),1)
            self.assertEqual(positions[0].status.value,'closed')


if __name__=='__main__':unittest.main()
