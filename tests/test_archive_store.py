import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
import subprocess
import sys
import unittest
import zlib
from unittest.mock import patch
import hermes_trading.archive_store as store

from hermes_trading.archive_store import seal, restore, validate, CHUNK_BYTES


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.archive = self.root / 'archive'
        (self.source / 'records.bin').write_bytes(b'original\x00evidence' * 300)
        (self.source / 'empty.txt').write_bytes(b'')

    def seal(self, **kwargs):
        return seal(self.source, self.archive, ['records.bin', 'empty.txt'],
                    experiment='synthetic-test', first_batch=1, last_batch=3, **kwargs)

    def test_byte_exact_roundtrip_and_existing_target_rejected(self):
        digest = self.seal()
        target = self.root / 'restored'
        receipt = restore(self.archive, digest, target)
        self.assertFalse(receipt['remote_retirement_authorized'])
        for name in ('records.bin', 'empty.txt'):
            self.assertEqual((target / name).read_bytes(), (self.source / name).read_bytes())
        with self.assertRaises(ValueError):
            restore(self.archive, digest, target)

    def test_object_directory_changes_sync_before_manifest_publication(self):
        events=[];replace=store.os.replace
        def synced(path):events.append(('sync',Path(path)));return True
        def replaced(source,target):
            result=replace(source,target);events.append(('publish',Path(target)));return result
        with patch.object(store,'_sync_directory',side_effect=synced,create=True),patch.object(store.os,'replace',side_effect=replaced):
            self.seal()
        published=[(index,path) for index,(kind,path) in enumerate(events) if kind=='publish']
        for index,path in published:
            self.assertTrue(any(kind=='sync' and directory==path.parent for kind,directory in events[index+1:]),path)
        manifest_index=next(index for index,path in published if path.parent.name=='manifests')
        object_indexes=[index for index,path in published if path.parent.name=='objects']
        self.assertTrue(object_indexes)
        for index in object_indexes:
            self.assertTrue(any(kind=='sync' and path==self.archive/'objects' for kind,path in events[index+1:manifest_index]))
        self.assertIn(('sync',self.archive.parent),events)

    def test_chain_continuity_identity_and_missing_parent(self):
        first = self.seal()
        (self.source / 'records.bin').write_bytes(b'new state, old history retained in predecessor')
        second = seal(self.source, self.archive, ['records.bin', 'empty.txt'],
                      experiment='synthetic-test', first_batch=4, last_batch=6, parent=first)
        self.assertEqual(len(validate(self.archive, second)), 2)
        for start, identity in ((3, 'synthetic-test'), (5, 'synthetic-test'), (4, 'other')):
            with self.assertRaises(ValueError):
                seal(self.source, self.archive, ['records.bin'], experiment=identity,
                     first_batch=start, last_batch=6, parent=first)
        (self.archive / 'manifests' / (first + '.json')).unlink()
        with self.assertRaises(ValueError):
            restore(self.archive, second, self.root / 'no-parent')

    def test_tampered_manifest_rejected(self):
        digest = self.seal()
        path = self.archive / 'manifests' / (digest + '.json')
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            validate(self.archive, digest)

    def test_missing_corrupt_and_trailing_objects_rejected(self):
        digest = self.seal()
        obj = next((self.archive / 'objects').glob('*'))
        original = obj.read_bytes()
        for replacement in (b'corrupt', original + b'trailing', zlib.compress(b'wrong')):
            obj.write_bytes(replacement)
            with self.assertRaises(ValueError):
                restore(self.archive, digest, self.root / 'bad')
        obj.unlink()
        with self.assertRaises(ValueError):
            validate(self.archive, digest)

    def test_bounded_inflate_rejects_bomb(self):
        digest = self.seal()
        obj = next((self.archive / 'objects').glob('*'))
        obj.write_bytes(zlib.compress(b'x' * (CHUNK_BYTES + 1)))
        with self.assertRaises(ValueError):
            validate(self.archive, digest)

    def test_unsafe_paths_case_collision_and_nonempty_wal_rejected(self):
        for inventory in (['../escape'], ['/absolute'], ['C:/escape'], ['a\\b'],
                          ['records.bin', 'RECORDS.bin'], ['records.bin', 'records.bin']):
            with self.assertRaises(ValueError):
                seal(self.source, self.archive, inventory, experiment='synthetic-test', first_batch=1, last_batch=3)
        (self.source / 'trading.db-wal').write_bytes(b'uncheckpointed')
        with self.assertRaises(ValueError):
            self.seal()

    def test_generated_chunk_boundaries_and_zero_bytes(self):
        rng = random.Random(20261015)
        for index, size in enumerate((0, 1, 127, 4096, CHUNK_BYTES - 1, CHUNK_BYTES, CHUNK_BYTES + 17)):
            raw = rng.randbytes(size)
            (self.source / 'records.bin').write_bytes(raw)
            digest = self.seal()
            target = self.root / ('generated-' + str(index))
            restore(self.archive, digest, target)
            self.assertEqual(hashlib.sha256((target / 'records.bin').read_bytes()).digest(),
                             hashlib.sha256(raw).digest())

    def test_failed_seal_publishes_no_manifest_and_retry_reuses_objects(self):
        def fail(stage):
            if stage == 'before_manifest':
                raise OSError('synthetic interruption')
        before = (self.source / 'records.bin').read_bytes()
        with self.assertRaises(OSError):
            self.seal(fault=fail)
        self.assertEqual(list((self.archive / 'manifests').glob('*.json')), [])
        self.assertEqual((self.source / 'records.bin').read_bytes(), before)
        digest = self.seal()
        self.assertEqual(len(validate(self.archive, digest)), 1)

    def test_failed_restore_does_not_publish_and_can_retry(self):
        digest = self.seal()
        target = self.root / 'interrupted'
        def fail(stage):
            if stage == 'before_restore_publish':
                raise OSError('synthetic interruption')
        with self.assertRaises(OSError):
            restore(self.archive, digest, target, fault=fail)
        self.assertFalse(target.exists())
        restore(self.archive, digest, target)
        self.assertTrue(target.exists())

    def test_source_or_destination_symlink_not_followed(self):
        outside = self.root / 'outside'; outside.mkdir()
        try:
            (self.source / 'link').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('host does not allow unprivileged symlinks; Linux verification required')
        with self.assertRaises(ValueError):
            seal(self.source, self.archive, ['link/secret'], experiment='synthetic-test', first_batch=1, last_batch=3)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_windows_junction_not_followed(self):
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'secret').write_bytes(b'outside selected source')
        link = self.source / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest('host cannot create junction')
        with self.assertRaises(ValueError):
            seal(self.source, self.archive, ['junction/secret'], experiment='synthetic-test', first_batch=1, last_batch=3)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO regression')
    def test_fifo_at_object_or_manifest_is_rejected_without_blocking(self):
        digest = self.seal()
        paths = [next((self.archive / 'objects').glob('*')),
                 self.archive / 'manifests' / (digest + '.json')]
        code = ('import sys; from hermes_trading.archive_store import validate; '
                'from pathlib import Path\n'
                'try: validate(Path(sys.argv[1]),sys.argv[2])\n'
                'except ValueError: sys.exit(0)\n'
                'sys.exit(3)')
        for path in paths:
            original = path.read_bytes(); path.unlink(); os.mkfifo(path)
            try:
                try:
                    result = subprocess.run([sys.executable, '-c', code, str(self.archive), digest],
                                            cwd=Path(__file__).parent, capture_output=True, timeout=2)
                except subprocess.TimeoutExpired:
                    self.fail('non-regular evidence blocked instead of being rejected')
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                path.unlink(); path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
