"""Non-mutating owner-held captures on actual disposable V2 databases."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_archive_nonce_transport as fixtures
from hermes_trading.archive_capacity import CapacityExceeded
from hermes_trading.archive_nonce import NONCE_PEAK_BYTES


class ProtectiveCaptureTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AdmittedProtectiveTransportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.receiver=self.f.receiver;self.store=self.f.store;self.owner=self.f.owner;self.domain=self.f.domain
        self.target=self.f.f.path.parent/'capture.sqlite'
        self.f.server.handle(self.f.request(),now=self.f.f.now)

    def deadline(self):return self.domain.clock()+10

    def test_nonce_snapshot_preserves_bytes_and_replays_including_same_timestamp(self):
        self.f.server.handle(self.f.request(),now=self.f.f.now)
        before=self.store.path.read_bytes()
        with self.owner.admit(8*1024**2) as lease:
            with self.store.capture(lease,deadline=self.deadline()) as capture:
                evidence=capture.evidence
                self.assertEqual(evidence['row_count'],2)
                evidence['metadata']['store_id']='f'*32
                self.assertNotEqual(capture.evidence,evidence)
                result=capture.backup_to(self.target)
                self.assertEqual(result['rows_sha256'],capture.evidence['rows_sha256'])
                self.assertEqual(capture.verify_source(),capture.evidence)
        self.assertEqual(self.store.path.read_bytes(),before)
        with closing(sqlite3.connect(self.target.as_uri()+'?mode=ro',uri=True)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nonce_entries').fetchone()[0],2)
        with self.assertRaises(ValueError):capture.verify_source()

    def test_nonce_capture_excludes_acceptance_and_nested_capture(self):
        with self.owner.admit(8*1024**2) as lease,self.store.capture(lease,deadline=self.deadline()):
            with self.assertRaises(ValueError):
                with self.store.armed(lease,deadline=self.deadline()):pass
            with self.assertRaises(ValueError):
                with self.store.capture(lease,deadline=self.deadline()):pass

    def test_nonce_capture_needs_live_sufficient_admission_before_connect(self):
        with self.owner.admit(NONCE_PEAK_BYTES) as expired:pass
        with patch('hermes_trading.archive_nonce.sqlite3.connect') as connection:
            with self.assertRaises(ValueError):
                with self.store.capture(expired,deadline=self.deadline()):pass
            with self.owner.admit(1) as lease:
                with self.assertRaises(CapacityExceeded):
                    with self.store.capture(lease,deadline=self.deadline()):pass
            connection.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_nonce_backup_rejects_existing_target_without_overwrite(self):
        self.target.write_bytes(b'keep original')
        with self.owner.admit(8*1024**2) as lease,self.store.capture(lease,deadline=self.deadline()) as capture:
            with self.assertRaises(FileExistsError):capture.backup_to(self.target)
        self.assertEqual(self.target.read_bytes(),b'keep original')

    def test_nonce_backup_copies_floor_and_exact_expired_rows_without_pruning(self):
        self.f.f.now+=timedelta(seconds=600)
        before=self.store.path.read_bytes()
        with self.owner.admit(8*1024**2) as lease,self.store.capture(lease,deadline=self.deadline()) as capture:
            original=capture.evidence
            capture.backup_to(self.target)
        self.assertEqual(self.store.path.read_bytes(),before)
        with closing(sqlite3.connect(self.target.as_uri()+'?mode=ro',uri=True)) as db:
            meta=json.loads(db.execute('SELECT payload FROM nonce_meta').fetchone()[0])
            self.assertEqual(meta,original['metadata'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM nonce_entries').fetchone()[0],original['row_count'])

    def test_receiver_capture_preserves_pending_credit_and_original_event_rows(self):
        self.f.server.handle(self.f.request('reserve'),now=self.f.f.now)
        original=self.receiver.read();before=self.receiver.path.read_bytes()
        with self.owner.admit(8*1024**2) as lease,self.receiver.capture(lease,deadline=self.deadline()) as capture:
            self.assertEqual(capture.evidence['head'],original)
            result=capture.backup_to(self.target)
            self.assertEqual(result['head'],original)
            self.assertEqual(result['event_count'],original['sequence']+1)
            with self.assertRaises(ValueError):self.receiver.read()
            self.assertEqual(capture.verify_source(),capture.evidence)
        self.assertEqual(self.receiver.path.read_bytes(),before)
        self.assertEqual(self.receiver.read(),original)

    def test_pair_captures_hold_same_cut_without_nonce_or_receiver_mutation(self):
        before=(self.store.path.read_bytes(),self.receiver.path.read_bytes())
        with self.owner.admit(8*1024**2) as lease:
            deadline=self.deadline()
            with self.receiver.capture(lease,deadline=deadline) as receiver,self.store.capture(lease,deadline=deadline) as nonce:
                receiver.backup_to(self.target)
                nonce.backup_to(self.target.with_name('nonce-copy.sqlite'))
                self.assertEqual(nonce.verify_source(),nonce.evidence)
                self.assertEqual(receiver.verify_source(),receiver.evidence)
        self.assertEqual((self.store.path.read_bytes(),self.receiver.path.read_bytes()),before)

    def test_capture_deadline_can_never_be_extended_by_backup_work(self):
        clock=[100.0];shared=lambda:clock[0]
        self.domain.clock=shared;self.receiver.clock=shared
        with self.owner.admit(8*1024**2) as lease:
            try:
                with self.receiver.capture(lease,deadline=105) as capture:
                    clock[0]=106
                    with self.assertRaises(TimeoutError):capture.backup_to(self.target)
            except TimeoutError:pass
            finally:clock[0]=100
        self.assertFalse(self.target.exists())
        self.assertIsNotNone(self.receiver.read())

    def test_capture_refuses_unregistered_destination_before_creation(self):
        other=Path(self.f.f.f.root)/'unregistered-copy.sqlite'
        with self.owner.admit(8*1024**2) as lease,self.store.capture(lease,deadline=self.deadline()) as capture:
            with self.assertRaises(ValueError):capture.backup_to(other)
        self.assertFalse(other.exists())

    def test_backup_target_replacement_is_not_initialized_or_overwritten(self):
        original_connect=sqlite3.connect;replacement=[]
        def replace(*args,**kwargs):
            self.target.rename(self.target.with_name('original-created-copy'))
            with closing(original_connect(self.target)) as db:
                db.execute('CREATE TABLE unrelated(value TEXT)');db.commit()
            replacement.append(self.target.read_bytes())
            return original_connect(*args,**kwargs)
        with self.owner.admit(8*1024**2) as lease,self.store.capture(lease,deadline=self.deadline()) as capture:
            with patch('hermes_trading.archive_capture_io.sqlite3.connect',side_effect=replace):
                with self.assertRaises(ValueError):capture.backup_to(self.target)
        self.assertEqual(self.target.read_bytes(),replacement[0])


if __name__=='__main__':unittest.main()
