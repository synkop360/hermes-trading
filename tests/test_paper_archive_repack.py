import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from scripts import paper_cloud_install as install
from scripts import paper_archive_repack as repack


class ArchiveRepackTests(unittest.TestCase):
    def prepare(self,root):
        aux=root/'.paper-cloud-cutover';aux.mkdir()
        (aux/'owner.json').write_text(json.dumps({'purpose':'paper_cloud_auxiliary_v1'}))
        (aux/'installed.json').write_text(json.dumps({'status':'installed'}))
        source=root/'old.db';source.write_bytes(bytes(range(256))*10000)
        archive=aux/('closed-journals-'+'a'*32+'.zip')
        install._archive_evidence(root,[source],archive)
        return aux,archive,install._verify_evidence(archive)

    def test_repack_preserves_every_member_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            aux,path,hashes=self.prepare(Path(tmp).resolve());old=path.read_bytes()
            result=repack.repack_archives(aux)
            self.assertGreater(result['bytes_reclaimed'],0)
            self.assertEqual(install._verify_evidence(path),hashes)
            with zipfile.ZipFile(path) as z:self.assertEqual(z.getinfo('old.db').compress_type,zipfile.ZIP_LZMA)
            self.assertEqual(repack.repack_archives(aux)['bytes_reclaimed'],0)
            self.assertNotEqual(path.read_bytes(),old)

    def test_raw_digest_pinned_archive_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            aux,path,_=self.prepare(Path(tmp).resolve());old=path.read_bytes()
            (aux/'external.json').write_text(json.dumps({'sha256':hashlib.sha256(old).hexdigest()}))
            self.assertEqual(repack.repack_archives(aux)['bytes_reclaimed'],0)
            self.assertEqual(path.read_bytes(),old)

    def test_interrupted_before_replace_can_resume_without_losing_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            aux,path,hashes=self.prepare(Path(tmp).resolve());old=path.read_bytes()
            def fail():raise RuntimeError('interrupted')
            with self.assertRaises(RuntimeError):repack.repack_archives(aux,fault=fail)
            self.assertEqual(path.read_bytes(),old)
            repack.repack_archives(aux)
            self.assertEqual(install._verify_evidence(path),hashes)

    def test_changed_archive_after_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            aux,path,_=self.prepare(Path(tmp).resolve());repack.repack_archives(aux)
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):repack.repack_archives(aux)
