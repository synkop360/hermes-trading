"""Portable snapshot handling for literal plus signs in evidence names."""
from pathlib import Path
import os
import tarfile
import unittest

import test_paper_backup as backup_fixtures
from scripts import pull_paper_backup


class SnapshotPlusNameTests(unittest.TestCase):
    def setUp(self):
        self.fixture = backup_fixtures.PaperBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_empty_lost_found_is_accepted(self):
        (self.fixture.root/'lost+found').mkdir()
        result = self.fixture.backup()
        self.assertEqual(result['status'], 'completed')

    def test_populated_nested_plus_names_keep_exact_bytes_through_daily_pull(self):
        cases = {
            'lost+found/evidence+raw.bin': b'\x00original+bytes\xff\n',
            'lost+found/nested+dir/packet+01.dat': bytes(range(256)),
        }
        for relative, payload in cases.items():
            path = self.fixture.root/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        result = self.fixture.backup()
        daily = Path(result['path'])
        for relative, payload in cases.items():
            self.assertEqual((daily/relative).read_bytes(), payload)

        store = self.fixture.base/'offhost'
        day = self.fixture.at.date().isoformat()

        def download(name, target):
            with tarfile.open(target, 'w') as archive:
                archive.add(daily, arcname=name, recursive=True)

        pulled = pull_paper_backup.pull_completed(
            store, source_identity=result['source_identity'], day=day, download=download)
        self.assertTrue(pulled['off_host_verified'])
        for relative, payload in cases.items():
            self.assertEqual((Path(pulled['path'])/relative).read_bytes(), payload)

    def test_forbidden_character_guard_remains(self):
        name = 'lost+found/evidence raw'
        path = self.fixture.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'guard')
        with self.assertRaisesRegex(ValueError, 'nonportable snapshot evidence name'):
            self.fixture.backup()

    @unittest.skipIf(os.name == 'nt', 'Windows normalizes trailing dots in path names')
    def test_trailing_dot_guard_remains_on_filesystems_that_preserve_it(self):
        name = 'lost+found/evidence.'
        path = self.fixture.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'guard')
        with self.assertRaisesRegex(ValueError, 'nonportable snapshot evidence name'):
            self.fixture.backup()


if __name__ == '__main__':
    unittest.main()
