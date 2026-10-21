from contextlib import closing
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path

from hermes_trading.archive_snapshot import snapshot, ownership


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'source';self.root.mkdir()
        self.target=Path(self.tmp.name)/'snapshot'
        self.worker=sqlite3.connect(self.root/'worker_runtime.sqlite');self.worker.execute('CREATE TABLE lifecycle(state)');self.worker.commit();self.worker.close()
        self.source=sqlite3.connect(self.root/'trading.db');self.addCleanup(self.source.close)
        self.source.execute('PRAGMA journal_mode=WAL')
        self.source.execute('CREATE TABLE fills(id INTEGER PRIMARY KEY,fee TEXT)')
        self.source.execute("INSERT INTO fills VALUES(1,'.008')");self.source.commit()
        (self.root/'strategy.yaml').write_text('version: 1\n')

    def invoke(self,**kwargs):
        return snapshot(self.root,self.target,**dict({'enabled':True,'max_bytes':1024*1024,'timeout_seconds':5},**kwargs))

    def test_live_wal_is_backed_up_consistently_without_copying_sidecars(self):
        self.assertGreater((self.root/'trading.db-wal').stat().st_size,0)
        result=self.invoke()
        with closing(sqlite3.connect(self.target/'trading.db')) as db:
            self.assertEqual(db.execute('SELECT * FROM fills').fetchall(),[(1,'.008')])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertFalse((self.target/'trading.db-wal').exists())
        self.assertEqual((self.target/'strategy.yaml').read_bytes(),(self.root/'strategy.yaml').read_bytes())
        inventory=json.loads((self.target/'snapshot_inventory.json').read_text())
        self.assertEqual(hashlib.sha256((self.target/'snapshot_inventory.json').read_bytes()).hexdigest(),result['manifest_sha256'])
        for row in inventory['files']:
            self.assertEqual(hashlib.sha256((self.target/row['path']).read_bytes()).hexdigest(),row['sha256'])
        self.assertFalse(result['independent_backup_verified'])

    def test_running_worker_or_active_source_writer_blocks_snapshot(self):
        with closing(sqlite3.connect(self.root/'worker_runtime.sqlite',isolation_level=None)) as db:
            db.execute('BEGIN EXCLUSIVE')
            with self.assertRaisesRegex((ValueError,sqlite3.OperationalError),'locked|writer'):self.invoke()
        self.assertFalse(self.target.exists())
        self.source.execute("INSERT INTO fills VALUES(2,'.008')")
        with self.assertRaisesRegex((ValueError,sqlite3.OperationalError),'locked|writer'):self.invoke()
        self.source.rollback();self.assertFalse(self.target.exists())

    def test_disabled_budget_deadline_and_fault_never_publish_partial_snapshot(self):
        with self.assertRaisesRegex(ValueError,'disabled'):self.invoke(enabled=False)
        with self.assertRaisesRegex(ValueError,'budget'):self.invoke(max_bytes=1)
        ticks=iter([0,10,20,30,40])
        with self.assertRaisesRegex(TimeoutError,'deadline'):self.invoke(clock=lambda:next(ticks))
        def fault(point):
            if point=='before_publish':raise RuntimeError('lost export')
        with self.assertRaisesRegex(RuntimeError,'lost export'):self.invoke(fault=fault)
        self.assertFalse(self.target.exists())
        self.assertTrue((self.root/'strategy.yaml').exists())

    def test_source_mutation_or_unknown_sidecar_is_not_certified(self):
        (self.root/'orphan.db-wal').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError,'sidecar'):self.invoke()
        (self.root/'orphan.db-wal').unlink()
        def mutate(point):
            if point=='before_source_recheck':(self.root/'strategy.yaml').write_text('version: changed\n')
        with self.assertRaisesRegex(ValueError,'changed'):self.invoke(fault=mutate)
        self.assertFalse(self.target.exists())

    def test_target_overlap_or_existing_target_rejected(self):
        with self.assertRaisesRegex(ValueError,'separate'):
            snapshot(self.root,self.root/'nested',enabled=True,max_bytes=1024*1024,timeout_seconds=5)
        self.target.mkdir()
        with self.assertRaisesRegex(ValueError,'new'):self.invoke()

    def supervisor(self):
        (self.root/'worker_runtime.sqlite').rename(self.root/'watcher_cycle.sqlite')
        with closing(sqlite3.connect(self.root/'llm_review_state.sqlite')) as db:
            db.execute('CREATE TABLE pending(review_id TEXT)')
            db.execute("INSERT INTO pending VALUES('unresolved-review')");db.commit()

    def test_supervisor_capture_uses_real_cycle_owner_and_preserves_review(self):
        self.supervisor()
        result=self.invoke(role='supervisor')
        self.assertEqual(result['ownership_role'],'supervisor')
        self.assertFalse((self.root/'worker_runtime.sqlite').exists())
        with closing(sqlite3.connect(self.target/'llm_review_state.sqlite')) as db:
            self.assertEqual(db.execute('SELECT * FROM pending').fetchall(),[('unresolved-review',)])

    def test_supervisor_capture_excludes_running_cycle(self):
        self.supervisor()
        with ownership(self.root,role='supervisor',enabled=True):
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                self.invoke(role='supervisor')
        self.assertFalse(self.target.exists())

    def test_supplied_cycle_owner_stays_held_across_capture_and_followup(self):
        self.supervisor()
        with ownership(self.root,role='supervisor',enabled=True) as owner:
            self.invoke(role='supervisor',owner_db=owner)
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                with ownership(self.root,role='supervisor',enabled=True):pass
            self.assertEqual(owner.execute('SELECT COUNT(*) FROM lifecycle').fetchone()[0],0)
        with ownership(self.root,role='supervisor',enabled=True):pass

    def test_role_and_supplied_owner_cannot_silently_change_ownership(self):
        with self.assertRaisesRegex(ValueError,'role'):self.invoke(role='other')
        with ownership(self.root,role='worker',enabled=True) as owner:
            with self.assertRaisesRegex(ValueError,'ownership'):
                self.invoke(role='supervisor',owner_db=owner)
            with self.assertRaisesRegex(ValueError,'ownership'):
                self.invoke(owner_db=owner,worker_db=owner)
        self.assertFalse((self.root/'watcher_cycle.sqlite').exists())


if __name__=='__main__':unittest.main()
