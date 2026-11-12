"""Daily SQLite-consistent paper backups and bounded owned generations."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hermes_trading.archive_snapshot import ownership


class PaperBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base/'paper'
        self.root.mkdir()
        self.destination = self.base/'backups'
        with closing(sqlite3.connect(self.root/'worker_runtime.sqlite')) as db:
            db.execute('CREATE TABLE lifecycle(id INTEGER PRIMARY KEY,state TEXT)')
            db.execute("INSERT INTO lifecycle VALUES(1,'running')")
            db.commit()
        (self.root/'strategy.yaml').write_text('entry:\n  fee: taker\n', encoding='utf-8')
        self.journal = sqlite3.connect(self.root/'wallet.sqlite')
        self.addCleanup(self.journal.close)
        self.journal.execute('PRAGMA journal_mode=WAL')
        self.journal.execute('CREATE TABLE observations(at TEXT PRIMARY KEY,equity TEXT)')
        self.journal.execute("INSERT INTO observations VALUES('opening','200')")
        self.journal.commit()
        self.at = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.paper_backup'),
                             'minimum daily paper backup is not implemented')
        return importlib.import_module('hermes_trading.paper_backup')

    def backup(self, **changes):
        options = dict(enabled=True, now=self.at, max_bytes=8*1024**2, timeout_seconds=30, keep=2)
        options.update(changes)
        return self.module().create_daily_backup(self.root, self.destination, **options)

    def test_owned_backup_contains_committed_wal_and_same_day_is_idempotent(self):
        with ownership(self.root, role='worker', enabled=True) as owner:
            result = self.backup(owner_db=owner)
            self.assertEqual(result['status'], 'completed')
            path = Path(result['path'])
            with closing(sqlite3.connect(path/'wallet.sqlite')) as db:
                self.assertEqual(list(db.execute('SELECT * FROM observations')), [('opening', '200')])
            self.assertEqual((path/'strategy.yaml').read_bytes(), (self.root/'strategy.yaml').read_bytes())
            original = {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}
            self.journal.execute("INSERT INTO observations VALUES('later','199')")
            self.journal.commit()
            repeated = self.backup(owner_db=owner)
            self.assertEqual(repeated['status'], 'already_completed')
            self.assertEqual(repeated['sha256'], result['sha256'])
            self.assertEqual(original, {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()})

    def test_only_verified_owned_daily_generations_are_pruned(self):
        self.destination.mkdir()
        unrelated = self.destination/'personal-note.txt'
        unrelated.write_text('preserve', encoding='utf-8')
        paths = []
        for day in range(3):
            result = self.backup(now=self.at+timedelta(days=day))
            paths.append(Path(result['path']))
        self.assertFalse(paths[0].exists())
        self.assertTrue(paths[1].is_dir())
        self.assertTrue(paths[2].is_dir())
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'preserve')
        self.assertEqual((self.root/'strategy.yaml').read_text(encoding='utf-8'), 'entry:\n  fee: taker\n')
        with closing(sqlite3.connect(self.root/'wallet.sqlite')) as db:
            self.assertEqual(list(db.execute('SELECT * FROM observations')), [('opening', '200')])

    def test_disabled_overlapping_and_small_budget_requests_preserve_sources(self):
        before = (self.root/'strategy.yaml').read_bytes()
        with self.assertRaises(ValueError):
            self.backup(enabled=False)
        with self.assertRaises(ValueError):
            self.module().create_daily_backup(self.root, self.root/'nested', enabled=True,
                now=self.at, max_bytes=8*1024**2, timeout_seconds=30, keep=2)
        with self.assertRaises(ValueError):
            self.backup(max_bytes=1)
        self.assertEqual((self.root/'strategy.yaml').read_bytes(), before)
        self.assertFalse(any(self.destination.glob('state-*')) if self.destination.exists() else False)

    def test_same_day_corruption_is_reported_instead_of_silently_reused(self):
        result = self.backup()
        (Path(result['path'])/'strategy.yaml').write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.backup()
        self.assertEqual((self.root/'strategy.yaml').read_text(encoding='utf-8'), 'entry:\n  fee: taker\n')

    def test_lost_publication_reply_keeps_a_complete_backup_and_retry_finishes_rotation(self):
        first = Path(self.backup()['path'])
        self.backup(now=self.at+timedelta(days=1))
        module = self.module()
        original = module.snapshot
        published = []
        def lost_reply(*args, **kwargs):
            result = original(*args, **kwargs)
            published.append(Path(args[1]))
            raise RuntimeError('invented lost return after snapshot publication')
        with patch.object(module, 'snapshot', side_effect=lost_reply):
            with self.assertRaisesRegex(RuntimeError, 'lost return'):
                self.backup(now=self.at+timedelta(days=2))
        self.assertEqual(len(published), 1)
        self.assertTrue((published[0]/module.RECEIPT).is_file(), 'published backup must already contain its completion receipt')
        retry = self.backup(now=self.at+timedelta(days=2))
        self.assertEqual(retry['status'], 'already_completed')
        self.assertFalse(first.exists(), 'same-day retry must finish interrupted owned-generation rotation')

    def test_oversized_metadata_is_rejected_before_open_or_hash(self):
        result = self.backup()
        backup = Path(result['path'])
        receipt = backup/self.module().RECEIPT
        inventory = backup/'snapshot_inventory.json'
        manifest = self.root/'manifest.json'
        original_open = Path.open
        for target, limit in ((receipt, 16384), (inventory, 4*1024**2), (manifest, 4*1024**2)):
            with self.subTest(target=target.name):
                previous = target.read_bytes() if target.exists() else None
                with target.open('wb') as stream:
                    stream.truncate(limit+1)
                def refuse_unbounded_open(path, *args, **kwargs):
                    if path == target:
                        raise AssertionError('oversized metadata must be refused before opening')
                    return original_open(path, *args, **kwargs)
                try:
                    with patch.object(Path, 'open', refuse_unbounded_open):
                        with self.assertRaises(ValueError):
                            self.backup()
                finally:
                    if previous is None:
                        target.unlink()
                    else:
                        target.write_bytes(previous)

    def test_nonregular_source_manifest_is_rejected_before_creating_backup(self):
        (self.root/'manifest.json').mkdir()
        with self.assertRaises(ValueError):
            self.backup()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
