"""Offline copy checks against actual consistent SQLite snapshot artifacts."""
from datetime import timedelta
import io
import os
from pathlib import Path
import shutil
import tarfile
import unittest

import test_paper_backup as backup_fixtures
from scripts import pull_paper_backup


class DailyPullTests(unittest.TestCase):
    def setUp(self):
        self.fixture=backup_fixtures.PaperBackupTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.api=pull_paper_backup
        self.copy=self.fixture.backup()
        self.source=Path(self.copy['path'])
        self.store=self.fixture.base/'offhost'
        self.day=self.fixture.at.date().isoformat()

    def download(self,name,target):
        self.assertEqual(name,self.source.name)
        with tarfile.open(target,'w') as archive:
            archive.add(self.source,arcname=name,recursive=True)

    def invoke(self,**changes):
        options=dict(source_identity=self.copy['source_identity'],day=self.day,download=self.download)
        options.update(changes)
        return self.api.pull_completed(self.store,**options)

    def test_complete_copy_preserves_sqlite_and_same_day_retry_uses_verified_cache(self):
        result=self.invoke()
        self.assertTrue(result['off_host_verified'])
        destination=Path(result['path'])
        self.assertEqual((destination/'wallet.sqlite').read_bytes(),(self.source/'wallet.sqlite').read_bytes())
        self.assertEqual((destination/'strategy.yaml').read_bytes(),(self.source/'strategy.yaml').read_bytes())
        def unexpected(*args):raise AssertionError('same completed copy must not be downloaded again')
        repeated=self.invoke(download=unexpected)
        self.assertEqual(repeated['status'],'already_completed')

    def test_failed_transport_does_not_publish_and_removes_only_owned_partial(self):
        def failed(name,target):
            target.write_bytes(b'incomplete')
            raise OSError('private transport details')
        with self.assertRaises(OSError):self.invoke(download=failed)
        self.assertFalse((self.store/self.source.name).exists())
        self.assertFalse(list(self.store.glob('.daily-pull-*')))
        self.assertTrue(self.source.exists())

    def test_links_parent_traversal_and_foreign_top_directory_reject_before_publication(self):
        for name,kind in (('../escaped',tarfile.REGTYPE),(self.source.name+'/linked',tarfile.SYMTYPE),
                          ('foreign/file',tarfile.REGTYPE)):
            def unsafe(request,target):
                with tarfile.open(target,'w') as archive:
                    member=tarfile.TarInfo(name);member.type=kind
                    if kind==tarfile.SYMTYPE:member.linkname='../../escaped'
                    archive.addfile(member,io.BytesIO(b''))
            with self.subTest(name=name),self.assertRaises(ValueError):self.invoke(download=unsafe)
            self.assertFalse((self.store/self.source.name).exists())
        self.assertFalse((self.store.parent/'escaped').exists())

    def test_changed_snapshot_member_is_rejected_by_original_inventory(self):
        (self.source/'strategy.yaml').write_text('tampered',encoding='utf-8')
        with self.assertRaises(ValueError):self.invoke()
        self.assertFalse((self.store/self.source.name).exists())

    def test_two_owned_generations_and_unrelated_files_are_preserved(self):
        self.store.mkdir()
        note=self.store/'personal-note.txt';note.write_text('preserve',encoding='utf-8')
        paths=[]
        for day in range(3):
            self.copy=self.fixture.backup(now=self.fixture.at+timedelta(days=day))
            self.source=Path(self.copy['path']);self.day=(self.fixture.at+timedelta(days=day)).date().isoformat()
            paths.append(Path(self.invoke()['path']))
        self.assertFalse(paths[0].exists());self.assertTrue(paths[1].is_dir());self.assertTrue(paths[2].is_dir())
        self.assertEqual(note.read_text(), 'preserve')

    def test_declared_oversized_tar_member_fails_without_extracting_it(self):
        def oversized(name,target):
            member=tarfile.TarInfo(name+'/oversized');member.size=500000001
            with target.open('wb') as stream:stream.write(member.tobuf())
        with self.assertRaises(ValueError):self.invoke(download=oversized)
        self.assertFalse((self.store/self.source.name).exists())

    def test_old_day_cannot_report_success_after_retiring_its_requested_copy(self):
        original=self.copy
        saved=self.fixture.base/'preserved-old-day'
        for day in range(3):
            self.copy=self.fixture.backup(now=self.fixture.at+timedelta(days=day))
            self.source=Path(self.copy['path']);self.day=(self.fixture.at+timedelta(days=day)).date().isoformat()
            result=self.invoke()
            if day==0:shutil.copytree(result['path'],saved)
        self.copy=original;self.day=self.fixture.at.date().isoformat()
        old=self.store/original['name']
        def unexpected(*args):raise AssertionError('outdated day must reject before transport')
        for cached in (False,True):
            with self.subTest(cached=cached):
                if cached:shutil.copytree(saved,old)
                before=sorted(p.name for p in self.store.iterdir())
                with self.assertRaisesRegex(ValueError,'retention'):
                    self.invoke(download=unexpected)
                self.assertEqual(sorted(p.name for p in self.store.iterdir()),before)

    def test_linked_lock_cannot_write_to_an_outside_empty_file(self):
        self.store.mkdir()
        outside=self.fixture.base/'outside-financial-evidence'
        outside.write_bytes(b'')
        try:os.link(outside,self.store/'daily_pull.lock')
        except OSError:self.skipTest('hard links unavailable')
        def unexpected(*args):raise AssertionError('linked lock must reject before transport')
        try:
            with self.assertRaises(ValueError):self.invoke(download=unexpected)
        finally:self.assertEqual(outside.read_bytes(),b'')


if __name__=='__main__':unittest.main()
