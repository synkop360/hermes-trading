import json,os,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch

from hermes_trading.reflection_supervision import _hash
from hermes_trading.paper_publication import _release_hashes
import release_transition_fixtures as fixtures


class StorageRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        f=await fixtures.NativeTransitionFixture.create(self)
        for profile in f.manifest['profiles']:
            with closing(sqlite3.connect(f.worker_root/profile['directory']/'trading.db')) as db:
                db.execute("UPDATE positions SET status='closed'");db.commit()
        with closing(sqlite3.connect(f.worker_root/'worker_runtime.sqlite')) as db:
            original=json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
            original['storage_stop']=True
            db.execute('UPDATE lifecycle SET state=? WHERE id=1',(json.dumps(original),));db.commit()
        halt={'schema_version':1,'experiment_id':'paired-test','policy_hash':_hash(f.policy),'reason':'storage_stop'}
        (f.primary/'experiment_halt.json').write_text(json.dumps(halt))
        return f,original

    def recover(self,f,**kwargs):
        from scripts.paper_storage_recovery import recover_storage
        with patch.dict(os.environ,fixtures.PAPER_ENV):
            return recover_storage(f.worker_root,approval_ref='synthetic-authorized-compaction',
                expected_runtime=_hash(_release_hashes()),now=f.at,**kwargs)

    async def test_recovery_preserves_every_nonstorage_lifecycle_field_and_original_halt(self):
        f,before=await self.fixture()
        result=self.recover(f)
        self.assertEqual(result['status'],'recovered')
        self.assertFalse((f.primary/'experiment_halt.json').exists())
        self.assertTrue((f.worker_root/'.paper-storage-recovery/retired-halt.json').is_file())
        with closing(sqlite3.connect(f.worker_root/'worker_runtime.sqlite')) as db:
            after=json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        self.assertFalse(after['storage_stop'])
        for key in before:
            if key not in {'storage_stop','storage_report','storage_recovery'}:self.assertEqual(after[key],before[key])
        self.assertEqual(self.recover(f)['status'],'already_recovered')

    async def test_failed_admission_retains_stop_and_marker(self):
        f,before=await self.fixture()
        with patch('scripts.paper_storage_recovery.inspect_storage',return_value={'status':'blocked'}):
            with self.assertRaises(ValueError):self.recover(f)
        self.assertTrue((f.primary/'experiment_halt.json').exists())
        self.assertFalse((f.worker_root/'.paper-storage-recovery/intent.json').exists())

    async def test_open_positions_or_elapsed_deadline_never_recover(self):
        f,before=await self.fixture()
        with patch('scripts.paper_storage_recovery._active_storage_positions',return_value={'adaptive_200':1}):
            with self.assertRaises(ValueError):self.recover(f)
        from datetime import timedelta
        from scripts.paper_storage_recovery import recover_storage
        with patch.dict(os.environ,fixtures.PAPER_ENV),self.assertRaises(ValueError):
            recover_storage(f.worker_root,approval_ref='synthetic-authorized-compaction',
                expected_runtime=_hash(_release_hashes()),now=f.at+timedelta(days=181))
        self.assertTrue((f.primary/'experiment_halt.json').exists())

    async def test_interruption_after_lifecycle_commit_reconciles_durable_intent(self):
        f,before=await self.fixture()
        def interrupt(stage):
            if stage=='lifecycle':raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError):self.recover(f,fault=interrupt)
        self.assertTrue((f.primary/'experiment_halt.json').exists())
        self.assertEqual(self.recover(f)['status'],'recovered')
