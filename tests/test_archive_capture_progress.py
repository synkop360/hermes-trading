"""Read-only capture work retains ownership without repeated domain traversal."""
import threading
import unittest
from unittest.mock import patch

import test_archive_nonce_transport as fixtures
from hermes_trading.archive_capacity import CapacityExceeded
from hermes_trading.archive_capture_io import CaptureBudget


class CaptureProgressTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AdmittedProtectiveTransportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.server.handle(self.f.request(),now=self.f.f.now)
        self.domain=self.f.domain;self.owner=self.f.owner;self.root=self.f.f.path.parent

    def test_readonly_full_source_verification_does_not_repeat_domain_inventory(self):
        before={p:p.read_bytes() for p in (self.f.receiver.path,self.f.store.path)}
        with self.owner.admit(8*1024**2) as lease:
            deadline=self.domain.clock()+10
            with self.f.receiver.capture(lease,deadline=deadline) as receiver,self.f.store.capture(lease,deadline=deadline) as nonce:
                with patch.object(self.domain,'_measure',wraps=self.domain._measure) as scan:
                    for _ in range(10):
                        self.assertEqual(receiver.verify_source(),receiver.evidence)
                        self.assertEqual(nonce.verify_source(),nonce.evidence)
                    self.assertEqual(scan.call_count,0,'read-only capture verification traverses the entire domain')
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_progress_keeps_owner_policy_deadline_and_original_thread(self):
        progress=getattr(CaptureBudget,'progress',None)
        self.assertTrue(callable(progress),'capture progress ownership guard is missing')
        original=self.domain._raw;clock=[100.0];self.domain.clock=lambda:clock[0]
        with self.owner.admit(8*1024**2) as lease:
            budget=CaptureBudget(lease,self.owner,105)
            with patch.object(self.domain,'_measure',side_effect=AssertionError('read-only progress rescanned')):
                budget.progress()
                # Windows retains a byte-range lock on this owner handle;
                # mutate its policy through that same original handle.
                self.owner._handle.seek(1);self.owner._handle.write(b'?')
                try:
                    with self.assertRaises(ValueError):budget.progress()
                finally:
                    self.owner._handle.seek(1);self.owner._handle.write(original[1:])
                clock[0]=106
                with self.assertRaises(TimeoutError):budget.progress()
                clock[0]=99
                with self.assertRaises(ValueError):budget.progress()
                clock[0]=100;errors=[]
                def foreign():
                    try:budget.progress()
                    except ValueError as error:errors.append(error)
                thread=threading.Thread(target=foreign);thread.start();thread.join(timeout=3)
                self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
        with self.assertRaises(ValueError):budget.progress()

    def test_capacity_is_rechecked_before_copy_creation_and_after_copy_sync(self):
        import hermes_trading.archive_capture_io as capture_io
        target=self.root/'bounded-copy.sqlite';filler=self.root/'unexpected-growth.bin'
        with self.owner.admit(8*1024**2) as lease,self.f.store.capture(lease,deadline=self.domain.clock()+10) as capture:
            with filler.open('xb') as stream:stream.truncate(8*1024**2+1)
            try:
                with self.assertRaises(CapacityExceeded):capture.backup_to(target)
                self.assertFalse(target.exists())
            finally:filler.unlink()
            sync=capture_io.sync_file
            def grow(path):
                sync(path)
                with filler.open('xb') as stream:stream.truncate(8*1024**2+1)
            try:
                with patch.object(capture_io,'sync_file',side_effect=grow):
                    with self.assertRaises(CapacityExceeded):capture.backup_to(target)
                self.assertTrue(target.exists())
            finally:filler.unlink(missing_ok=True)


if __name__=='__main__':unittest.main()
