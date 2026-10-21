"""Dedicated admitted replay storage; no live source or receiver mutation."""
from contextlib import contextmanager,closing
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from hermes_trading.archive_capacity import StorageDomain,CapacityExceeded,CapacityUnavailable
from hermes_trading.archive_nonce import BoundedArchiveNonceStore,NONCE_PEAK_BYTES,NonceCapacityError,NonceIntegrityError


class ArchiveNonceTests(unittest.TestCase):
    measured_peaks={}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'state';self.root.mkdir()
        self.domain=StorageDomain(self.root,domain_id='synthetic-nonce',limit_bytes=16*1024*1024,
            reserve_bytes=1024*1024,enabled=True);self.domain.initialize()
        self.owner=self.enterContext(self.domain.ownership())
        self.at=datetime(2026,10,18,12,tzinfo=timezone.utc)
        self.binding={'schema_version':2,'purpose':'paired_archive_protective_witness_v2','witness_id':'a'*32,'genesis_sha256':'b'*64}
        self.identity={'experiment_id':'synthetic-nonce','cohort_sha256':'c'*64,'release_sha256':'d'*64,
            'policy_sha256':'e'*64,'limits_sha256':'f'*64,'max_cycles':4,'deadline':(self.at+timedelta(days=1)).isoformat()}
        self.path=self.root/'nonces.sqlite';self.store_id='1'*32

    def options(self,**changes):
        return dict(owner=self.owner,expected_store_id=self.store_id,binding=self.binding,identity=self.identity,enabled=True,**changes)

    def initialize(self,**changes):
        options=self.options(**changes)
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:
            receipt=BoundedArchiveNonceStore.initialize(self.path,lease=lease,**options)
        return BoundedArchiveNonceStore(self.path,**options),receipt

    def add(self,store,nonce='2'*32,at=None,**kwargs):
        at=at or self.at
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:
            with store.armed(lease,deadline=self.domain.clock()+5):
                return store.add_once(nonce,accepted_at=at,expires_at=at+timedelta(seconds=300),**kwargs)

    def rows(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            return db.execute('SELECT nonce,accepted_us,expires_us FROM nonce_entries').fetchall()

    def test_explicit_generation_receipt_and_replay_survive_reopen(self):
        store,receipt=self.initialize();self.assertEqual(receipt['store_id'],self.store_id)
        self.assertTrue(self.add(store));before=self.path.read_bytes()
        self.assertFalse(self.add(store));self.assertEqual(self.path.read_bytes(),before)
        reopened=BoundedArchiveNonceStore(self.path,**self.options())
        self.assertFalse(self.add(reopened));self.assertEqual(len(self.rows()),1)
        self.assertFalse((self.path.parent/(self.path.name+'-wal')).exists())
        self.assertFalse((self.path.parent/(self.path.name+'-shm')).exists())

    def test_missing_or_existing_storage_is_never_implicitly_created_or_reset(self):
        with self.assertRaises((ValueError,OSError)):BoundedArchiveNonceStore(self.path,**self.options())
        self.assertFalse(self.path.exists())
        store,_=self.initialize();self.add(store);before=self.path.read_bytes()
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:
            with self.assertRaises((ValueError,FileExistsError)):
                BoundedArchiveNonceStore.initialize(self.path,lease=lease,**self.options())
        self.assertEqual(self.path.read_bytes(),before)

    def test_wrong_external_generation_binding_and_limits_fail_without_mutation(self):
        self.initialize();before=self.path.read_bytes()
        for changes in [{'expected_store_id':'3'*32},{'max_rows':2},{'max_pages':255},
                {'binding':dict(self.binding,witness_id='4'*32)},
                {'identity':dict(self.identity,release_sha256='4'*64)}]:
            options=self.options();options.update(changes)
            with self.subTest(changes=changes):
                store=BoundedArchiveNonceStore(self.path,**options)
                with self.assertRaises(NonceIntegrityError):self.add(store)
                self.assertEqual(self.path.read_bytes(),before)

    def test_nonce_rows_expire_exactly_and_clock_regression_never_prunes(self):
        store,_=self.initialize(max_rows=1);self.assertTrue(self.add(store))
        with self.assertRaises(NonceCapacityError):self.add(store,'3'*32,self.at+timedelta(seconds=299))
        self.assertEqual(len(self.rows()),1)
        self.assertTrue(self.add(store,'3'*32,self.at+timedelta(seconds=300)))
        self.assertEqual([row[0] for row in self.rows()],['3'*32])
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):self.add(store,'4'*32,self.at+timedelta(seconds=299))
        self.assertEqual(self.path.read_bytes(),before)

    def test_arming_is_required_single_use_and_original_thread_bound(self):
        store,_=self.initialize()
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(ValueError):store.add_once('2'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
            connection.assert_not_called()
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:
            with store.armed(lease,deadline=self.domain.clock()+5):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future=executor.submit(store.add_once,'2'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
                    with self.assertRaises(ValueError):future.result()
                self.assertTrue(store.add_once('2'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300)))
                with self.assertRaises(ValueError):store.add_once('3'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))

    def test_insufficient_shared_admission_never_opens_the_nonce_database(self):
        store,_=self.initialize()
        with self.owner.admit(1) as lease,patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(CapacityExceeded):
                with store.armed(lease,deadline=self.domain.clock()+5):pass
            connection.assert_not_called()

    def test_invalid_nonce_and_expiry_are_rejected_before_sql(self):
        store,_=self.initialize();before=self.path.read_bytes()
        for nonce in ['bad','A'*32,True]:
            with self.subTest(nonce=nonce),self.assertRaises(ValueError):self.add(store,nonce)
        for expiry in [self.at+timedelta(seconds=299),self.at.replace(tzinfo=None)]:
            with self.owner.admit(NONCE_PEAK_BYTES) as lease,store.armed(lease,deadline=self.domain.clock()+5):
                with self.assertRaises(ValueError):store.add_once('2'*32,accepted_at=self.at,expires_at=expiry)
        self.assertEqual(self.path.read_bytes(),before)

    def test_invalid_first_call_consumes_original_thread_arming(self):
        store,_=self.initialize()
        with self.owner.admit(NONCE_PEAK_BYTES) as lease,store.armed(lease,deadline=self.domain.clock()+5):
            with self.assertRaises(ValueError):store.add_once('bad',accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
            with self.assertRaises(ValueError):store.add_once('2'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
        self.assertEqual(self.rows(),[])

    def test_precommit_fault_rolls_back_expiry_insert_and_floor(self):
        store,_=self.initialize(max_rows=1);self.add(store)
        before=self.path.read_bytes()
        def fail(point):
            if point=='before_commit':raise OSError('synthetic precommit failure')
        store.fault=fail
        with self.assertRaises(OSError):self.add(store,'3'*32,self.at+timedelta(seconds=300))
        self.assertEqual(self.path.read_bytes(),before)
        store.fault=None
        self.assertTrue(self.add(store,'3'*32,self.at+timedelta(seconds=300)))

    def test_postcommit_lost_reply_preserves_nonce(self):
        store,_=self.initialize()
        def fail(point):
            if point=='after_commit':raise OSError('synthetic lost reply')
        store.fault=fail
        with self.assertRaises(OSError):self.add(store)
        store.fault=None
        self.assertFalse(self.add(store));self.assertEqual(len(self.rows()),1)

    def test_foreign_schema_is_not_migrated(self):
        self.initialize()
        with self.owner.admit(NONCE_PEAK_BYTES):
            with closing(sqlite3.connect(self.path)) as db:
                db.execute('CREATE TABLE unrelated(value TEXT)');db.commit()
        store=BoundedArchiveNonceStore(self.path,**self.options());before=self.path.read_bytes()
        with self.assertRaises(NonceIntegrityError):self.add(store)
        self.assertEqual(self.path.read_bytes(),before)

    def test_replaced_generation_is_not_learned_from_the_replacement_file(self):
        store,_=self.initialize();self.add(store);self.path.rename(self.root/'original.sqlite')
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:
            options=self.options();options['expected_store_id']='9'*32
            BoundedArchiveNonceStore.initialize(self.path,lease=lease,**options)
        with self.assertRaises(NonceIntegrityError):self.add(store)
        restored=BoundedArchiveNonceStore(self.path,**self.options())
        with self.assertRaises(NonceIntegrityError):self.add(restored)

    def test_initializer_rejects_replacement_of_its_newly_created_file(self):
        connect=sqlite3.connect;replacement=[]
        def replace_before_connect(*args,**kwargs):
            self.path.rename(self.root/'initialization-original.sqlite')
            with closing(connect(self.path)) as foreign:
                foreign.execute('CREATE TABLE unrelated(value TEXT)');foreign.commit()
            replacement.append(self.path.read_bytes())
            return connect(*args,**kwargs)
        with self.owner.admit(NONCE_PEAK_BYTES) as lease:
            with patch('hermes_trading.archive_nonce.sqlite3.connect',side_effect=replace_before_connect):
                with self.assertRaises(NonceIntegrityError):
                    BoundedArchiveNonceStore.initialize(self.path,lease=lease,**self.options())
        self.assertEqual(self.path.read_bytes(),replacement[0])

    def test_actual_wal_header_and_sidecars_are_rejected_without_conversion(self):
        self.initialize()
        with self.owner.admit(NONCE_PEAK_BYTES):
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        before=self.path.read_bytes()
        with self.assertRaises(NonceIntegrityError):BoundedArchiveNonceStore(self.path,**self.options())
        self.assertEqual(self.path.read_bytes(),before)
        # A rollback-format file with an unexpected empty sidecar also fails.
        # This is a separate owned file, never a migration of the WAL fixture.
        original_path=self.path;self.path=self.root/'second.sqlite'
        store,_=self.initialize();wal=Path(str(self.path)+'-wal')
        with self.owner.admit(NONCE_PEAK_BYTES):wal.write_bytes(b'')
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(NonceIntegrityError):self.add(store)
            connection.assert_not_called()
        self.path=original_path

    def test_oversized_or_hardlinked_rollback_sidecar_is_rejected_before_sql(self):
        store,_=self.initialize();journal=Path(str(self.path)+'-journal')
        with self.owner.admit(NONCE_PEAK_BYTES):
            journal.write_bytes(b'\0'*(256*(4096+8)+65536+1))
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(NonceCapacityError):self.add(store)
            connection.assert_not_called()
        # Both paths are owned by this synthetic fixture. No external cleanup.
        with self.assertRaisesRegex(ValueError,'hard-linked'):
            with self.owner.admit(NONCE_PEAK_BYTES):
                journal.rename(self.root/'oversized-journal-evidence')
                source=self.root/'linked-journal-source';source.write_bytes(b'journal')
                journal.hardlink_to(source)
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaisesRegex(ValueError,'hard-linked'):self.add(store)
            with self.assertRaises(NonceIntegrityError):BoundedArchiveNonceStore(self.path,**self.options())
            connection.assert_not_called()

    def test_hot_journal_recovery_requires_admission_and_preserves_committed_nonce(self):
        store,_=self.initialize();self.add(store)
        crash_code='''
import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('PRAGMA synchronous=FULL')
db.execute('PRAGMA cache_size=1')
db.execute('PRAGMA cache_spill=ON')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE nonce_meta SET payload=? WHERE id=1',('uncommitted '*10000,))
os._exit(0)
'''
        with self.owner.admit(NONCE_PEAK_BYTES):
            child=subprocess.run([sys.executable,'-B','-c',crash_code,str(self.path)],
                capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        journal=Path(str(self.path)+'-journal');self.assertTrue(journal.exists())
        self.assertEqual(journal.read_bytes()[:8],bytes.fromhex('d9d505f920a163d7'))
        before={p.name:p.read_bytes() for p in (self.path,journal)}
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(ValueError):store.add_once('3'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
            connection.assert_not_called()
        self.assertEqual({p.name:p.read_bytes() for p in (self.path,journal)},before)
        connect=sqlite3.connect
        def admitted_connect(*args,**kwargs):
            self.assertIsNotNone(store._arm)
            self.assertIs(store._arm['lease'].owner,self.owner)
            store._arm['lease'].check()
            return connect(*args,**kwargs)
        with patch('hermes_trading.archive_nonce.sqlite3.connect',side_effect=admitted_connect):
            self.assertFalse(self.add(store))
        self.assertFalse(journal.exists());self.assertEqual([r[0] for r in self.rows()],['2'*32])
        self.assertTrue(self.add(store,'3'*32))

    def test_page_exhaustion_preserves_all_live_rows_and_recovers_after_expiry(self):
        store,_=self.initialize(max_pages=3)
        accepted=[]
        for index in range(256):
            nonce=f'{index:032x}';before=self.path.read_bytes()
            try:self.add(store,nonce)
            except NonceCapacityError:
                self.assertEqual(self.path.read_bytes(),before)
                break
            accepted.append(nonce)
        else:self.fail('three-page fixture never exercised SQLite allocation exhaustion')
        self.assertGreater(len(accepted),1)
        self.assertLess(len(accepted),256)
        self.assertEqual({row[0] for row in self.rows()},set(accepted))
        self.assertTrue(self.add(store,'f'*32,self.at+timedelta(seconds=300)))
        self.assertEqual([row[0] for row in self.rows()],['f'*32])

    def test_released_nested_foreign_owner_and_process_arming_never_connect(self):
        store,_=self.initialize()
        with self.owner.admit(NONCE_PEAK_BYTES) as expired:pass
        other_root=Path(self.tmp.name)/'other';other_root.mkdir()
        other=StorageDomain(other_root,domain_id='other',limit_bytes=16*1024*1024,
            reserve_bytes=1024*1024,enabled=True);other.initialize()
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(ValueError):
                with store.armed(expired,deadline=self.domain.clock()+5):pass
            with other.ownership() as owner,owner.admit(NONCE_PEAK_BYTES) as lease:
                with self.assertRaises(ValueError):
                    with store.armed(lease,deadline=self.domain.clock()+5):pass
            with self.owner.admit(NONCE_PEAK_BYTES) as lease:
                with store.armed(lease,deadline=self.domain.clock()+5):
                    with self.assertRaises(ValueError):
                        with store.armed(lease,deadline=self.domain.clock()+5):pass
                    import os
                    with patch('hermes_trading.archive_nonce.os.getpid',return_value=os.getpid()+100000):
                        with self.assertRaises(ValueError):store.add_once('2'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
            connection.assert_not_called()
        self.assertTrue(self.add(store))

    def test_invalid_request_deadlines_and_original_lease_deadline_are_enforced(self):
        store,_=self.initialize();clock=[100.0]
        with patch.object(self.domain,'clock',side_effect=lambda:clock[0]):
            with self.owner.admit(NONCE_PEAK_BYTES) as lease:
                for deadline in (100,111,float('nan'),float('inf'),True):
                    with self.subTest(deadline=deadline),self.assertRaises(ValueError):
                        with store.armed(lease,deadline=deadline):pass
            before=self.path.read_bytes()
            with self.owner.admit(NONCE_PEAK_BYTES,timeout_seconds=1) as lease:
                try:
                    with store.armed(lease,deadline=105):
                        clock[0]=101
                        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
                            with self.assertRaises(CapacityUnavailable):store.add_once('2'*32,accepted_at=self.at,expires_at=self.at+timedelta(seconds=300))
                            connection.assert_not_called()
                except CapacityUnavailable:pass
                finally:clock[0]=100
            self.assertEqual(self.path.read_bytes(),before)

    def test_precommit_deadline_rolls_back_and_postcommit_deadline_keeps_floor(self):
        store,_=self.initialize(max_rows=1);self.add(store);clock=[100.0]
        with patch.object(self.domain,'clock',side_effect=lambda:clock[0]):
            for point,committed in [('before_commit',False),('after_commit',True)]:
                before=self.path.read_bytes()
                def expire(observed):
                    if observed==point:clock[0]=106
                store.fault=expire
                with self.owner.admit(NONCE_PEAK_BYTES) as lease:
                    try:
                        with self.assertRaises(TimeoutError):
                            with store.armed(lease,deadline=105):
                                store.add_once('3'*32,accepted_at=self.at+timedelta(seconds=300),expires_at=self.at+timedelta(seconds=600))
                    finally:clock[0]=100
                if not committed:self.assertEqual(self.path.read_bytes(),before)
                else:self.assertEqual([row[0] for row in self.rows()],['3'*32])
            store.fault=None;before=self.path.read_bytes()
            with self.assertRaises(ValueError):self.add(store,'4'*32,self.at+timedelta(seconds=299))
            self.assertEqual(self.path.read_bytes(),before)

    def test_maximum_live_rows_churn_and_fault_extents_stay_inside_proposed_peak(self):
        store,_=self.initialize();peaks=[]
        def observe(point):
            sizes={p.name:p.stat().st_size for p in self.root.iterdir() if p.is_file() and p.name.startswith(self.path.name)}
            peaks.append((point,sizes))
        store.fault=observe
        for index in range(256):self.assertTrue(self.add(store,f'{index:032x}'))
        before=self.path.read_bytes()
        with self.assertRaises(NonceCapacityError):self.add(store,'e'*32)
        self.assertEqual(self.path.read_bytes(),before)
        def fail(point):
            observe(point)
            if point=='before_commit':raise OSError('expiry rollback at maximum occupancy')
        store.fault=fail
        with self.assertRaises(OSError):self.add(store,'f'*32,self.at+timedelta(seconds=300))
        self.assertEqual(self.path.read_bytes(),before)
        store.fault=observe
        for index in range(256):self.assertTrue(self.add(store,f'{index+256:032x}',self.at+timedelta(seconds=300)))
        self.assertEqual(len(self.rows()),256)
        self.assertTrue(any(any(name.endswith('-journal') for name in sizes) for _,sizes in peaks))
        self.assertLess(max(sum(sizes.values()) for _,sizes in peaks),NONCE_PEAK_BYTES)
        self.assertTrue(all(not any(name.endswith(('-wal','-shm')) for name in sizes) for _,sizes in peaks))
        type(self).measured_peaks['maximum_row_churn']={
            'accepted_operations':512,'live_rows':256,'checkpoints':len(peaks),
            'max_named_extent_bytes':max(sum(sizes.values()) for _,sizes in peaks),
            'max_main_bytes':max(sizes.get(self.path.name,0) for _,sizes in peaks),
            'max_journal_bytes':max(sizes.get(self.path.name+'-journal',0) for _,sizes in peaks),
            'proposed_additional_peak_bytes':NONCE_PEAK_BYTES,
            'observation_scope':'named main/rollback files at before/after-commit fault checkpoints; not exhaustive VFS tracing',
            'temporary_file_confinement_qualified':False}


if __name__=='__main__':unittest.main()
