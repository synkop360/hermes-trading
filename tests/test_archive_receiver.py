"""Original-history ownership/cache correctness; disposable synthetic receivers."""
from contextlib import closing
import json,os,sqlite3,unittest,uuid
from unittest.mock import patch

import test_archive_witness as kernel
import test_archive_witness_worker as native
from hermes_trading import archive_witness as witness
from hermes_trading.archive_receiver import VerifiedReceiver
from hermes_trading.archive_witness_transport import WitnessServer,WitnessClient
from hermes_trading.control_auth import SQLiteNonceReplayStore
from hermes_trading.persistence.repository import StateRepository


class VerifiedReceiverTests(unittest.TestCase):
    def setUp(self):
        self.f=kernel.WitnessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.original_head=self.f.head
        self.receiver=VerifiedReceiver(self.f.path,self.f.binding,self.f.identity,enabled=True)
        self.addCleanup(self.receiver.close);self.receiver.prepare(timeout_seconds=10)
        self.f.head=lambda:self.receiver.read()

    def act(self,view,action='check',**kwargs):
        return self.receiver.apply(view,action=action,expected_head_sha256=kwargs.pop('expected_head_sha256',self.f.head()['head_sha256']),
            operation_id=kwargs.pop('operation_id',uuid.uuid4().hex),now=kernel.NOW,**kwargs)

    def test_verified_owned_history_is_reused_without_dropping_original_events(self):
        with patch.object(witness,'_load',side_effect=AssertionError('history must not be rescanned while owned')):
            for count in range(1,4):
                token=self.act(self.f.view(count-1),action='reserve')['state']['reservation']['reservation_id']
                self.act(self.f.view(count,latest=token));self.receiver.read()
        self.receiver.close()
        with closing(sqlite3.connect(self.f.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM witness_events').fetchone()[0],7)
        self.assertEqual(self.original_head()['state']['point']['complete'],3)

    def test_other_sqlite_writer_cannot_change_verified_owned_history(self):
        before=self.receiver.read()
        with closing(sqlite3.connect(self.f.path,timeout=0)) as db:
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                db.execute("UPDATE witness_events SET sha256='broken' WHERE sequence=0")
        self.assertEqual(self.receiver.read(),before)

    def test_restart_fully_rechecks_old_prefix_not_just_current_tail(self):
        token=self.act(self.f.view(),action='reserve')['state']['reservation']['reservation_id']
        self.act(self.f.view(1,latest=token));self.receiver.close()
        with closing(sqlite3.connect(self.f.path)) as db:
            db.execute("UPDATE witness_events SET sha256=? WHERE sequence=1",('f'*64,));db.commit()
        with self.assertRaisesRegex(ValueError,'checksum|chain'):self.receiver.prepare(timeout_seconds=10)
        with self.assertRaisesRegex(ValueError,'prepar|ready'):self.receiver.read()

    def test_external_metadata_change_invalidates_until_full_preparation(self):
        before=self.receiver.read();stat=self.f.path.stat()
        os.utime(self.f.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+10_000_000))
        with self.assertRaisesRegex(ValueError,'changed|ownership'):self.receiver.read()
        with patch.object(witness,'_load',wraps=witness._load) as verifier:
            self.receiver.prepare(timeout_seconds=10);self.assertEqual(verifier.call_count,1)
        self.assertEqual(self.receiver.read(),before)

    def test_external_metadata_change_during_read_cannot_be_adopted_as_verified(self):
        calls=[0];stat=self.f.path.stat()
        def clock():
            calls[0]+=1
            if calls[0]==3:os.utime(self.f.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+10_000_000))
            return 0
        self.receiver.clock=clock
        with self.assertRaisesRegex(ValueError,'changed|ownership'):self.receiver.read()

    def test_precommit_failure_keeps_original_history_and_requires_preparation(self):
        before=self.receiver.read();view=self.f.view()
        def fail(point):
            if point=='before_commit':raise RuntimeError('before original commit')
        with self.assertRaisesRegex(RuntimeError,'before original commit'):
            self.act(view,action='reserve',fault=fail)
        self.assertEqual(self.original_head(),before)
        with self.assertRaisesRegex(ValueError,'prepar|ready'):self.receiver.read()
        self.receiver.prepare(timeout_seconds=10)
        self.assertEqual(self.receiver.read(),before)

    def test_retained_in_memory_floor_rejects_older_valid_database_on_reprepare(self):
        import shutil
        self.receiver.close();old=self.f.root/'old.sqlite';shutil.copy2(self.f.path,old)
        self.receiver.prepare(timeout_seconds=10)
        self.act(self.f.view(),action='reserve');self.receiver.close()
        shutil.copy2(old,self.f.path)
        with self.assertRaisesRegex(ValueError,'regress|floor|rollback'):self.receiver.prepare(timeout_seconds=10)

    def test_lost_commit_reply_rechecks_and_recovers_original_operation(self):
        view=self.f.view();head=self.receiver.read()['head_sha256'];operation=uuid.uuid4().hex
        def fault(point):
            if point=='after_commit':raise RuntimeError('lost receiver reply')
        with self.assertRaisesRegex(RuntimeError,'lost receiver reply'):
            self.act(view,action='reserve',operation_id=operation,expected_head_sha256=head,fault=fault)
        self.receiver.prepare(timeout_seconds=10)
        retry=self.act(view,action='reserve',operation_id=operation,expected_head_sha256=head)
        self.assertEqual(retry['sequence'],1);self.assertEqual(retry['state']['reservation']['reservation_id'],operation)

    def test_default_disabled_and_separate_startup_request_deadlines(self):
        disabled=VerifiedReceiver(self.f.root/'never-created.sqlite',self.f.binding,self.f.identity)
        with self.assertRaisesRegex(ValueError,'disabled'):disabled.prepare(timeout_seconds=10)
        self.assertFalse((self.f.root/'never-created.sqlite').exists())
        with self.assertRaises(ValueError):self.receiver.read(timeout_seconds=11)
        with self.assertRaises(ValueError):self.receiver.prepare(timeout_seconds=121)


class CachedAuthenticatedWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import secrets
        self.f=native.NativeWitnessWorkerTests();self.f.setUp()
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()
        self.receiver=VerifiedReceiver(self.f.path,self.f.binding,self.f.identity,enabled=True)
        self.addCleanup(self.receiver.close);self.receiver.prepare(timeout_seconds=10)
        self.f.head=lambda:self.receiver.read()
        self.repo=StateRepository.open(self.f.path.parent/'nonces.sqlite');self.repo.migrate();self.addCleanup(self.repo.close)
        secret=secrets.token_hex(32)
        self.server=WitnessServer(self.f.path,self.f.binding,self.f.identity,secret,SQLiteNonceReplayStore(self.repo),enabled=True,receiver=self.receiver)
        def exchange(envelope,*,timeout_seconds,max_response_bytes):
            return self.server.handle(envelope,now=self.f.f.at,timeout_seconds=timeout_seconds)
        self.f.client=WitnessClient(self.f.binding,self.f.identity,secret,exchange,enabled=True,now=self.f.f.clock)

    async def test_native_signed_worker_reuses_verified_prefix_and_preserves_bound(self):
        with patch.object(witness,'_load',side_effect=AssertionError('native requests must reuse verified receiver history')):
            result=await self.f.invoke();await self.f.invoke()
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(self.receiver.read()['state']['point']['complete'],2)
        self.assertEqual(self.f.f.market.collect.await_count,2)
        for profile in self.f.f.manifest['profiles']:
            self.assertEqual(len(self.f.f.positions(profile['directory'])),1)


if __name__=='__main__':unittest.main()
