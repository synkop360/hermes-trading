"""Fresh-target restoration; no active account is replaced."""
from contextlib import closing
import hashlib,sqlite3,unittest

import test_archive_account_history as fixtures
from hermes_trading.archive_account_restore import restore_account


class AccountRestoreTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AccountHistoryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.target=self.f.root/'restored.db'
        with closing(sqlite3.connect(self.f.active)) as db:
            db.execute('CREATE TABLE original_control(id TEXT PRIMARY KEY, raw BLOB)')
            db.execute("INSERT INTO original_control(rowid,id,raw) VALUES(9,'current',?)",(b'\x00original',))
            db.execute('PRAGMA user_version=17');db.commit()
        self.f.change('DELETE FROM decisions')

    def invoke(self,**kwargs):
        options=dict(enabled=True,cache_root=self.f.root,max_bytes=8*1024*1024,timeout_seconds=10)
        options.update(kwargs);return restore_account(self.f.refs(),self.target,**options)

    def test_original_financial_union_and_all_active_control_rows_are_preserved(self):
        before={p:p.read_bytes() for p in (self.f.first,self.f.active)}
        result=self.invoke()
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute('SELECT rowid,* FROM original_control').fetchall(),[(9,'current',b'\x00original')])
            self.assertEqual(db.execute('SELECT reason FROM decisions').fetchone()[0],'original')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],17)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(result['database_sha256'],hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertFalse(result['activated']);self.assertFalse(result['retirement_authorized'])
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertFalse(list(self.f.root.glob('.account-*')))

    def test_disabled_or_existing_target_never_overwrites(self):
        with self.assertRaises(ValueError):self.invoke(enabled=False)
        self.target.write_bytes(b'keep original')
        with self.assertRaises((ValueError,FileExistsError)):self.invoke()
        self.assertEqual(self.target.read_bytes(),b'keep original')

    def test_disk_failure_or_deadline_retains_sources_and_removes_staging(self):
        before=self.f.active.read_bytes()
        for failure in ('disk','deadline'):
            ticks=[0]
            def fault(point,stage):
                if point=='after_backup':
                    if failure=='disk':raise OSError('invented full disk')
                    ticks[0]=20
            with self.assertRaises((OSError,TimeoutError)):
                self.invoke(clock=lambda:ticks[0],fault=fault)
            self.assertFalse(self.target.exists());self.assertEqual(self.f.active.read_bytes(),before)
            self.assertFalse(list(self.f.root.glob('.account-*')))

    def test_concurrent_target_is_not_replaced(self):
        def fault(point,stage):
            if point=='before_publish':self.target.write_bytes(b'concurrent original')
        with self.assertRaises(FileExistsError):self.invoke(fault=fault)
        self.assertEqual(self.target.read_bytes(),b'concurrent original')

    def test_changed_staged_control_state_is_rejected(self):
        def fault(point,stage):
            if point=='before_publish':
                with closing(sqlite3.connect(stage)) as db:
                    db.execute("UPDATE original_control SET raw=X'01'");db.commit()
        with self.assertRaisesRegex(ValueError,'control|original'):
            self.invoke(fault=fault)
        self.assertFalse(self.target.exists())

    def test_lost_publication_reply_retains_valid_target_without_overwrite_on_retry(self):
        before=self.f.active.read_bytes()
        def fault(point,stage):
            if point=='after_publish':raise OSError('invented lost publication reply')
        with self.assertRaisesRegex(OSError,'lost publication reply'):self.invoke(fault=fault)
        digest=hashlib.sha256(self.target.read_bytes()).hexdigest()
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute('SELECT reason FROM decisions').fetchone()[0],'original')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        with self.assertRaises(ValueError):self.invoke()
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(),digest)
        self.assertEqual(self.f.active.read_bytes(),before)


class NativeAccountRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_venue_minimum_preserves_pending_remainder_after_whole_cohort_restore(self):
        case=fixtures.NativeAccountHistoryTests()
        try:
            await case.compare_history(partial=True,restore=True,block_venue=True)
        finally:
            for function,args,kwargs in case._cleanups:self.addCleanup(function,*args,**kwargs)
            case._cleanups.clear()

    async def test_restored_partial_cohort_resumes_and_matches_intact_original_exits(self):
        case=fixtures.NativeAccountHistoryTests()
        try:
            await case.compare_history(partial=True,restore=True)
        finally:
            # The reused case has no async runner. Transfer its synchronous
            # native fixture cleanups to this live runner rather than hide errors.
            for function,args,kwargs in case._cleanups:self.addCleanup(function,*args,**kwargs)
            case._cleanups.clear()


if __name__=='__main__':unittest.main()
