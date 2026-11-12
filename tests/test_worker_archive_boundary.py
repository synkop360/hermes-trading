"""Native owner-boundary capture and outage admission; invented quotes only."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import json,sqlite3,unittest

import test_paired_worker as fixtures
from hermes_trading.paired_worker import run_worker,_stats
from hermes_trading.market_data import MarketSnapshot


class WorkerArchiveBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.limits.update(max_cycles=2,archive_exports={'schema_version':1,'enabled':True,'max_bytes':32*1024*1024,'timeout_seconds':10})
        self.save()
        import tempfile
        from pathlib import Path
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.target=Path(self.tmp.name)/'worker'

    def save(self):(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
    async def invoke(self,callback):
        return await run_worker(self.f.root,collector_factory=lambda:self.f.market,clock=self.f.clock,sleep=self.f.sleep,archive_boundary=callback)

    async def test_real_worker_captures_completed_eight_account_cut_without_releasing_owner(self):
        captured=[];saved=[]
        async def callback(boundary):
            if boundary.complete!=1:return
            saved.append(boundary)
            result=boundary.capture(self.target,expected_complete=1);captured.append(result)
            self.assertEqual(result['cut']['complete'],1);self.assertEqual(_stats(self.target/'adaptive_100')['complete'],1)
            with self.assertRaisesRegex(RuntimeError,'worker'):await self.invoke(callback)
            for profile in self.f.manifest['profiles']:
                directory=profile['directory']
                with closing(sqlite3.connect(self.f.root/directory/'trading.db')) as source,closing(sqlite3.connect(self.target/directory/'trading.db')) as copied:
                    self.assertEqual(source.execute('SELECT * FROM positions ORDER BY rowid').fetchall(),copied.execute('SELECT * FROM positions ORDER BY rowid').fetchall())
        result=await self.invoke(callback)
        self.assertIsNone(result.get('local_archive_capture_error'))
        self.assertEqual(result['completed_batches'],2);self.assertEqual(self.f.market.collect.await_count,2)
        self.assertEqual(len(captured),1);self.assertFalse(captured[0]['retirement_authorized'])
        with self.assertRaisesRegex(ValueError,'expired|boundary'):saved[0].capture(self.target.with_name('late'),expected_complete=1)

    async def test_missing_explicit_configuration_or_adapter_blocks_before_collection(self):
        with self.assertRaisesRegex(ValueError,'archive.*adapter|archive.*callback'):await self.invoke(None)
        del self.f.limits['archive_exports'];self.save()
        async def callback(boundary):pass
        with self.assertRaisesRegex(ValueError,'archive.*config'):await self.invoke(callback)
        self.assertEqual(self.f.market.collect.await_count,0)

    async def test_native_boundary_coordinates_worker_and_supervisor_owners_for_all_wallets(self):
        from hermes_trading.archive_coordinator import capture
        from hermes_trading.archive_snapshot import ownership
        from hermes_trading.paper_publication import _release_hashes
        from hermes_trading.reflection_supervision import initialize_supervisor
        initialize_supervisor(self.f.primary,now=self.f.now)
        for profile in self.f.manifest['profiles']:
            folder=self.f.root/profile['directory'];(folder/'history').mkdir(exist_ok=True)
            (folder/'history/v0001.yaml').write_bytes((folder/'strategy.yaml').read_bytes())
        self.f.limits['archive_exports']['timeout_seconds']=30
        self.f.limits['max_cycles']=3;self.save()
        pc=self.target.with_name('supervisor');pc.mkdir();captured=[];errors=[]
        for name in ('reflection_owner.json','llm_review_state.sqlite'):
            (pc/name).write_bytes((self.f.primary/name).read_bytes())
        with closing(sqlite3.connect(pc/'watcher_cycle.sqlite')) as db:
            db.execute('CREATE TABLE cycle(id)');db.commit()
        async def callback(boundary):
            if boundary.complete!=2:return
            def worker_capture(target,**limits):
                with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                    with ownership(pc,role='supervisor',enabled=True):pass
                with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                    with ownership(self.f.root,role='worker',enabled=True):pass
                return boundary.capture(target,expected_complete=2)
            try:
                result=capture(pc,self.target,enabled=True,worker_capture=worker_capture,
                    experiment_id='paired-test',expected_release=_release_hashes(),through_sequence=1,
                    max_bytes=64*1024*1024,timeout_seconds=boundary.remaining(),now=self.f.at)
            except Exception as error:
                errors.append(error);raise
            captured.append(result)
        result=await self.invoke(callback)
        if errors:raise errors[0]
        self.assertIsNone(result.get('local_archive_capture_error'))
        self.assertEqual(len(captured),1)
        certificate=captured[0]['certificate']
        self.assertEqual(certificate['observed_complete'],2)
        self.assertEqual(certificate['supervisor_scope'],'separate_snapshot')
        self.assertEqual(len(certificate['account_snapshots']),8)
        self.assertFalse(captured[0]['retirement_authorized'])
        self.assertEqual(result['completed_batches'],3)
        self.assertEqual(self.f.market.collect.await_count,3)
        with ownership(pc,role='supervisor',enabled=True):pass
        with ownership(self.f.root,role='worker',enabled=True):pass

    async def test_capture_failure_blocks_new_entries_in_a_flat_wallet(self):
        snapshot=self.f.snapshot
        self.f.market.collect.return_value={snapshot.asset:MarketSnapshot(snapshot.asset,snapshot.timeframe,'unavailable','no_trade','invented first quote')}
        async def failure(boundary):
            self.f.market.collect.return_value={snapshot.asset:snapshot}
            raise OSError('invented export failure')
        result=await self.invoke(failure)
        self.assertEqual(result['completed_batches'],2);self.assertIsNotNone(result['local_archive_capture_error'])
        for profile in self.f.manifest['profiles']:self.assertEqual(len(self.f.positions(profile['directory'])),0)

    async def test_capture_failure_still_allows_original_protective_exits(self):
        original_sleep=self.f.sleep
        async def observe(seconds):
            if self.f.market.collect.await_count==2:
                heartbeat=json.loads((self.f.root/'heartbeat.json').read_text())
                self.assertEqual(heartbeat['status'],'degraded')
            await original_sleep(seconds)
        self.f.sleep=observe
        async def failure(boundary):
            snapshot=replace(self.f.snapshot,price=dict(self.f.snapshot.price,bid='77',ask='78',received_at=self.f.at.isoformat()))
            self.f.market.collect.return_value={snapshot.asset:snapshot};raise OSError('invented export failure')
        result=await self.invoke(failure)
        self.assertEqual(result['completed_batches'],2)
        for profile in self.f.manifest['profiles']:
            rows=self.f.positions(profile['directory']);self.assertEqual(len(rows),1);self.assertEqual(rows[0].status.value,'closed')

    async def test_successful_local_capture_clears_its_error_and_entries_can_resume(self):
        self.f.limits['max_cycles']=3;self.save();snapshot=self.f.snapshot
        self.f.market.collect.return_value={snapshot.asset:MarketSnapshot(snapshot.asset,snapshot.timeframe,'unavailable','no_trade','invented first quote')}
        async def recover(boundary):
            if boundary.complete==1:
                self.f.market.collect.return_value={snapshot.asset:snapshot};raise OSError('invented destination outage')
            if boundary.complete==2:
                boundary.capture(self.target,expected_complete=2)
                # The blocked batch already consumed the previous signal. Recovery
                # permits a fresh closed candle, never a retroactive entry on it.
                fresh=replace(snapshot,candle=replace(snapshot.candle,
                    open_timestamp=self.f.at-timedelta(hours=1),close_timestamp=self.f.at),
                    price=dict(snapshot.price,received_at=self.f.at.isoformat()))
                self.f.market.collect.return_value={fresh.asset:fresh}
        result=await self.invoke(recover)
        self.assertIsNone(result.get('local_archive_capture_error'))
        self.assertEqual(result['completed_batches'],3);self.assertEqual(self.f.market.collect.await_count,3)
        for profile in self.f.manifest['profiles']:self.assertEqual(len(self.f.positions(profile['directory'])),1)

    async def test_original_deadline_is_rechecked_after_callback(self):
        async def delayed(boundary):self.f.at+=timedelta(days=2)
        result=await self.invoke(delayed)
        self.assertEqual(result['stop_reason'],'deadline');self.assertEqual(result['completed_batches'],1)
        self.assertEqual(self.f.market.collect.await_count,1)

    async def test_owner_change_before_boundary_replaces_previous_healthy_heartbeat(self):
        original_sleep=self.f.sleep
        async def change(seconds):
            await original_sleep(seconds)
            self.f.policy['model']['name']='changed:free'
            (self.f.primary/'reflection_owner.json').write_text(json.dumps(self.f.policy))
        self.f.sleep=change
        async def idle(boundary):pass
        with self.assertRaisesRegex(ValueError,'owner'):await self.invoke(idle)
        heartbeat=json.loads((self.f.primary/'heartbeat.json').read_text())
        self.assertEqual(heartbeat['status'],'degraded');self.assertEqual(heartbeat['error'],'owner_changed')
        self.assertEqual(self.f.market.collect.await_count,1)

    async def test_partial_original_batch_is_replayed_before_any_capture_callback(self):
        from unittest.mock import patch
        from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
        seen=[]
        async def callback(boundary):seen.append(boundary.complete)
        async def crash_account(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after original account')
        async def crash_cycle(*args,**kwargs):return await run_paired_cycle(*args,**kwargs,process_account=crash_account)
        with patch('hermes_trading.paired_worker.run_paired_cycle',side_effect=crash_cycle):
            with self.assertRaisesRegex(RuntimeError,'original account'):await self.invoke(callback)
        self.assertEqual(seen,[])
        result=await self.invoke(callback)
        self.assertEqual(seen,[1,2]);self.assertEqual(result['completed_batches'],2)
        self.assertEqual(self.f.market.collect.await_count,2)


if __name__=='__main__':unittest.main()
