import gzip,hashlib,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing


class CompactHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        (self.root/'manifest.json').write_text(json.dumps({'experiment_id':'retained-test'}))
        with closing(sqlite3.connect(self.root/'worker_runtime.sqlite')) as db:db.execute('CREATE TABLE original(value)')
        self.original=b'original legacy financial history\n'*20000
        (self.root/'decisions.jsonl').write_bytes(self.original)
        account=self.root/'adaptive_200';account.mkdir()
        (account/'decisions.jsonl').write_bytes(b'current account journal')

    def compact(self,**kwargs):
        from scripts.paper_compact_history import compact_history
        with patch.dict(os.environ,{'HERMES_TRADING_MODE':'paper','HERMES_TRADING_ALLOW_REAL_ORDERS':'false'}):
            return compact_history(self.root,enabled=True,retire_originals=kwargs.pop('retire_originals',True),**kwargs)

    def test_archive_only_preserves_every_original_and_reports_potential_gain(self):
        result=self.compact(retire_originals=False)
        self.assertEqual(result['bytes_reclaimed'],0)
        self.assertGreater(result['bytes_reclaimable'],len(self.original)//2)
        self.assertEqual((self.root/'decisions.jsonl').read_bytes(),self.original)

    def test_compressed_history_is_exact_and_current_wallet_is_untouched(self):
        result=self.compact()
        self.assertGreater(result['bytes_reclaimed'],len(self.original)//2)
        self.assertFalse((self.root/'decisions.jsonl').exists())
        archive=self.root/'.legacy-history-compact/decisions.jsonl.gz'
        self.assertEqual(gzip.decompress(archive.read_bytes()),self.original)
        self.assertEqual((self.root/'adaptive_200/decisions.jsonl').read_bytes(),b'current account journal')
        self.assertEqual(self.compact()['bytes_reclaimed'],0)

    def test_restart_after_receipt_reuses_verified_archive(self):
        def interrupt(stage):
            if stage=='receipt':raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):self.compact(fault=interrupt)
        self.assertTrue((self.root/'decisions.jsonl').exists())
        self.compact()
        self.assertFalse((self.root/'decisions.jsonl').exists())

    def test_corrupt_archive_and_changed_original_are_never_deleted(self):
        def interrupt(stage):
            if stage=='receipt':raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):self.compact(fault=interrupt)
        archive=self.root/'.legacy-history-compact/decisions.jsonl.gz'
        archive.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.compact()
        self.assertEqual((self.root/'decisions.jsonl').read_bytes(),self.original)

    def test_existing_worker_lease_blocks_compaction(self):
        from hermes_trading.archive_snapshot import ownership
        with ownership(self.root,role='worker',enabled=True):
            with self.assertRaises(sqlite3.OperationalError):self.compact()
        self.assertEqual((self.root/'decisions.jsonl').read_bytes(),self.original)

    def test_changed_original_after_receipt_is_retained(self):
        def interrupt(stage):
            if stage=='receipt':raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):self.compact(fault=interrupt)
        (self.root/'decisions.jsonl').write_bytes(b'new legacy writer')
        with self.assertRaises(ValueError):self.compact()
        self.assertEqual((self.root/'decisions.jsonl').read_bytes(),b'new legacy writer')
