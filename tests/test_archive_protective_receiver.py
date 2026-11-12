"""Prepared V2 ownership/prefix correctness; disposable original histories."""
from contextlib import closing
from copy import deepcopy
import os,sqlite3,threading,unittest,uuid
from unittest.mock import patch

import test_archive_protective_witness as kernel
from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_protective_receiver import ProtectiveReceiver


class ProtectiveReceiverTests(unittest.TestCase):
    def setUp(self):
        self.f=kernel.ProtectiveWitnessTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.genesis_bytes=self.f.path.read_bytes()
        self.receiver=ProtectiveReceiver(self.f.path,self.f.binding,self.f.identity,enabled=True)
        self.addCleanup(self.receiver.close);self.receiver.prepare(timeout_seconds=10)
        self.f.head=self.receiver.read

    def act(self,view=None,action='check',operation=None,expected=None,fault=None):
        head=self.receiver.read()
        return self.receiver.apply(view or self.f.view(head),action=action,
            expected_head_sha256=expected or head['head_sha256'],operation_id=operation or uuid.uuid4().hex,
            now=self.f.now,fault=fault)

    def test_owned_prefix_is_reused_and_original_credit_transitions_are_preserved(self):
        with patch.object(witness,'_load',side_effect=AssertionError('owned prefix must not be rescanned')):
            head=self.act(action='reserve')
            normal=self.f.completion(head,'ordinary')
            protected=self.f.completion(head,'protective',sequence=2,previous=normal['prefix_sha256'])
            head=self.act(self.f.view(head,suffix=[normal,protected]))
            self.assertEqual(head['state']['last_consumed_generation'],1)
            head=self.act(action='reserve');self.assertEqual(head['state']['credit']['generation'],2)
            self.assertEqual(self.receiver.read(),head)
        self.receiver.close()
        self.assertEqual(witness.read(self.f.path,self.f.binding,self.f.identity,enabled=True),head)
        with closing(sqlite3.connect(self.f.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM protective_events').fetchone()[0],4)

    def test_another_writer_and_foreign_thread_cannot_borrow_ownership(self):
        head=self.receiver.read()
        with closing(sqlite3.connect(self.f.path,timeout=0)) as db:
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):db.execute("UPDATE protective_events SET sha256='bad'")
        errors=[]
        def foreign():
            try:self.receiver.read()
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=foreign);thread.start();thread.join(timeout=5)
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],ValueError)
        self.assertEqual(self.receiver.read(),head)

    def test_reprepare_rechecks_earlier_original_event_not_only_tail(self):
        self.act(action='reserve');self.receiver.close()
        with closing(sqlite3.connect(self.f.path)) as db:
            db.execute("UPDATE protective_events SET sha256=? WHERE sequence=0",('f'*64,));db.commit()
        with self.assertRaises(ValueError):self.receiver.prepare(timeout_seconds=10)
        with self.assertRaisesRegex(ValueError,'prepar'):self.receiver.read()

    def test_external_file_change_invalidates_until_full_preparation(self):
        head=self.receiver.read();stat=self.f.path.stat()
        os.utime(self.f.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+10_000_000))
        with self.assertRaisesRegex(ValueError,'changed'):self.receiver.read()
        with patch.object(witness,'_load',wraps=witness._load) as load:
            self.receiver.prepare(timeout_seconds=10);self.assertEqual(load.call_count,1)
        self.assertEqual(self.receiver.read(),head)

    def test_before_commit_failure_rolls_back_and_requires_preparation(self):
        head=self.receiver.read()
        def fail(point):
            if point=='before_commit':raise RuntimeError('before durable commit')
        with self.assertRaises(RuntimeError):self.act(action='reserve',fault=fail)
        with self.assertRaises(ValueError):self.receiver.read()
        self.assertEqual(self.receiver.prepare(timeout_seconds=10),head)

    def test_lost_reply_reprepare_retries_same_operation_without_another_event(self):
        head=self.receiver.read();view=self.f.view(head);operation=uuid.uuid4().hex
        def fail(point):
            if point=='after_commit':raise RuntimeError('lost reply')
        with self.assertRaises(RuntimeError):self.act(view,'reserve',operation,head['head_sha256'],fail)
        committed=self.receiver.prepare(timeout_seconds=10)
        self.assertEqual(self.act(view,'reserve',operation,head['head_sha256']),committed)
        self.assertEqual(committed['sequence'],1)

    def test_lost_reply_floor_cannot_be_rolled_back_to_valid_genesis(self):
        def fail(point):
            if point=='after_commit':raise RuntimeError('lost reply')
        with self.assertRaises(RuntimeError):self.act(action='reserve',fault=fail)
        self.f.path.write_bytes(self.genesis_bytes)
        with self.assertRaisesRegex(ValueError,'floor|regress'):self.receiver.prepare(timeout_seconds=10)

    def test_returns_are_detached_and_noop_requests_do_not_append(self):
        head=self.receiver.read();changed=deepcopy(head);changed['state']['credit']['generation']=99
        self.assertEqual(self.receiver.read(),head);self.assertEqual(self.act(),head);self.assertEqual(self.act(),head)

    def test_disabled_binding_and_separate_deadline_limits(self):
        disabled=ProtectiveReceiver(self.f.path,self.f.binding,self.f.identity)
        with self.assertRaises(ValueError):disabled.prepare()
        with self.assertRaises(ValueError):ProtectiveReceiver(self.f.path,{'witness_id':'a'*32,'genesis_sha256':'b'*64},self.f.identity)
        with self.assertRaises(ValueError):self.receiver.read(timeout_seconds=11)
        with self.assertRaises(ValueError):self.receiver.prepare(timeout_seconds=121)


class PreparedProtectiveWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_actual_worker_outage_and_reconnect_reuse_verified_receiver(self):
        import test_archive_protective_worker as native
        from hermes_trading.archive_protective_transport import ProtectiveServer
        f=native.ProtectiveWorkerTests();f.setUp()
        # These are fixture instances, not independently run async TestCases.
        # Their async doCleanups would require their own initialized runner.
        # Register their actual owned resources on this running test instead.
        def cleanup_fixture():
            receiver_parent=f.f.path.parent;cohort_root=f.f.f.root
            f.repo.close();f.f.tmp.cleanup();f.f.f.tearDown()
            self.assertFalse(receiver_parent.exists());self.assertFalse(cohort_root.exists())
        self.addCleanup(cleanup_fixture)
        receiver=ProtectiveReceiver(f.f.path,f.f.binding,f.f.identity,enabled=True)
        self.addCleanup(receiver.close);receiver.prepare(timeout_seconds=10);f.f.head=receiver.read
        f.server=ProtectiveServer(f.f.path,f.f.binding,f.f.identity,f.server.secret,f.server.nonce_store,
            enabled=True,receiver=receiver)
        with patch.object(witness,'_load',side_effect=AssertionError('native requests must reuse owned verified history')):
            result=await f.invoke(f.outage);self.assertEqual(result['completed_batches'],2)
            f.error=None;done=await f.invoke()
        self.assertEqual(done['stop_reason'],'cycle_bound');self.assertEqual(receiver.read()['state']['point']['complete'],2)
        self.assertEqual(f.f.f.market.collect.await_count,2)
