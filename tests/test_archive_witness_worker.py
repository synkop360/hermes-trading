"""Native worker reservation checks using an isolated local receiver, no network."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import json,secrets,shutil,sqlite3,tempfile,unittest
from unittest.mock import patch

import test_paired_worker as fixtures
from hermes_trading import archive_witness as witness
from hermes_trading import archive_journal as archive
from hermes_trading.control_auth import sign_command
from hermes_trading.paired_worker import run_worker
from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
from hermes_trading.reflection_supervision import _hash,_json,_time


class Receiver:
    """Test-only adapter. Separate pathname does not establish independence."""
    def __init__(self,path,clock):self.path=path;self.clock=clock;self.lose_ack=False;self.bad_binding=False;self.unavailable=False
    def read(self,*,binding,identity,timeout):
        if self.unavailable:raise RuntimeError('receiver unavailable')
        result=witness.read(self.path,binding,identity,enabled=True)
        if self.bad_binding:result['genesis_sha256']='f'*64
        return result
    def apply(self,view,*,binding,identity,action,expected_head_sha256,operation_id,timeout):
        result=witness.apply(self.path,binding,identity,view,action=action,
            expected_head_sha256=expected_head_sha256,operation_id=operation_id,enabled=True,now=self.clock())
        if self.lose_ack and action=='check' and view['complete']:
            self.lose_ack=False;raise RuntimeError('lost completion acknowledgement')
        return result


class NativeWitnessWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.limits['max_cycles']=4 if 'repeated_retirement' in self._testMethodName else 2;self.save_limits()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'receiver.sqlite';self.identity=witness.identity_for(self.f.root)
        self.binding=witness.initialize(self.path,self.identity,source_root=self.f.root,enabled=True,now=self.f.at)
        self.f.limits['archive_witness']={'schema_version':1,'binding':self.binding,'identity_sha256':_hash(self.identity)}
        self.save_limits();self.client=Receiver(self.path,self.f.clock)

    def save_limits(self):(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
    def head(self):return witness.read(self.path,self.binding,self.identity,enabled=True)
    async def invoke(self,root=None):
        return await run_worker(root or self.f.root,collector_factory=lambda:self.f.market,
            clock=self.f.clock,sleep=self.f.sleep,witness=self.client)

    async def test_configured_missing_adapter_blocks_before_collection(self):
        with self.assertRaisesRegex(ValueError,'witness.*adapter'):
            await run_worker(self.f.root,collector_factory=lambda:self.fail('collector constructed'),clock=self.f.clock)
        self.assertEqual(self.f.market.collect.await_count,0)

    async def test_native_worker_reserves_each_common_batch_and_bound_survives_restart(self):
        result=await self.invoke()
        self.assertEqual(result['completed_batches'],2);self.assertEqual(result['stop_reason'],'cycle_bound')
        self.assertEqual(self.head()['state']['point']['complete'],2)
        self.assertIsNone(self.head()['state']['reservation'])
        await self.invoke();self.assertEqual(self.f.market.collect.await_count,2)

    async def test_full_worker_source_rollback_is_rejected_before_collection(self):
        old=Path(self.tmp.name)/'old-worker';shutil.copytree(self.f.root,old)
        await self.invoke()
        with self.assertRaisesRegex(ValueError,'regress|missing'):
            await self.invoke(old)
        self.assertEqual(self.f.market.collect.await_count,2)

    async def test_interrupted_original_packet_uses_same_reservation_without_recollection(self):
        async def crash_account(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after first account')
        async def crash_cycle(*args,**kwargs):return await run_paired_cycle(*args,**kwargs,process_account=crash_account)
        with patch('hermes_trading.paired_worker.run_paired_cycle',side_effect=crash_cycle):
            with self.assertRaisesRegex(RuntimeError,'first account'):await self.invoke()
        reserved=deepcopy(self.head()['state']['reservation'])
        self.assertEqual(reserved['sequence'],1)
        result=await self.invoke()
        self.assertEqual(result['completed_batches'],2);self.assertEqual(self.f.market.collect.await_count,2)
        self.assertIsNone(self.head()['state']['reservation'])

    async def test_lost_completion_reply_reconciles_without_extra_collection(self):
        self.client.lose_ack=True
        with self.assertRaisesRegex(RuntimeError,'lost completion'):await self.invoke()
        self.assertEqual(self.head()['state']['point']['complete'],1)
        result=await self.invoke()
        self.assertEqual(result['completed_batches'],2);self.assertEqual(self.f.market.collect.await_count,2)

    async def test_unfrozen_reservation_never_auto_cancels_on_restart(self):
        self.f.market.collect.side_effect=RuntimeError('collection failed before freeze')
        with self.assertRaisesRegex(RuntimeError,'before freeze'):await self.invoke()
        token=self.head()['state']['reservation']['reservation_id']
        self.f.market.collect.side_effect=None
        with self.assertRaisesRegex(ValueError,'unresolved|original packet'):await self.invoke()
        self.assertEqual(self.head()['state']['reservation']['reservation_id'],token)
        self.assertEqual(self.f.market.collect.await_count,1)

    async def test_foreign_receiver_binding_is_rejected_before_collection(self):
        self.client.bad_binding=True
        with self.assertRaisesRegex(ValueError,'witness.*binding'):await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,0)

    async def test_receiver_outage_replaces_previous_healthy_heartbeat(self):
        original_sleep=self.f.sleep
        async def lose_receiver(seconds):
            await original_sleep(seconds);self.client.unavailable=True
        self.f.sleep=lose_receiver
        with self.assertRaisesRegex(RuntimeError,'receiver unavailable'):await self.invoke()
        heartbeat=json.loads((self.f.primary/'heartbeat.json').read_text())
        self.assertEqual(heartbeat['status'],'degraded')
        self.assertIn('witness_failed',heartbeat['error'])
        self.assertEqual(self.f.market.collect.await_count,1)
        self.assertEqual(self.head()['state']['point']['complete'],1)

    def retire(self,through):
        """Invented receipts exercise journal mechanics, not independent backups."""
        path=self.f.primary/'paper_publications.sqlite'
        request=archive.prepare(path,enabled=True,experiment_id=self.identity['experiment_id'],
            release_sha256=self.identity['release_sha256'],archive_sha256='b'*64,
            account_snapshots={name:'c'*64 for name in archive.ACCOUNTS},
            copies={'pc':'d'*64,'backup':'e'*64},through_sequence=through)
        keys={name:secrets.token_hex(32) for name in ('pc','backup')}
        receipts={name:{'body':_json(request),'nonce':request['operation_id'],'timestamp':self.f.at.isoformat(),
            'signature':sign_command(key,request['operation_id'],self.f.at,_json(request))} for name,key in keys.items()}
        archive.acknowledge(path,request['operation_id'],receipts,keys,enabled=True,now=self.f.at)
        archive.retire(path,request['operation_id'],enabled=True)

    async def pause_after(self,count):
        original_sleep=self.f.sleep
        async def pause(seconds):
            await original_sleep(seconds)
            if self.f.market.collect.await_count==count:raise RuntimeError('synthetic worker pause')
        self.f.sleep=pause
        try:
            with self.assertRaisesRegex(RuntimeError,'synthetic worker pause'):await self.invoke()
        finally:self.f.sleep=original_sleep

    async def test_repeated_retirement_preserves_witness_and_original_four_cycle_bound(self):
        await self.pause_after(2);self.retire(1)
        await self.pause_after(3);self.retire(2)
        result=await self.invoke()
        self.assertEqual(result['completed_batches'],4);self.assertEqual(result['archive_revision'],2)
        self.assertEqual(result['limits']['max_cycles'],4);self.assertEqual(result['stop_reason'],'cycle_bound')
        point=self.head()['state']['point']
        self.assertEqual(point['complete'],4);self.assertEqual(point['retired_complete'],2)
        self.assertEqual(point['archive_revision'],2)
        with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paired_batches').fetchone()[0],2)
        for profile in self.f.manifest['profiles']:self.assertEqual(len(self.f.positions(profile['directory'])),1)
        await self.invoke();self.assertEqual(self.f.market.collect.await_count,4)

    async def test_repeated_retirement_never_extends_the_original_deadline(self):
        await self.pause_after(2);self.retire(1)
        self.f.at=_time(self.identity['deadline'])
        result=await self.invoke()
        self.assertEqual(result['deadline'],self.identity['deadline'])
        self.assertEqual(result['stop_reason'],'deadline');self.assertEqual(result['completed_batches'],2)
        self.assertEqual(self.head()['state']['point']['complete'],2)
        self.assertEqual(self.head()['state']['point']['archive_revision'],1)
        self.assertEqual(self.f.market.collect.await_count,2)


if __name__=='__main__':unittest.main()
