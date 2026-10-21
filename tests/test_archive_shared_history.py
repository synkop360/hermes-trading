"""Original shared evidence across invented retirements; no market replay."""
from contextlib import closing
import hashlib, importlib.util, shutil, sqlite3, unittest
from pathlib import Path

import test_archive_journal as journal_fixture
from hermes_trading import archive_journal
from hermes_trading.paired_packet_storage import store_packet, store_record, load_packet
from hermes_trading.reflection_supervision import _hash, _json


class SharedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=journal_fixture.ArchiveJournalTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=Path(self.fixture.tmp.name); self.path=self.fixture.path
        self.sources=[]

    def capture(self, name):
        target=self.root/name; shutil.copyfile(self.path,target)
        self.sources.append({'path':target,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        return target

    def retire(self, through=2):
        request=self.fixture.prepare(through_sequence=through)
        self.fixture.acknowledge(request); self.fixture.retire(request)
        return request

    def reader(self, **overrides):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.archive_shared_history'),
                             'original shared-history reader is not implemented')
        from hermes_trading import archive_shared_history
        options=dict(
            enabled=True,expected_cohort_sha256='a'*64,expected_experiment_id='synthetic-only',
            cache_root=self.root,max_bytes=4*1024**2,timeout_seconds=10)
        options.update(overrides)
        return archive_shared_history.shared_history(self.sources, **options)

    def append(self,index):
        with closing(sqlite3.connect(self.path)) as db:
            self.fixture.append(db,index); db.commit()

    def test_original_bytes_rowids_and_objects_survive_multiple_retirements(self):
        with closing(sqlite3.connect(self.path)) as db:
            packet={'index':1,'release':{'source':'original'*100}}
            db.execute('BEGIN')
            db.execute('UPDATE paired_batches SET payload=?,input_hash=?,completed=? WHERE rowid=1',
                (store_packet(db,packet),_hash(packet),store_record(db,{n:{'index':1} for n in archive_journal.ACCOUNTS})))
            db.commit()
        first=self.capture('first.sqlite'); self.retire(); self.append(4)
        self.capture('second.sqlite'); self.retire(3); self.append(5); self.capture('current.sqlite')
        before={s['path']:s['path'].read_bytes() for s in self.sources}
        with self.reader() as view:
            self.assertEqual(view.complete,5); self.assertEqual(view.retired_complete,3)
            self.assertEqual([tuple(r) for r in view.history.execute('SELECT rowid,batch_id FROM paired_batches')],
                             [(i,f'batch-{i}') for i in range(1,6)])
            self.assertEqual(view.current.execute('SELECT COUNT(*) FROM paired_batches').fetchone()[0],2)
            self.assertEqual(load_packet(view.history,view.history.execute('SELECT payload FROM paired_batches WHERE rowid=1').fetchone()[0]),packet)
            with closing(sqlite3.connect(first)) as original:
                self.assertEqual(tuple(view.history.execute('SELECT payload,completed FROM paired_batches WHERE rowid=1').fetchone()),
                                 original.execute('SELECT payload,completed FROM paired_batches WHERE rowid=1').fetchone())
                self.assertEqual([tuple(r) for r in view.history.execute('SELECT * FROM paired_packet_objects')],
                                 original.execute('SELECT * FROM paired_packet_objects').fetchall())
            for db in (view.history,view.current):
                with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM paired_batches')
            self.assertIsNone(view.history.execute("SELECT name FROM sqlite_master WHERE name='paired_archive_checkpoint'").fetchone())
        self.assertEqual(before,{s['path']:s['path'].read_bytes() for s in self.sources})
        self.assertFalse(list(self.root.glob('.shared-history-*')))

    def test_missing_prefix_conflict_and_missing_settlement_fail(self):
        for failure in ('missing_prefix','conflict','settlement'):
            with self.subTest(failure=failure):
                self.sources=[]
                old=self.capture(f'{failure}.sqlite')
                if failure=='missing_prefix':
                    with closing(sqlite3.connect(old)) as db:db.execute('DELETE FROM paired_batches WHERE rowid=1');db.commit()
                elif failure=='conflict':
                    with closing(sqlite3.connect(old)) as db:
                        db.execute('UPDATE paired_settlements SET payload=? WHERE batch_id=?',(_json({n:{'changed':1} for n in archive_journal.ACCOUNTS}),'batch-3'));db.commit()
                else:
                    with closing(sqlite3.connect(old)) as db:db.execute("DELETE FROM paired_settlements WHERE batch_id='batch-1'");db.commit()
                self.sources[0]['sha256']=hashlib.sha256(old.read_bytes()).hexdigest()
                self.capture(f'{failure}-current.sqlite')
                with self.assertRaises(ValueError):
                    with self.reader():pass

    def test_retired_prefix_must_match_checkpoint_even_with_valid_rehashed_source(self):
        old=self.capture('old.sqlite');self.retire();self.capture('current.sqlite')
        with closing(sqlite3.connect(old)) as db:
            packet={'index':'changed'}
            db.execute('UPDATE paired_batches SET payload=?,input_hash=? WHERE rowid=1',(_json(packet),_hash(packet)));db.commit()
        self.sources[0]['sha256']=hashlib.sha256(old.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'prefix'):
            with self.reader():pass

    def test_source_cohort_experiment_order_and_pending_are_rejected(self):
        self.capture('old.sqlite');self.retire();self.append(4);self.capture('current.sqlite')
        for overrides in ({'expected_cohort_sha256':'b'*64},{'expected_experiment_id':'other'}):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                with self.reader(**overrides):pass
        self.sources.reverse()
        with self.assertRaises(ValueError):
            with self.reader():pass
        self.sources.reverse()
        source=self.sources[-1]['path']
        with closing(sqlite3.connect(source)) as db:db.execute("UPDATE paired_batches SET status='pending' WHERE rowid=4");db.commit()
        self.sources[-1]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            with self.reader():pass

    def test_disabled_bad_pins_sidecars_and_aggregate_input_bounds(self):
        target=self.capture('current.sqlite')
        for overrides in ({'enabled':False},{'max_bytes':4096},{'timeout_seconds':float('nan')},
                          {'timeout_seconds':10**1000}):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                with self.reader(**overrides):pass
        self.sources[0]['sha256']='0'*64
        with self.assertRaises(ValueError):
            with self.reader():pass
        self.sources[0]['sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        for suffix in ('-wal','-journal'):
            sidecar=target.with_name(target.name+suffix);sidecar.write_bytes(b'not sealed')
            try:
                with self.assertRaisesRegex(ValueError,'snapshot|sidecar'):
                    with self.reader():pass
            finally:sidecar.unlink()
        total=target.stat().st_size
        self.capture('another.sqlite')
        with self.assertRaisesRegex(ValueError,'byte'):
            with self.reader(max_bytes=total):pass

    def test_deadline_and_source_change_during_use_are_not_silently_accepted(self):
        self.capture('current.sqlite');ticks=iter((0,0,11,11,11))
        with self.assertRaises(TimeoutError):
            with self.reader(clock=lambda:next(ticks,11)):pass
        with self.assertRaisesRegex(ValueError,'changed|digest'):
            with self.reader():
                with closing(sqlite3.connect(self.sources[0]['path'])) as db:
                    db.execute("UPDATE paired_identity SET manifest_hash=?",('f'*64,));db.commit()

    def test_retained_suffix_alone_and_corrupt_checkpoint_cannot_recreate_missing_history(self):
        self.retire();target=self.capture('current.sqlite')
        with self.assertRaises(ValueError):
            with self.reader():pass
        with closing(sqlite3.connect(target)) as db:
            db.execute('UPDATE paired_archive_checkpoint SET sha256=?',('0'*64,));db.commit()
        self.sources[-1]['sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'checkpoint'):
            with self.reader():pass

    def test_rehashed_checkpoint_request_must_match_original_next_input(self):
        import json
        self.capture('old.sqlite');self.retire();target=self.capture('current.sqlite')
        with closing(sqlite3.connect(target)) as db:
            row=db.execute('SELECT operation_id,request FROM paired_archive_operations').fetchone()
            changed=json.loads(row[1]);changed['next_input_hash']='0'*64
            db.execute('UPDATE paired_archive_operations SET request=?,request_sha256=? WHERE operation_id=?',
                       (_json(changed),_hash(changed),row[0]));db.commit()
        self.sources[-1]['sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'next input'):
            with self.reader():pass

    def test_missing_original_object_is_not_repaired_from_a_newer_snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            packet={'release':{'source':'original'*100}};db.execute('BEGIN')
            db.execute('UPDATE paired_batches SET payload=?,input_hash=? WHERE rowid=1',
                       (store_packet(db,packet),_hash(packet)));db.commit()
        old=self.capture('old.sqlite');self.capture('current.sqlite')
        with closing(sqlite3.connect(old)) as db:db.execute('DELETE FROM paired_packet_objects');db.commit()
        self.sources[0]['sha256']=hashlib.sha256(old.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'object is missing'):
            with self.reader():pass

    def test_committed_wal_rows_cannot_be_pinned_by_hashing_only_main_file(self):
        target=self.capture('current.sqlite')
        with closing(sqlite3.connect(target)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            self.fixture.append(writer,4);writer.commit()
            self.sources[0]['sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'snapshot|sidecar'):
                with self.reader():pass


if __name__=='__main__':unittest.main()
