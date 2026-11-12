"""Owner-held original receiver export/restore checks, synthetic state only."""
from contextlib import closing
from pathlib import Path
import errno,hashlib,json,sqlite3,time,unittest,uuid,zipfile

import test_archive_receiver as fixtures
from hermes_trading.archive_receiver import VerifiedReceiver
from hermes_trading.archive_receiver_export import restore_receiver_export
from hermes_trading import archive_receiver_export as exports
from hermes_trading.reflection_supervision import _hash,_json


class ReceiverExportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.VerifiedReceiverTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.receiver=self.f.receiver;self.root=self.f.f.root;self.target=self.root/'evidence.zip'

    def cycle(self,count):
        token=self.f.act(self.f.f.view(count-1),action='reserve')['state']['reservation']['reservation_id']
        return self.f.act(self.f.f.view(count,latest=token))

    def export(self,**kwargs):
        return self.receiver.export(self.target,**dict({'enabled':True,'expected_head_sha256':self.receiver.read()['head_sha256'],
            'max_bytes':1024*1024,'timeout_seconds':10},**kwargs))

    def restore(self,receipt,target=None,**kwargs):
        return restore_receiver_export(self.target,target or self.root/'restored.sqlite',
            **dict({'enabled':True,'expected_sha256':receipt['archive_sha256'],
            'binding':self.f.f.binding,'identity':self.f.f.identity,'expected_head_sha256':receipt['head']['head_sha256'],
            'max_bytes':1024*1024,'timeout_seconds':10},**kwargs))

    def test_complete_original_rows_restore_and_keep_original_cycle_bound(self):
        self.cycle(1);before=self.cycle(2);receipt=self.export()
        self.assertEqual(self.receiver.read(),before)
        with closing(sqlite3.connect(self.f.f.path,timeout=0)) as other:
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):other.execute('SELECT * FROM witness_events')
        self.receiver.close();result=self.restore(receipt);restored=self.root/'restored.sqlite'
        self.assertEqual(result['head'],before);self.assertFalse(result['retirement_authorized'])
        with closing(sqlite3.connect(self.f.f.path)) as original,closing(sqlite3.connect(restored)) as copied:
            for table in ('witness_meta','witness_events'):
                self.assertEqual(original.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),copied.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall())
        clone=VerifiedReceiver(restored,self.f.f.binding,self.f.f.identity,enabled=True);self.addCleanup(clone.close)
        clone.prepare(timeout_seconds=10);self.f.f.head=lambda:clone.read()
        token=clone.apply(self.f.f.view(2),action='reserve',expected_head_sha256=clone.read()['head_sha256'],now=fixtures.kernel.NOW)['state']['reservation']['reservation_id']
        clone.apply(self.f.f.view(3,latest=token),expected_head_sha256=clone.read()['head_sha256'],now=fixtures.kernel.NOW)
        with self.assertRaisesRegex(ValueError,'cycle bound'):
            clone.apply(self.f.f.view(3),action='reserve',expected_head_sha256=clone.read()['head_sha256'],now=fixtures.kernel.NOW)

    def test_disabled_and_budget_failures_preserve_healthy_source(self):
        before=self.receiver.read()
        with self.assertRaisesRegex(ValueError,'disabled'):self.export(enabled=False)
        with self.assertRaisesRegex(ValueError,'byte|budget'):self.export(max_bytes=1)
        self.assertFalse(self.target.exists());self.assertEqual(self.receiver.read(),before)

    def test_disk_full_or_lost_export_reply_never_changes_receiver_history(self):
        before=self.cycle(1)
        def full(point,stage):
            if point=='after_backup':raise OSError(errno.ENOSPC,'invented full destination')
        with self.assertRaises(OSError):self.export(fault=full)
        self.assertFalse(self.target.exists());self.assertEqual(self.receiver.read(),before)
        self.assertEqual(list(self.root.glob('.rx-*')),[])
        def lost(point,stage):
            if point=='after_publish':raise RuntimeError('lost export reply')
        with self.assertRaisesRegex(RuntimeError,'lost export reply'):self.export(fault=lost)
        self.assertTrue(self.target.exists());self.assertEqual(self.receiver.read(),before)

    def test_mutated_copy_cannot_be_published_and_source_remains_ready(self):
        before=self.cycle(1)
        def corrupt(point,stage):
            if point=='after_backup':
                with closing(sqlite3.connect(stage/'receiver.sqlite')) as db:
                    db.execute("UPDATE witness_events SET payload='corrupt' WHERE sequence=1");db.commit()
        with self.assertRaisesRegex(ValueError,'copy|digest|differ'):self.export(fault=corrupt)
        self.assertFalse(self.target.exists());self.assertEqual(self.receiver.read(),before)

    def test_existing_or_stale_target_is_not_overwritten_or_reauthorized(self):
        self.cycle(1);receipt=self.export();raw=self.target.read_bytes()
        with self.assertRaisesRegex((ValueError,FileExistsError),'exist|new'):self.export()
        self.assertEqual(self.target.read_bytes(),raw)
        current=self.cycle(2)
        with self.assertRaisesRegex(ValueError,'head|cut'):
            self.restore(receipt,expected_head_sha256=current['head_sha256'])
        self.assertFalse((self.root/'restored.sqlite').exists())

    def test_pending_reservation_is_preserved_and_never_implicitly_cancelled(self):
        self.f.act(self.f.f.view(),action='reserve');before=self.receiver.read();receipt=self.export()
        self.assertTrue(receipt['reservation_pending']);self.receiver.close();self.restore(receipt)
        clone=VerifiedReceiver(self.root/'restored.sqlite',self.f.f.binding,self.f.f.identity,enabled=True)
        self.addCleanup(clone.close);self.assertEqual(clone.prepare(timeout_seconds=10),before)
        self.f.f.head=lambda:clone.read()
        with self.assertRaisesRegex(ValueError,'unresolved|original packet'):
            clone.apply(self.f.f.view(),action='reserve',expected_head_sha256=before['head_sha256'],now=fixtures.kernel.NOW)

    def test_hash_pin_and_target_rules_reject_before_restore_publication(self):
        receipt=self.export();target=self.root/'restored.sqlite'
        with self.assertRaisesRegex(ValueError,'digest|hash'):self.restore(receipt,expected_sha256='f'*64)
        self.assertFalse(target.exists());target.write_bytes(b'keep original')
        with self.assertRaisesRegex((ValueError,FileExistsError),'exist|new'):self.restore(receipt)
        self.assertEqual(target.read_bytes(),b'keep original')

    def test_target_appearing_at_publication_is_never_replaced(self):
        before=self.receiver.read()
        def appear(point,stage):
            if point=='before_publish':self.target.write_bytes(b'keep concurrent file')
        with self.assertRaises(FileExistsError):self.export(fault=appear)
        self.assertEqual(self.target.read_bytes(),b'keep concurrent file')
        self.assertEqual(self.receiver.read(),before)

    def test_expired_export_never_publishes_and_keeps_source_ready(self):
        before=self.receiver.read();elapsed=[0];self.receiver.clock=lambda:elapsed[0]
        def expire(point,stage):
            if point=='after_backup':elapsed[0]=2
        with self.assertRaises(TimeoutError):self.export(timeout_seconds=1,fault=expire)
        self.assertFalse(self.target.exists());self.assertEqual(self.receiver.read(),before)

    def test_deadline_is_checked_while_writing_the_final_archive(self):
        before=self.receiver.read();elapsed=[0];self.receiver.clock=lambda:elapsed[0]
        def expire(point,stage):
            if point=='archive_chunk':elapsed[0]=2
        with self.assertRaises(TimeoutError):self.export(timeout_seconds=1,fault=expire)
        self.assertFalse(self.target.exists());self.assertEqual(self.receiver.read(),before)

    def test_forged_checksums_cannot_replace_full_original_event_verification(self):
        self.cycle(1);receipt=self.export()
        with zipfile.ZipFile(self.target) as archive:
            raw=archive.read('receiver.sqlite');certificate=json.loads(archive.read('certificate.json'))
        corrupt=self.root/'corrupt.sqlite';corrupt.write_bytes(raw)
        with closing(sqlite3.connect(corrupt)) as db:
            event=json.loads(db.execute('SELECT payload FROM witness_events WHERE sequence=1').fetchone()[0])
            event['purpose']='forged-but-self-checksummed-export'
            db.execute('UPDATE witness_events SET payload=? WHERE sequence=1',(_json(event),));db.commit()
            certificate.update(exports._fingerprint(db,exports._Budget(1024*1024,10,120,time.monotonic)))
        raw=corrupt.read_bytes();certificate.update(database_bytes=len(raw),database_sha256=hashlib.sha256(raw).hexdigest())
        with zipfile.ZipFile(self.target,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
            archive.write(corrupt,'receiver.sqlite');archive.writestr('certificate.json',_json(certificate))
        new_hash=hashlib.sha256(self.target.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'checksum|chain'):
            self.restore(receipt,expected_sha256=new_hash)
        self.assertFalse((self.root/'restored.sqlite').exists())


class NativeCohortReceiverRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.CachedAuthenticatedWorkerTests();self.f.setUp()
        roots=(self.f.f.path.parent,self.f.f.f.root)
        self.addCleanup(lambda:[self.assertFalse(path.exists(),'nested fixture cleanup must finish on the active runner') for path in roots])
        # Reused async test cases have no runner of their own. Route their
        # registered callbacks to this active case instead of swallowing errors.
        def transfer(fixture):
            for function,args,kwargs in fixture._cleanups:
                nested=getattr(function,'__self__',None)
                if getattr(function,'__name__','')=='doCleanups' and isinstance(nested,unittest.TestCase):transfer(nested)
                else:self.addCleanup(function,*args,**kwargs)
            fixture._cleanups.clear()
        transfer(self.f)

    async def test_all_eight_accounts_and_receiver_restore_without_extra_collection(self):
        from hermes_trading.archive_snapshot import snapshot
        from hermes_trading.archive_witness_transport import WitnessServer,WitnessClient
        from hermes_trading.control_auth import SQLiteNonceReplayStore
        from hermes_trading.paired_worker import run_worker
        worker=self.f.f.f;await self.f.f.invoke()
        restored_root=self.f.f.path.parent/'restored-worker'
        snapshot(worker.root,restored_root,enabled=True,max_bytes=32*1024*1024,timeout_seconds=30)
        artifact=self.f.f.path.parent/'receiver.zip';head=self.f.receiver.read()
        receipt=self.f.receiver.export(artifact,enabled=True,expected_head_sha256=head['head_sha256'],max_bytes=1024*1024,timeout_seconds=10)
        self.f.receiver.close()
        restored_receiver=self.f.f.path.parent/'restored-receiver.sqlite'
        restore_receiver_export(artifact,restored_receiver,enabled=True,expected_sha256=receipt['archive_sha256'],
            binding=self.f.f.binding,identity=self.f.f.identity,expected_head_sha256=head['head_sha256'],
            max_bytes=1024*1024,timeout_seconds=10)
        for profile in worker.manifest['profiles']:
            source=worker.root/profile['directory']/'trading.db';copied=restored_root/profile['directory']/'trading.db'
            with closing(sqlite3.connect(source)) as original,closing(sqlite3.connect(copied)) as restored:
                tables=original.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                for (table,) in tables:
                    self.assertEqual(original.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),
                        restored.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),(profile['directory'],table))
        clone=VerifiedReceiver(restored_receiver,self.f.f.binding,self.f.f.identity,enabled=True);self.addCleanup(clone.close)
        self.assertEqual(clone.prepare(timeout_seconds=10),head)
        server=WitnessServer(restored_receiver,self.f.f.binding,self.f.f.identity,self.f.server.secret,
            SQLiteNonceReplayStore(self.f.repo),enabled=True,receiver=clone)
        def exchange(envelope,**kwargs):return server.handle(envelope,now=worker.at,timeout_seconds=kwargs['timeout_seconds'])
        client=WitnessClient(self.f.f.binding,self.f.f.identity,self.f.server.secret,exchange,enabled=True,now=worker.clock)
        result=await run_worker(restored_root,collector_factory=lambda:worker.market,clock=worker.clock,sleep=worker.sleep,witness=client)
        self.assertEqual(result['completed_batches'],2);self.assertEqual(result['stop_reason'],'cycle_bound')
        self.assertEqual(worker.market.collect.await_count,2)


if __name__=='__main__':unittest.main()
