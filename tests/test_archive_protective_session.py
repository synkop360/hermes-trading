"""Original eight-account inputs and real exclusive local owner; no installed worker mode."""
from copy import deepcopy
from datetime import timedelta
import json,secrets,sqlite3,unittest

import test_archive_protective_source as native
from hermes_trading.archive_protective_session import ProtectiveSession
from hermes_trading.archive_protective_transport import ProtectiveClient,ProtectiveServer
from hermes_trading.control_auth import SQLiteNonceReplayStore
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _connect


class ProtectiveSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=native.ProtectiveSourceTests();self.f.setUp()
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()
        self.genesis_bytes=self.f.path.read_bytes()
        self.owner=_connect(self.f.f.root/'worker_runtime.sqlite')
        self.owner.execute('CREATE TABLE lifecycle(id INTEGER PRIMARY KEY,state TEXT)');self.owner.commit();self.addCleanup(self.owner.close)
        self.repo=StateRepository.open(self.f.path.parent/'nonce.sqlite');self.repo.migrate();self.addCleanup(self.repo.close)
        key=secrets.token_hex(32)
        self.server=ProtectiveServer(self.f.path,self.f.binding,self.f.identity,key,SQLiteNonceReplayStore(self.repo),enabled=True)
        self.error=None
        def exchange(envelope,*,timeout_seconds,max_response_bytes):
            if self.error is not None:raise self.error
            return self.server.handle(envelope,now=self.f.f.at,timeout_seconds=timeout_seconds)
        self.client=ProtectiveClient(self.f.binding,self.f.identity,key,exchange,enabled=True,now=self.f.f.clock)
        self.session=self.make()

    def make(self,**kwargs):
        return ProtectiveSession(self.f.f.root,self.owner,self.f.binding,self.f.identity,self.client,
            enabled=kwargs.pop('enabled',True),now=self.f.f.clock,**kwargs)

    async def enter(self):
        self.f.apply=lambda head,view=None,action='check':self.session.reserve() if action=='reserve' else self.session.reconcile()
        return await self.f.enter()

    def outage(self):
        self.error=OSError('invented unreachable receiver')
        with self.assertRaises(OSError):self.session.reconcile()

    def test_disabled_and_wrong_or_released_owner_never_authorize(self):
        with self.assertRaises(ValueError):self.make(enabled=False)
        other=_connect(self.f.f.root/'wrong.sqlite');other.commit();self.addCleanup(other.close)
        with self.assertRaises(ValueError):ProtectiveSession(self.f.f.root,other,self.f.binding,self.f.identity,self.client,enabled=True)
        self.session.reconcile();self.outage();self.owner.close()
        with self.assertRaises((ValueError,sqlite3.Error)):self.session.claim_protection()

    async def test_native_normal_completion_outage_grant_is_durable_and_consumed_once(self):
        head=await self.enter();self.outage();token=self.session.claim_protection()
        self.assertEqual(token,head['state']['credit']['credit_id'])
        raw=self.owner.execute('SELECT payload FROM protective_cache').fetchone()[0]
        self.assertEqual(json.loads(raw)['spent_generation'],1)
        with self.assertRaises(ValueError):self.session.claim_protection()
        await self.f.protect(head)
        self.error=None;done=self.session.reconcile()
        self.assertEqual(done,self.f.head());self.assertEqual(done['state']['point']['complete'],2)
        self.assertIsNone(done['state']['credit']);self.assertEqual(self.f.f.market.collect.await_count,2)
        renewed=self.session.reserve();self.assertEqual(renewed['state']['credit']['generation'],2)

    async def test_cached_grant_cannot_authorize_after_restart_without_fresh_receiver(self):
        await self.enter();self.outage();fresh=self.make()
        with self.assertRaises(ValueError):fresh.claim_protection()
        with self.assertRaises(OSError):fresh.reconcile()
        with self.assertRaises(ValueError):fresh.claim_protection()
        self.error=None;fresh.reconcile();self.assertIsInstance(fresh.reserve(),dict)

    async def test_authentication_failure_is_not_an_eligible_outage(self):
        await self.enter();self.error=ValueError('bad MAC')
        with self.assertRaises(ValueError):self.session.reconcile()
        with self.assertRaises(ValueError):self.session.claim_protection()

    def test_lost_reserve_reply_does_not_enable_borrowing_unfrozen_reservation(self):
        self.session.reconcile();exchange=self.client.exchange
        def lost(envelope,**kwargs):
            result=exchange(envelope,**kwargs)
            if json.loads(envelope['body'])['action']=='reserve':raise OSError('lost reserve reply')
            return result
        self.client.exchange=lost
        with self.assertRaises(OSError):self.session.reserve()
        self.assertIsNotNone(self.f.head()['state']['reservation'])
        with self.assertRaises(ValueError):self.session.claim_protection()

    async def test_claim_without_original_packet_stays_ambiguous_on_restart(self):
        await self.enter();self.outage();self.session.claim_protection();self.error=None
        with self.assertRaisesRegex(ValueError,'missing|ambiguous'):self.make().reconcile()
        self.assertEqual(self.f.f.market.collect.await_count,1)

    async def test_original_partial_protection_replays_after_fresh_restart_reconciliation(self):
        from hermes_trading.paired_paper import process_paper_account
        head=await self.enter();self.outage();self.session.claim_protection()
        async def crash(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after account')
        with self.assertRaises(RuntimeError):await self.f.protect(head,process_account=crash)
        self.error=None;fresh=self.make();frozen=fresh.reconcile()
        self.assertEqual(fresh.replay_protection(),head['state']['credit']['credit_id'])
        await self.f.protect(frozen);self.assertEqual(fresh.reconcile()['state']['point']['complete'],2)
        self.assertEqual(self.f.f.market.collect.await_count,2)

    async def test_changed_local_cache_stops_live_session(self):
        head=await self.enter();self.session.reconcile()
        self.owner.execute("UPDATE protective_cache SET sha256=?",('f'*64,));self.owner.commit()
        with self.assertRaises(ValueError):self.session.reconcile()
        with self.assertRaises(ValueError):self.session.claim_protection()

    async def test_fresh_signed_but_regressed_receiver_head_is_rejected_after_restart(self):
        await self.enter();self.session.reconcile()
        self.f.path.write_bytes(self.genesis_bytes)
        with self.assertRaisesRegex(ValueError,'regressed'):self.make().reconcile()
        with self.assertRaisesRegex(ValueError,'regressed'):self.session.reconcile()

    async def test_failed_cache_commit_cannot_authorize_quotes(self):
        await self.enter();self.outage()
        self.owner.execute("CREATE TRIGGER refuse_claim BEFORE INSERT ON protective_cache BEGIN SELECT RAISE(ABORT,'invented full disk'); END")
        self.owner.commit()
        with self.assertRaises(sqlite3.Error):self.session.claim_protection()
        with self.assertRaises(ValueError):self.session.claim_protection()
        self.assertEqual(self.f.f.market.collect.await_count,1)

    async def test_original_deadline_and_capacity_remain_bounds(self):
        await self.enter();self.outage();self.f.f.at+=timedelta(days=181)
        with self.assertRaises(ValueError):self.session.claim_protection()
