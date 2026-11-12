import gzip,hashlib,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing


class CompactHistoryTests(unittest.TestCase):
    def legacy_database(self):
        from hermes_trading.paper_publication import _release_hashes
        from hermes_trading.reflection_supervision import _hash
        predecessor=json.loads((Path(__file__).resolve().parents[1]/'scripts/cloud_release_plan.json').read_text())['new']['runtime']
        self.assertEqual(_hash(predecessor),'082042439d5dadd8247b788fe6b29de5c7449f165f339a2e155761bf55c525de')
        source=patch('hermes_trading.paper_publication._release_hashes',return_value=predecessor)
        source.start();self.addCleanup(source.stop)
        names=[f'{arm}_{wallet}' for arm in ('adaptive','control') for wallet in (50,100,150,200)]
        (self.root/'manifest.json').write_text(json.dumps({'experiment_id':'retained-test','status':'paper_ready',
            'execution_enabled':True,'profiles':[{'directory':name} for name in names]}))
        (self.root/'.paper-cloud-cutover').mkdir();(self.root/'.paper-cloud-cutover/installed.json').write_text('{"status":"installed"}')
        with closing(sqlite3.connect(self.root/'worker_runtime.sqlite')) as db:
            db.execute('CREATE TABLE lifecycle(id INTEGER PRIMARY KEY,state TEXT)')
            db.execute('INSERT INTO lifecycle VALUES(1,?)',(json.dumps({'storage_stop':True}),));db.commit()
        for name in names:
            (self.root/name).mkdir(exist_ok=True)
            with closing(sqlite3.connect(self.root/name/'trading.db')) as db:
                db.execute('CREATE TABLE positions(status TEXT)');db.commit()
        database=self.root/'trading.db'
        with closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE historical(value TEXT)')
            db.executemany('INSERT INTO historical VALUES(?)',[('retained original history',)]*10000);db.commit()
        return database

    def test_legacy_database_roundtrip_preserves_every_original_byte(self):
        database=self.legacy_database();original=database.read_bytes()
        result=self.compact(legacy_database_only=True)
        self.assertGreater(result['bytes_reclaimed'],0)
        self.assertFalse(database.exists())
        archive=self.root/'.legacy-database-compact/trading.db.gz'
        self.assertEqual(gzip.decompress(archive.read_bytes()),original)
        self.assertEqual((self.root/'adaptive_200/decisions.jsonl').read_bytes(),b'current account journal')
        with closing(sqlite3.connect(self.root/'worker_runtime.sqlite')) as db:
            db.execute('UPDATE lifecycle SET state=? WHERE id=1',(json.dumps({'storage_stop':False}),));db.commit()
        self.assertEqual(self.compact(legacy_database_only=True)['bytes_reclaimed'],0)

    def test_legacy_database_with_sidecar_is_never_retired(self):
        database=self.legacy_database();original=database.read_bytes()
        database.with_name('trading.db-wal').write_bytes(b'not resolved')
        with self.assertRaises(ValueError):self.compact(legacy_database_only=True)
        self.assertEqual(database.read_bytes(),original)

    def test_clean_wal_mode_legacy_database_has_exact_archive_roundtrip(self):
        database=self.legacy_database()
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(),('wal',))
        self.assertFalse(database.with_name(database.name+'-wal').exists())
        original=database.read_bytes()
        result=self.compact(legacy_database_only=True)
        self.assertGreater(result['bytes_reclaimed'],0)
        self.assertEqual(gzip.decompress((self.root/'.legacy-database-compact/trading.db.gz').read_bytes()),original)

    def test_legacy_database_aliasing_active_wallet_is_never_retired(self):
        database=self.legacy_database();active=self.root/'adaptive_200/trading.db'
        active.unlink();os.link(database,active)
        with self.assertRaises(ValueError):self.compact(legacy_database_only=True)
        self.assertTrue(database.exists());self.assertTrue(active.exists())
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
