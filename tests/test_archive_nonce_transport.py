"""Admitted V2 request composition on disposable original receiver evidence."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import secrets
import sqlite3
import unittest
import uuid
from unittest.mock import patch

import test_archive_protective_witness as kernel
from hermes_trading.archive_capacity import StorageDomain,CapacityBusy,CapacityExceeded
from hermes_trading.archive_nonce import BoundedArchiveNonceStore,NONCE_PEAK_BYTES
from hermes_trading.archive_protective_receiver import ProtectiveReceiver
from hermes_trading.archive_protective_transport import ProtectiveServer,REQUEST_PURPOSE
from hermes_trading import archive_protective_witness as witness
from hermes_trading import archive_witness_transport as wire
from hermes_trading.reflection_supervision import _hash


class AdmittedProtectiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.f=kernel.ProtectiveWitnessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.domain=StorageDomain(self.f.path.parent,domain_id='admitted-receiver',
            limit_bytes=16*1024**2,reserve_bytes=1024**2,enabled=True);self.domain.initialize()
        self.owner=self.enterContext(self.domain.ownership());self.nonce_path=self.f.path.parent/'bounded-nonce.sqlite'
        options=dict(owner=self.owner,expected_store_id=uuid.uuid4().hex,binding=self.f.binding,identity=self.f.identity,enabled=True)
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:BoundedArchiveNonceStore.initialize(self.nonce_path,lease=lease,**options)
        self.store=BoundedArchiveNonceStore(self.nonce_path,**options)
        self.receiver=ProtectiveReceiver(self.f.path,self.f.binding,self.f.identity,enabled=True,max_bytes=1024**2,clock=self.domain.clock)
        self.addCleanup(self.receiver.close)
        with self.owner.admit(1024**2) as lease:
            lease.require_path(self.f.path);self.receiver.prepare(timeout_seconds=10)
        self.secret=secrets.token_hex(32)
        self.server=ProtectiveServer(self.f.path,self.f.binding,self.f.identity,self.secret,self.store,
            enabled=True,max_db_bytes=1024**2,receiver=self.receiver,clock=self.domain.clock)

    def request(self,action='read',*,expected=None,operation=None,at=None):
        head=self.receiver.read()
        body=dict(schema_version=2,purpose=REQUEST_PURPOSE,experiment_id=self.f.identity['experiment_id'],
            binding=self.f.binding,identity_sha256=_hash(self.f.identity),action=action,
            view=self.f.view(head) if action!='read' else None,
            expected_head_sha256=(expected or head['head_sha256']) if action!='read' else None,
            operation_id=(operation or uuid.uuid4().hex) if action!='read' else None)
        return wire._signed(self.secret,uuid.uuid4().hex,at or self.f.now,body)

    def rows(self):
        with closing(sqlite3.connect(self.nonce_path.as_uri()+'?mode=ro',uri=True)) as db:
            return db.execute('SELECT nonce FROM nonce_entries').fetchall()

    def test_signed_request_commits_nonce_before_receiver_and_retains_combined_owner(self):
        envelope=self.request();original=self.receiver.read
        def inspect(**kwargs):
            with closing(sqlite3.connect(self.nonce_path,timeout=0,isolation_level=None)) as db:
                db.execute('BEGIN EXCLUSIVE');db.rollback()
            with self.assertRaises(CapacityBusy):
                with self.owner.admit(1):pass
            return original(**kwargs)
        with patch.object(self.receiver,'read',side_effect=inspect):
            response=self.server.handle(envelope,now=self.f.now)
        self.assertEqual(response['nonce'],envelope['nonce']);self.assertEqual(self.rows(),[(envelope['nonce'],)])
        before=self.nonce_path.read_bytes()
        with self.assertRaisesRegex(ValueError,'authentication|replay'):self.server.handle(envelope,now=self.f.now)
        self.assertEqual(self.nonce_path.read_bytes(),before)

    def test_bad_mac_future_and_exact_freshness_boundary_leave_nonce_untouched(self):
        bad=self.request();bad['signature']='0'*64
        requests=[(bad,self.f.now),(self.request(at=self.f.now+timedelta(microseconds=1)),self.f.now),
            (self.request(),self.f.now+timedelta(seconds=300))]
        before=self.nonce_path.read_bytes()
        for envelope,now in requests:
            with self.subTest(timestamp=envelope['timestamp']),patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
                with self.assertRaises(ValueError):self.server.handle(envelope,now=now)
                connection.assert_not_called()
            self.assertEqual(self.nonce_path.read_bytes(),before)

    def test_combined_capacity_denial_precedes_nonce_or_receiver_sql(self):
        envelope=self.request();before=self.nonce_path.read_bytes()
        with self.owner.admit(11*1024**2):
            (self.f.path.parent/'owned-capacity-padding').write_bytes(b'x'*(11*1024**2))
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection,patch.object(self.receiver,'read') as read:
            with self.assertRaises(CapacityExceeded):self.server.handle(envelope,now=self.f.now)
            connection.assert_not_called();read.assert_not_called()
        self.assertEqual(self.nonce_path.read_bytes(),before)

    def test_accepted_nonce_survives_receiver_cas_failure(self):
        envelope=self.request('reserve',expected='f'*64);before=self.f.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'expected.*head'):self.server.handle(envelope,now=self.f.now)
        self.assertEqual(self.rows(),[(envelope['nonce'],)]);self.assertEqual(self.f.path.read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'authentication|replay'):self.server.handle(envelope,now=self.f.now)

    def test_free_space_drop_after_nonce_commit_denies_receiver_and_keeps_replay(self):
        envelope=self.request();before=self.f.path.read_bytes();committed=[False]
        def fault(point):
            if point=='after_commit':committed[0]=True
        self.store.fault=fault
        def disk_usage(path):
            return SimpleNamespace(free=self.domain.reserve_bytes+131072 if committed[0] else 32*1024**2)
        with patch('hermes_trading.archive_capacity.shutil.disk_usage',side_effect=disk_usage),patch.object(self.receiver,'read') as read:
            with self.assertRaises(CapacityExceeded):self.server.handle(envelope,now=self.f.now)
            read.assert_not_called()
        self.assertTrue(committed[0]);self.assertEqual(self.rows(),[(envelope['nonce'],)])
        self.assertEqual(self.f.path.read_bytes(),before)

    def test_lost_reply_retries_new_nonce_with_original_operation_without_new_event(self):
        envelope=self.request('reserve');first=self.server.handle(envelope,now=self.f.now)
        request=json.loads(envelope['body'])
        retry=wire._signed(self.secret,uuid.uuid4().hex,self.f.now,request)
        second=self.server.handle(retry,now=self.f.now)
        self.assertEqual(json.loads(first['body'])['head'],json.loads(second['body'])['head'])
        self.assertEqual(len(self.rows()),2);self.assertEqual(self.receiver.read()['sequence'],1)

    def test_different_receiver_clock_is_rejected_before_nonce_acceptance(self):
        self.receiver.clock=lambda:0
        before=self.nonce_path.read_bytes()
        with self.assertRaises(ValueError):self.server.handle(self.request(),now=self.f.now)
        self.assertEqual(self.nonce_path.read_bytes(),before)

    def test_prepared_dispatch_does_not_extend_original_absolute_deadline(self):
        clock=[100.0];shared_clock=lambda:clock[0]
        self.domain.clock=shared_clock;self.server.clock=shared_clock;self.receiver.clock=shared_clock
        envelope=self.request('reserve');before=self.f.path.read_bytes();apply=self.receiver.apply
        def delayed(*args,**kwargs):
            clock[0]=111
            return apply(*args,**kwargs)
        with patch.object(self.receiver,'apply',side_effect=delayed):
            with self.assertRaises(TimeoutError):self.server.handle(envelope,now=self.f.now)
        clock[0]=100
        self.assertEqual(self.f.path.read_bytes(),before)
        self.assertEqual(self.rows(),[(envelope['nonce'],)])

    def test_stateless_precommit_budget_clamps_to_original_request_deadline(self):
        envelope=self.request('reserve');request=json.loads(envelope['body']);self.receiver.close()
        before=self.f.path.read_bytes();clock=[100.0]
        def expire(point):
            if point=='before_commit':clock[0]=106
        with self.assertRaises(TimeoutError):
            witness.apply(self.f.path,self.f.binding,self.f.identity,request['view'],action='reserve',
                expected_head_sha256=request['expected_head_sha256'],operation_id=request['operation_id'],
                enabled=True,now=self.f.now,max_bytes=1024**2,timeout_seconds=10,clock=lambda:clock[0],
                request_deadline=105,fault=expire)
        self.assertEqual(self.f.path.read_bytes(),before)


class AdmittedProtectiveWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_eight_wallet_outage_and_reconnect_use_bounded_nonce_requests(self):
        import test_archive_protective_worker as native
        f=native.ProtectiveWorkerTests();f.setUp()
        for function,args,kwargs in f._cleanups:self.addCleanup(function,*args,**kwargs)
        f._cleanups.clear()
        f.repo.close()  # Superseded fixture adapter; no old handle outlives the admitted owner.
        domain=StorageDomain(f.f.path.parent,domain_id='eight-wallet-admitted',limit_bytes=16*1024**2,
            reserve_bytes=1024**2,enabled=True);domain.initialize()
        owner=self.enterContext(domain.ownership());nonce_path=f.f.path.parent/'bounded-worker-nonce.sqlite'
        options=dict(owner=owner,expected_store_id=uuid.uuid4().hex,binding=f.f.binding,identity=f.f.identity,enabled=True)
        with owner.admit(NONCE_PEAK_BYTES) as lease:BoundedArchiveNonceStore.initialize(nonce_path,lease=lease,**options)
        store=BoundedArchiveNonceStore(nonce_path,**options)
        receiver=ProtectiveReceiver(f.f.path,f.f.binding,f.f.identity,enabled=True,max_bytes=1024**2,clock=domain.clock)
        self.addCleanup(receiver.close)
        with owner.admit(1024**2):receiver.prepare(timeout_seconds=10)
        f.f.head=receiver.read
        f.server=ProtectiveServer(f.f.path,f.f.binding,f.f.identity,f.server.secret,store,
            enabled=True,max_db_bytes=1024**2,clock=domain.clock,receiver=receiver)
        result=await f.invoke(f.outage);self.assertEqual(result['completed_batches'],2)
        self.assertEqual(result['status'],'waiting_for_archive')
        f.error=None;done=await f.invoke()
        self.assertEqual(done['stop_reason'],'cycle_bound');self.assertEqual(receiver.read()['state']['point']['complete'],2)
        self.assertEqual(f.f.f.market.collect.await_count,2)
        for profile in f.f.f.manifest['profiles']:
            self.assertEqual(f.f.f.positions(profile['directory'])[0].status.value,'closed')


if __name__=='__main__':unittest.main()
