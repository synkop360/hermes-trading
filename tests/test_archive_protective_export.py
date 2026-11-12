"""Exact-cut paired export; synthetic copies never authorize live recovery."""
from contextlib import closing
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
import zipfile
from unittest.mock import patch

import test_archive_nonce_transport as fixtures
from hermes_trading.archive_protective_export import export_pair,restore_pair
from hermes_trading.archive_capacity import CapacityExceeded
from hermes_trading.archive_nonce import BoundedArchiveNonceStore
from hermes_trading.archive_protective_receiver import ProtectiveReceiver


class ProtectiveExportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AdmittedProtectiveTransportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.f.path.parent;self.target=self.root/'pair.zip';self.restored=self.root/'restored-pair'
        self.first=self.f.request();self.f.server.handle(self.first,now=self.f.f.now)
        self.store_id=self.f.store._core['store_id']

    def export(self,**changes):
        options=dict(enabled=True,expected_head_sha256=self.f.receiver.read()['head_sha256'],max_bytes=2*1024**2,timeout_seconds=10)
        options.update(changes)
        return export_pair(self.f.receiver,self.f.store,self.target,**options)

    def restore(self,receipt,**changes):
        options=dict(owner=self.f.owner,enabled=True,expected_archive_sha256=receipt['archive_sha256'],
            expected_certificate_sha256=receipt['certificate_sha256'],binding=self.f.f.binding,identity=self.f.f.identity,
            expected_nonce_store_id=self.store_id,expected_head_sha256=receipt['head_sha256'],max_bytes=2*1024**2,timeout_seconds=10)
        options.update(changes)
        return restore_pair(self.target,self.restored,**options)

    def test_exact_pair_roundtrip_preserves_nonce_and_pending_reservation(self):
        self.f.server.handle(self.f.request('reserve'),now=self.f.f.now)
        head=self.f.receiver.read();source=(self.f.store.path.read_bytes(),self.f.receiver.path.read_bytes())
        receipt=self.export();restored=self.restore(receipt)
        self.assertEqual((self.f.store.path.read_bytes(),self.f.receiver.path.read_bytes()),source)
        self.assertFalse(restored['runtime_resumption_authorized']);self.assertFalse(restored['independent_backup_verified'])
        self.assertFalse(restored['retirement_authorized'])
        self.assertEqual({p.name for p in self.restored.iterdir()},
            {'receiver.sqlite','nonces.sqlite','certificate.json','RESTORE_COMPLETE.json'})
        receiver=ProtectiveReceiver(self.restored/'receiver.sqlite',self.f.f.binding,self.f.f.identity,
            enabled=True,max_bytes=1024**2,clock=self.f.domain.clock)
        try:
            with self.f.owner.admit(1024**2):self.assertEqual(receiver.prepare(timeout_seconds=10),head)
        finally:receiver.close()
        store=BoundedArchiveNonceStore(self.restored/'nonces.sqlite',owner=self.f.owner,
            expected_store_id=self.store_id,binding=self.f.f.binding,identity=self.f.f.identity,enabled=True)
        with self.f.owner.admit(4*1024**2) as lease,store.armed(lease,deadline=self.f.domain.clock()+5):
            self.assertFalse(store.add_once(self.first['nonce'],accepted_at=self.f.f.now,
                expires_at=self.f.f.now+timedelta(seconds=300)))
        self.assertEqual(receipt['nonce_rows'],2)

    def test_old_artifact_with_same_head_and_acceptance_time_fails_new_external_cut(self):
        first=self.export();old=self.target.read_bytes()
        self.target.rename(self.root/'old-pair.zip')
        self.f.server.handle(self.f.request(),now=self.f.f.now)
        new=self.export();self.assertEqual(first['head_sha256'],new['head_sha256'])
        self.assertNotEqual(first['nonce_rows_sha256'],new['nonce_rows_sha256'])
        self.assertNotEqual(first['archive_sha256'],new['archive_sha256'])
        self.target.write_bytes(old)
        with self.assertRaises(ValueError):self.restore(new)
        self.assertFalse(self.restored.exists())

    def test_wrong_generation_certificate_or_existing_target_are_never_adopted(self):
        receipt=self.export()
        for changes in ({'expected_nonce_store_id':'f'*32},{'expected_certificate_sha256':'f'*64}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.restore(receipt,**changes)
            self.assertFalse(self.restored.exists())
        self.restored.mkdir();sentinel=self.restored/'keep';sentinel.write_bytes(b'original')
        with self.assertRaises((ValueError,FileExistsError)):self.restore(receipt)
        self.assertEqual(sentinel.read_bytes(),b'original')

    def test_restore_failure_between_copies_never_publishes_pair(self):
        receipt=self.export()
        def fail(point,stage):
            if point=='after_receiver_extract':raise OSError('synthetic interruption between pair copies')
        with self.assertRaises(OSError):self.restore(receipt,fault=fail)
        self.assertFalse(self.restored.exists());self.assertTrue(self.target.exists())
        self.assertEqual(self.restore(receipt)['head_sha256'],receipt['head_sha256'])

    def test_export_failure_or_target_race_preserves_sources(self):
        before=(self.f.receiver.path.read_bytes(),self.f.store.path.read_bytes())
        def fail(point,stage):
            if point=='after_receiver_backup':raise OSError('synthetic destination failure')
        with self.assertRaises(OSError):self.export(fault=fail)
        self.assertFalse(self.target.exists());self.assertEqual(self.f.receiver.read()['sequence'],0)
        def race(point,stage):
            if point=='before_publish':self.target.write_bytes(b'concurrent original')
        with self.assertRaises(FileExistsError):self.export(fault=race)
        self.assertEqual(self.target.read_bytes(),b'concurrent original')
        self.assertEqual((self.f.receiver.path.read_bytes(),self.f.store.path.read_bytes()),before)

    def test_denied_export_capacity_creates_no_stage_or_nonce(self):
        before=set(self.root.iterdir());nonce=self.f.store.path.read_bytes()
        with self.f.owner.admit(11*1024**2):
            (self.root/'padding').write_bytes(b'x'*(11*1024**2))
        # Source preflight is bounded; shared admission fails before any stage.
        with self.assertRaises(CapacityExceeded):self.export()
        self.assertEqual(set(self.root.iterdir()),before|{self.root/'padding'})
        self.assertEqual(self.f.store.path.read_bytes(),nonce)

    def test_archive_encoding_and_full_original_chain_are_validated(self):
        self.f.server.handle(self.f.request('reserve'),now=self.f.f.now)
        receipt=self.export()
        with zipfile.ZipFile(self.target) as archive:
            cert=json.loads(archive.read('certificate.json'));nonce=archive.read('nonces.sqlite')
            receiver=archive.read('receiver.sqlite')
        corrupt=self.root/'corrupt.sqlite';corrupt.write_bytes(receiver)
        with closing(sqlite3.connect(corrupt)) as db:
            db.execute("UPDATE protective_events SET sha256=? WHERE sequence=1",('f'*64,));db.commit()
        receiver=corrupt.read_bytes()
        cert['receiver']['database_sha256']=hashlib.sha256(receiver).hexdigest();cert['receiver']['database_bytes']=len(receiver)
        raw=json.dumps(cert,sort_keys=True,separators=(',',':')).encode()
        with zipfile.ZipFile(self.target,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
            archive.writestr('receiver.sqlite',receiver);archive.writestr('nonces.sqlite',nonce);archive.writestr('certificate.json',raw)
        with self.assertRaises(ValueError):self.restore(receipt,
            expected_archive_sha256=hashlib.sha256(self.target.read_bytes()).hexdigest(),
            expected_certificate_sha256=hashlib.sha256(raw).hexdigest())
        self.assertFalse(self.restored.exists())

    def test_lost_export_and_restore_acknowledgements_preserve_published_evidence(self):
        def lost(point,stage):
            if point=='after_publish':raise OSError('synthetic lost publication reply')
        with self.assertRaises(OSError):self.export(fault=lost)
        self.assertTrue(self.target.exists())
        with zipfile.ZipFile(self.target) as archive:
            raw=archive.read('certificate.json');cert=json.loads(raw)
        receipt=dict(archive_sha256=hashlib.sha256(self.target.read_bytes()).hexdigest(),
            certificate_sha256=hashlib.sha256(raw).hexdigest(),head_sha256=cert['receiver']['head']['head_sha256'])
        with self.assertRaises(OSError):self.restore(receipt,fault=lost)
        self.assertTrue((self.restored/'RESTORE_COMPLETE.json').exists())
        with self.assertRaises((ValueError,FileExistsError)):self.restore(receipt)

    def test_expired_export_cleans_owned_stage_without_extending_publication(self):
        clock=[100.0];shared=lambda:clock[0]
        self.f.domain.clock=shared;self.f.receiver.clock=shared;self.f.server.clock=shared
        def expire(point,stage):
            if point=='after_receiver_backup':clock[0]=102
        with self.assertRaises(TimeoutError):self.export(timeout_seconds=1,fault=expire)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.v2-pair-*')),[])
        clock[0]=100;self.assertEqual(self.f.receiver.read()['sequence'],0)

    def test_export_archive_tampering_before_publication_is_rejected(self):
        def corrupt(point,stage):
            if point=='before_publish':
                with (stage/'pair.zip').open('ab') as stream:stream.write(b'corrupt staged bytes')
        with self.assertRaises(ValueError):self.export(fault=corrupt)
        self.assertFalse(self.target.exists())

    def test_restore_copy_tampering_before_publication_is_rejected(self):
        receipt=self.export()
        def corrupt(point,stage):
            if point=='before_publish':
                with (stage/'nonces.sqlite').open('ab') as stream:stream.write(b'corrupt staged bytes')
        with self.assertRaises(ValueError):self.restore(receipt,fault=corrupt)
        self.assertFalse(self.restored.exists())

    def test_restore_target_race_never_replaces_existing_directory(self):
        receipt=self.export()
        def race(point,stage):
            if point=='before_publish':
                self.restored.mkdir();(self.restored/'original').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.restore(receipt,fault=race)
        self.assertEqual((self.restored/'original').read_bytes(),b'keep')

    def test_in_place_nonce_rollback_during_capture_invalidates_original_store(self):
        older=self.f.store.path.read_bytes()
        self.f.server.handle(self.f.request(),now=self.f.f.now)
        def rollback(point,stage):
            if point=='after_nonce_backup':self.f.store.path.write_bytes(older)
        with self.assertRaises(ValueError):self.export(fault=rollback)
        self.assertFalse(self.target.exists())
        with self.assertRaises(ValueError):self.f.server.handle(self.f.request(),now=self.f.f.now)

    def test_nonce_unused_page_corruption_cannot_pass_only_row_validation(self):
        receipt=self.export()
        with zipfile.ZipFile(self.target) as archive:
            receiver=archive.read('receiver.sqlite');nonce=bytearray(archive.read('nonces.sqlite'))
            cert=json.loads(archive.read('certificate.json'))
        nonce.extend(b'\0'*4096)
        nonce[28:32]=(len(nonce)//4096).to_bytes(4,'big')
        nonce[32:36]=(9999).to_bytes(4,'big');nonce[36:40]=(1).to_bytes(4,'big')
        cert['nonce']['database_bytes']=len(nonce);cert['nonce']['database_sha256']=hashlib.sha256(nonce).hexdigest()
        raw=json.dumps(cert,sort_keys=True,separators=(',',':')).encode()
        with zipfile.ZipFile(self.target,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
            archive.writestr('receiver.sqlite',receiver);archive.writestr('nonces.sqlite',nonce);archive.writestr('certificate.json',raw)
        with self.assertRaises(ValueError):self.restore(receipt,
            expected_archive_sha256=hashlib.sha256(self.target.read_bytes()).hexdigest(),
            expected_certificate_sha256=hashlib.sha256(raw).hexdigest())
        self.assertFalse(self.restored.exists())

    def test_previously_verified_export_copy_cannot_change_before_archive_output(self):
        def corrupt(point,stage):
            if point=='after_nonce_backup':
                with (stage/'receiver.sqlite').open('ab') as stream:stream.write(b'ignored trailing bytes')
        with self.assertRaises(ValueError):self.export(fault=corrupt)
        self.assertFalse(self.target.exists())

    def test_extracted_copy_must_still_match_certificate_before_publication(self):
        receipt=self.export()
        def corrupt(point,stage):
            if point=='after_receiver_extract':
                with (stage/'receiver.sqlite').open('ab') as stream:stream.write(b'ignored trailing bytes')
        with self.assertRaises(ValueError):self.restore(receipt,fault=corrupt)
        self.assertFalse(self.restored.exists())

    def test_nonce_header_corruption_permanently_blocks_original_instance_after_rewrite(self):
        older=self.f.store.path.read_bytes();self.f.server.handle(self.f.request(),now=self.f.f.now)
        def corrupt(point,stage):
            if point=='after_nonce_backup':self.f.store.path.write_bytes(b'broken header')
        with self.assertRaises(ValueError):self.export(fault=corrupt)
        self.f.store.path.write_bytes(older)
        with self.assertRaises(ValueError):self.f.server.handle(self.f.request(),now=self.f.f.now)
        self.assertTrue(list(self.root.glob('.v2-pair-*')))

    def test_restore_expiry_cleans_stage_and_preserves_original_artifact(self):
        receipt=self.export();clock=[100.0];self.f.domain.clock=lambda:clock[0]
        def expire(point,stage):
            if point=='after_receiver_extract':clock[0]=102
        with self.assertRaises(TimeoutError):self.restore(receipt,timeout_seconds=1,fault=expire)
        self.assertFalse(self.restored.exists());self.assertEqual(list(self.root.glob('.v2-pair-*')),[])
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(),receipt['archive_sha256'])

    def test_compressed_pair_is_rejected_before_decoding_database_members(self):
        receipt=self.export()
        with zipfile.ZipFile(self.target) as archive:members={name:archive.read(name) for name in archive.namelist()}
        with zipfile.ZipFile(self.target,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=False) as archive:
            for name,raw in members.items():archive.writestr(name,raw)
        with self.assertRaises(ValueError):self.restore(receipt,
            expected_archive_sha256=hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(self.restored.exists())


class NativeProtectivePairRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_eight_accounts_restore_with_pair_and_reconcile_without_collection(self):
        import test_archive_protective_worker as native
        from hermes_trading.archive_capacity import StorageDomain
        from hermes_trading.archive_snapshot import snapshot
        from hermes_trading.archive_protective_transport import ProtectiveServer,ProtectiveClient
        from hermes_trading.paired_worker import run_worker
        import secrets,uuid
        f=native.ProtectiveWorkerTests();f.setUp()
        for function,args,kwargs in f._cleanups:self.addCleanup(function,*args,**kwargs)
        f._cleanups.clear();f.repo.close()
        root=f.f.path.parent
        domain=StorageDomain(root,domain_id='native-pair-capture',limit_bytes=32*1024**2,reserve_bytes=1024**2,enabled=True)
        domain.initialize();owner=self.enterContext(domain.ownership())
        options=dict(owner=owner,expected_store_id=uuid.uuid4().hex,binding=f.f.binding,identity=f.f.identity,enabled=True)
        with owner.admit(4*1024**2) as lease:
            BoundedArchiveNonceStore.initialize(root/'bounded.sqlite',lease=lease,**options)
        nonce=BoundedArchiveNonceStore(root/'bounded.sqlite',**options)
        receiver=ProtectiveReceiver(f.f.path,f.f.binding,f.f.identity,enabled=True,max_bytes=1024**2,clock=domain.clock)
        self.addCleanup(receiver.close)
        with owner.admit(1024**2):receiver.prepare(timeout_seconds=10)
        f.f.head=receiver.read
        f.server=ProtectiveServer(f.f.path,f.f.binding,f.f.identity,f.server.secret,nonce,
            enabled=True,max_db_bytes=1024**2,clock=domain.clock,receiver=receiver)
        result=await f.invoke(f.outage);self.assertEqual(result['completed_batches'],2)
        clone_root=root/'worker-copy'
        with owner.admit(16*1024**2):snapshot(f.f.f.root,clone_root,enabled=True,max_bytes=16*1024**2,timeout_seconds=20)
        head=receiver.read();artifact=root/'native-pair.zip'
        receipt=export_pair(receiver,nonce,artifact,enabled=True,expected_head_sha256=head['head_sha256'],max_bytes=2*1024**2,timeout_seconds=30)
        restored=root/'pair-copy'
        restore_pair(artifact,restored,owner=owner,enabled=True,
            expected_archive_sha256=receipt['archive_sha256'],expected_certificate_sha256=receipt['certificate_sha256'],
            binding=f.f.binding,identity=f.f.identity,expected_nonce_store_id=options['expected_store_id'],
            expected_head_sha256=head['head_sha256'],max_bytes=2*1024**2,timeout_seconds=120)
        copy_receiver=ProtectiveReceiver(restored/'receiver.sqlite',f.f.binding,f.f.identity,enabled=True,max_bytes=1024**2,clock=domain.clock)
        self.addCleanup(copy_receiver.close)
        with owner.admit(1024**2):self.assertEqual(copy_receiver.prepare(timeout_seconds=10),head)
        copy_nonce=BoundedArchiveNonceStore(restored/'nonces.sqlite',**options)
        key=secrets.token_hex(32)
        server=ProtectiveServer(restored/'receiver.sqlite',f.f.binding,f.f.identity,key,copy_nonce,
            enabled=True,max_db_bytes=1024**2,clock=domain.clock,receiver=copy_receiver)
        def exchange(envelope,*,timeout_seconds,**kwargs):return server.handle(envelope,now=f.f.f.at,timeout_seconds=timeout_seconds)
        client=ProtectiveClient(f.f.binding,f.f.identity,key,exchange,enabled=True,now=f.f.f.clock)
        done=await run_worker(clone_root,collector_factory=lambda:f.f.f.market,clock=f.f.f.clock,sleep=f.f.f.sleep,witness=client)
        self.assertEqual(done['completed_batches'],2);self.assertEqual(done['stop_reason'],'cycle_bound')
        self.assertEqual(copy_receiver.read()['state']['point']['complete'],2)
        self.assertEqual(f.f.f.market.collect.await_count,2)
        for profile in f.f.f.manifest['profiles']:
            source=f.f.f.root/profile['directory']/'trading.db';copied=clone_root/profile['directory']/'trading.db'
            with closing(sqlite3.connect(source)) as original,closing(sqlite3.connect(copied)) as restored_db:
                tables=original.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                for (table,) in tables:
                    self.assertEqual(original.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),
                        restored_db.execute('SELECT * FROM "'+table+'" ORDER BY rowid').fetchall(),(profile['directory'],table))


if __name__=='__main__':unittest.main()
