"""Explicitly opted-in actual worker with a signed disposable receiver."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json,secrets,sqlite3,unittest
from unittest.mock import patch

import test_archive_protective_source as native
from hermes_trading import archive_witness as original
from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_protective_transport import ProtectiveClient,ProtectiveServer
from hermes_trading.control_auth import SQLiteNonceReplayStore
from hermes_trading.paired_worker import run_worker
from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _hash


class ProtectiveWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=native.ProtectiveSourceTests();self.f.setUp()
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()
        self.f.f.limits['max_cycles']=2;self.save()
        self.f.path=self.f.path.parent/'worker-receiver.sqlite';self.f.identity=original.identity_for(self.f.f.root)
        self.f.binding=witness.initialize(self.f.path,self.f.identity,source_root=self.f.f.root,enabled=True,now=self.f.f.at)
        self.f.f.limits['archive_witness']={'schema_version':2,'binding':self.f.binding,'identity_sha256':_hash(self.f.identity)};self.save()
        self.repo=StateRepository.open(self.f.path.parent/'nonce.sqlite');self.repo.migrate();self.addCleanup(self.repo.close)
        key=secrets.token_hex(32);self.error=None;self.lost_reserve=False;self.lost_complete=False
        self.server=ProtectiveServer(self.f.path,self.f.binding,self.f.identity,key,SQLiteNonceReplayStore(self.repo),enabled=True)
        def exchange(envelope,*,timeout_seconds,max_response_bytes):
            if self.error is not None:raise self.error
            value=json.loads(envelope['body']);result=self.server.handle(envelope,now=self.f.f.at,timeout_seconds=timeout_seconds)
            if self.lost_reserve and value['action']=='reserve':raise OSError('lost original reserve reply')
            if self.lost_complete and value['action']=='check' and value['view']['point']['complete']==1:
                self.prepare_exit();self.error=OSError('lost original completion reply');raise self.error
            return result
        self.client=ProtectiveClient(self.f.binding,self.f.identity,key,exchange,enabled=True,now=self.f.f.clock)

    def save(self):(self.f.f.root/'worker_limits.json').write_text(json.dumps(self.f.f.limits))
    def prepare_exit(self,*,stale=False):
        self.f.f.at+=timedelta(minutes=1)
        received=self.f.f.at-timedelta(hours=1) if stale else self.f.f.at
        quote=replace(self.f.f.snapshot,price=dict(self.f.f.snapshot.price,bid='77',ask='78',received_at=received.isoformat()))
        self.f.f.market.collect.return_value={quote.asset:quote}
    async def invoke(self,sleep=None):
        return await run_worker(self.f.f.root,collector_factory=lambda:self.f.f.market,clock=self.f.f.clock,
            sleep=sleep or self.f.f.sleep,witness=self.client)
    async def outage(self,seconds):self.prepare_exit();self.error=OSError('receiver unreachable')
    def heartbeat(self):return json.loads((self.f.f.primary/'heartbeat.json').read_text())

    async def test_actual_worker_outage_closes_all_eight_once_and_reconnect_ack_does_not_collect(self):
        state=await self.invoke(self.outage)
        self.assertEqual(state['status'],'waiting_for_archive');self.assertEqual(state['completed_batches'],2)
        self.assertEqual(self.f.f.market.collect.await_count,2);self.assertEqual(self.heartbeat()['status'],'stopped')
        self.assertEqual(state['archive_protection']['acknowledgement'],'pending')
        from hermes_trading.archive_review import canonical_closed_rows
        for profile in self.f.f.manifest['profiles']:
            path=self.f.f.root/profile['directory'];self.assertEqual(self.f.f.positions(profile['directory'])[0].status.value,'closed')
            with closing(sqlite3.connect(path/'trading.db')) as db:
                db.row_factory=sqlite3.Row;rows=canonical_closed_rows(db)
                self.assertEqual(rows[0]['exit_reason'],'paired_archive_stop')
                from hermes_trading.paired_performance_report import _canonical_account
                orders,_,_=_canonical_account(path,self.f.f.at,database=db)
                self.assertGreater(sum(Decimal(row['fee']) for row in orders),0)
        self.error=None;done=await self.invoke()
        self.assertEqual(done['stop_reason'],'cycle_bound');self.assertEqual(self.f.head()['state']['point']['complete'],2)
        self.assertIsNone(self.f.head()['state']['credit']);self.assertEqual(self.f.f.market.collect.await_count,2)

    async def test_lost_normal_completion_reply_uses_bounded_original_plus_protective_suffix(self):
        self.lost_complete=True;state=await self.invoke()
        self.assertEqual(state['completed_batches'],2);self.assertEqual(self.f.f.market.collect.await_count,2)
        self.assertEqual(self.f.head()['state']['point']['complete'],1)
        self.error=None;self.lost_complete=False
        await self.invoke();self.assertEqual(self.f.head()['state']['point']['complete'],2)
        self.assertEqual(self.f.f.market.collect.await_count,2)

    async def test_missing_adapter_or_unavailable_startup_receiver_never_collects(self):
        with self.assertRaises(ValueError):await run_worker(self.f.f.root,clock=self.f.f.clock)
        self.error=OSError('startup unavailable')
        with self.assertRaises(OSError):await self.invoke()
        self.assertEqual(self.f.f.market.collect.await_count,0)

    async def test_authentication_error_is_not_protective_transport_outage(self):
        async def bad(seconds):self.prepare_exit();self.error=ValueError('authentication failed')
        with self.assertRaises(ValueError):await self.invoke(bad)
        self.assertEqual(self.f.f.market.collect.await_count,1)
        self.assertEqual(self.heartbeat()['status'],'degraded')

    async def test_lost_unfrozen_reserve_reply_does_not_collect_or_borrow_credit(self):
        self.lost_reserve=True
        with self.assertRaises(OSError):await self.invoke()
        self.assertEqual(self.f.f.market.collect.await_count,0)
        self.assertIsNotNone(self.f.head()['state']['reservation'])

    async def test_partial_original_protection_restarts_without_recollecting(self):
        async def fail_account(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after original protective account')
        async def cycle(*args,**kwargs):
            if kwargs.get('operational_stop')=='paired_archive_stop':kwargs['process_account']=fail_account
            return await run_paired_cycle(*args,**kwargs)
        with patch('hermes_trading.paired_worker.run_paired_cycle',side_effect=cycle):
            with self.assertRaises(RuntimeError):await self.invoke(self.outage)
        self.assertEqual(self.heartbeat()['worker_lifecycle']['completed_batches'],1)
        self.error=None;done=await self.invoke()
        self.assertEqual(done['stop_reason'],'cycle_bound');self.assertEqual(self.f.f.market.collect.await_count,2)

    async def test_healthy_admission_keeps_final_original_slot_reserved(self):
        state=await self.invoke()
        self.assertEqual(state['stop_reason'],'protective_capacity_reserved')
        self.assertEqual(state['completed_batches'],1);self.assertEqual(self.f.f.market.collect.await_count,1)

    async def test_original_deadline_prevents_new_outage_quotes(self):
        async def expire(seconds):
            self.f.f.at+=timedelta(days=2);self.error=OSError('receiver unavailable after deadline')
        with self.assertRaises((ValueError,OSError)):await self.invoke(expire)
        self.assertEqual(self.f.f.market.collect.await_count,1)

    async def test_stale_outage_quote_leaves_positions_pending_without_claiming_exits(self):
        async def stale(seconds):self.prepare_exit(stale=True);self.error=OSError('receiver unreachable')
        result=await self.invoke(stale)
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(result['archive_protection']['status'],'positions_pending')
        self.assertEqual(self.f.f.market.collect.await_count,2)
        for profile in self.f.f.manifest['profiles']:
            self.assertEqual(self.f.f.positions(profile['directory'])[0].status.value,'open')

    async def test_original_partial_ordinary_packet_replays_without_a_new_quote(self):
        async def crash_account(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after original ordinary account')
        async def cycle(*args,**kwargs):return await run_paired_cycle(*args,**kwargs,process_account=crash_account)
        with patch('hermes_trading.paired_worker.run_paired_cycle',side_effect=cycle):
            with self.assertRaises(RuntimeError):await self.invoke()
        result=await self.invoke()
        self.assertEqual(result['completed_batches'],1)
        self.assertEqual(result['stop_reason'],'protective_capacity_reserved')
        self.assertEqual(self.f.f.market.collect.await_count,1)
