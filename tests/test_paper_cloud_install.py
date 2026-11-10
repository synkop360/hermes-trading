import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import base64
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import timedelta

from scripts.paper_cloud_install import decode_checkpoint


class CheckpointTests(unittest.TestCase):
    def test_remaining_space_excludes_verified_retained_rollback_and_counts_stages(self):
        from scripts.paper_cloud_install import required_cutover_space
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);aux=root/'.paper-cloud-cutover';aux.mkdir()
            (root/'retained.bin').write_bytes(b'x'*1000)
            from scripts import paper_release_transition as transition
            roots={'worker':root,'supervisor':aux/'supervisor','launch':aux/'launch.json'}
            for _,role,name,_ in transition.COMPONENTS:
                path=roots[role]/name if role!='launch' else roots[role]
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x'*10)
            expected=2*60+100+16*1024*1024
            self.assertEqual(required_cutover_space(root,aux,100,phases=2),expected)
            stage=aux/'.entry-history-prepare-orphan';stage.mkdir();(stage/'copy').write_bytes(b'x'*500)
            self.assertEqual(required_cutover_space(root,aux,100,phases=2),expected)
            # A durable native intent establishes that its rollback copy has
            # already been published; native reconciliation verifies it.
            phase=aux/'entry-history';phase.mkdir();(phase/'intent.json').write_text('{}')
            self.assertEqual(required_cutover_space(root,aux,100,phases=2),60+100+16*1024*1024)

    def test_closed_wal_is_backed_up_and_checkpointed_without_changing_rows(self):
        from scripts.paper_cloud_install import recover_journals
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);aux=root/'.paper-cloud-cutover';aux.mkdir();database=root/'retained.sqlite'
            program=('import sqlite3,os; d=sqlite3.connect('+repr(str(database))+'); '
                'd.execute("PRAGMA journal_mode=WAL"); d.execute("CREATE TABLE retained (value TEXT)"); '
                'd.execute("INSERT INTO retained VALUES (\'original\')"); d.commit(); os._exit(0)')
            subprocess.run([sys.executable,'-c',program],check=True,timeout=10)
            self.assertTrue(database.with_name(database.name+'-wal').exists())
            result=recover_journals(root,aux)
            self.assertEqual(result['databases'],1)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT value FROM retained').fetchall(),[('original',)])
            self.assertFalse(database.with_name(database.name+'-wal').exists())
            with zipfile.ZipFile(aux/result['archive']) as archive:
                self.assertIn('retained.sqlite-wal',archive.namelist())

    def test_compaction_keeps_verified_evidence_and_does_not_touch_active_state(self):
        from scripts.paper_cloud_install import compact_audits
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);aux=root/'.paper-cloud-cutover';aux.mkdir()
            original=aux/'cloud-source/rollback/worker';original.mkdir(parents=True)
            (original/'original.bin').write_bytes(b'original evidence')
            (aux/'installed.json').write_text('{}');active=root/'active';active.write_bytes(b'retained')
            compact_audits(aux)
            self.assertFalse((aux/'cloud-source').exists())
            self.assertEqual(active.read_bytes(),b'retained')
            with zipfile.ZipFile(aux/'native-transition-audits.zip') as archive:
                self.assertEqual(archive.read('cloud-source/rollback/worker/original.bin'),b'original evidence')
            compact_audits(aux)

    def archive(self, *, name='wallet_account.json', extra=None, bad_hash=False):
        raw=b'{"private":"account"}'
        manifest=dict(schema_version=1,experiment_id='paired-test',
            files={name:'0'*64 if bad_hash else hashlib.sha256(raw).hexdigest()},
            source_launch_sha256=hashlib.sha256(b'{}').hexdigest())
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('supervisor/'+name,raw);archive.writestr('launch.json',b'{}')
            archive.writestr('manifest.json',json.dumps(manifest))
            if extra:archive.writestr(extra,b'private')
        return output.getvalue()

    def decode(self,raw):return decode_checkpoint(raw,hashlib.sha256(raw).hexdigest(),'paired-test')

    def test_exact_archive_is_verified_without_writing_or_logging(self):
        files,launch,manifest=self.decode(self.archive())
        self.assertEqual(files['wallet_account.json'],b'{"private":"account"}')
        self.assertEqual(launch,b'{}');self.assertEqual(manifest['experiment_id'],'paired-test')

    def test_checksum_identity_and_member_hashes_are_mandatory(self):
        raw=self.archive()
        with self.assertRaises(ValueError):decode_checkpoint(raw,'0'*64,'paired-test')
        with self.assertRaises(ValueError):decode_checkpoint(raw,hashlib.sha256(raw).hexdigest(),'other')
        with self.assertRaises(ValueError):self.decode(self.archive(bad_hash=True))

    def test_paths_extra_members_and_sidecars_are_rejected(self):
        for name in ['../wallet.json','/wallet.json','a/../../wallet.json','wallet.sqlite-wal','a\\wallet.json']:
            with self.subTest(name=name),self.assertRaises(ValueError):self.decode(self.archive(name=name))
        with self.assertRaises(ValueError):self.decode(self.archive(extra='secret.env'))

    def test_compressed_bomb_is_rejected_before_extracting(self):
        raw=self.archive()
        with patch('scripts.paper_cloud_install.MAX_UNCOMPRESSED',1), \
                patch.object(zipfile.ZipFile,'read',side_effect=AssertionError('read before size admission')):
            with self.assertRaises(ValueError):self.decode(raw)


if __name__=='__main__':unittest.main()


class NativeCloudInstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_and_restart_preserve_native_journal_and_original_account(self):
        from scripts import paper_cloud_install as install_module, native_hermes_watch
        import release_transition_fixtures as fixtures
        from test_paper_cloud_release_transition import CloudReleaseTransitionTests
        from hermes_trading.reflection_supervision import _hash
        plan=CloudReleaseTransitionTests().plan();plan['target']['experiment_id']='paired-test'
        with patch.object(fixtures,'OLD_COMMIT',plan['old_commit']), \
                patch.object(fixtures.NativeTransitionFixture,'_releases',return_value=(plan['old'],plan['new'])):
            f=await fixtures.NativeTransitionFixture.create(self)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);project=Path(tmp.name).resolve()
        root=project/'state';shutil.copytree(f.worker_root,root)
        scripts=project/'scripts';scripts.mkdir()
        for name in ['paper_local_transport.py','paper_supervisor_service.py']:
            shutil.copyfile(Path(native_hermes_watch.__file__).with_name(name),scripts/name)
        source_launch=json.loads(f.launch_file.read_text());source_launch['paper_safety_enabled']=True
        launch=json.dumps(source_launch).encode()
        rows={p.relative_to(f.supervisor_root).as_posix():p.read_bytes() for p in f.supervisor_root.rglob('*') if p.is_file()}
        manifest=dict(schema_version=1,experiment_id='paired-test',
            files={name:hashlib.sha256(raw).hexdigest() for name,raw in rows.items()},
            source_launch_sha256=hashlib.sha256(launch).hexdigest())
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in rows.items():archive.writestr('supervisor/'+name,raw)
            archive.writestr('launch.json',launch);archive.writestr('manifest.json',json.dumps(manifest))
        raw=output.getvalue()
        env=dict(fixtures.PAPER_ENV,HERMES_CLOUD_CUTOVER_APPROVAL='synthetic-cloud-owner',
            HERMES_CLOUD_CHECKPOINT_SHA256=hashlib.sha256(raw).hexdigest(),HERMES_CLOUD_CHECKPOINT_PARTS='1',
            HERMES_CLOUD_CHECKPOINT_00=base64.b64encode(raw).decode())
        env.update({'RAILWAY_'+key.upper()+'_ID':value for key,value in install_module.transition.TARGET.items()})
        account_before=(root/'adaptive_200/wallet_account.json').read_bytes()
        def reviews(path):
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
                return db.execute('SELECT * FROM reviews ORDER BY rowid').fetchall()
        before=reviews(f.supervisor_root/'llm_review_state.sqlite')
        with patch.dict(os.environ,fixtures.PAPER_ENV),patch('hermes_trading.supervisor_launch._now',return_value=f.at):
            installed=install_module.install(project,plan,env=env,experiment_id='paired-test')
            self.assertEqual(installed['status'],'installed')
            env.pop('HERMES_CLOUD_CHECKPOINT_00');env.pop('HERMES_CLOUD_CHECKPOINT_PARTS')
            restarted=install_module.install(project,plan,env=env,experiment_id='paired-test')
        self.assertEqual(restarted['status'],'already_installed')
        # Preserve every identity/path/release check after the original finite
        # supervisor deadline, without extending or reactivating that deadline.
        with patch.dict(os.environ,fixtures.PAPER_ENV),patch('hermes_trading.supervisor_launch._now',return_value=f.at+timedelta(days=181)):
            self.assertEqual(install_module.install(project,plan,env=env,experiment_id='paired-test')['status'],'already_installed')
            from hermes_trading.supervisor_launch import validate_launch
            with self.assertRaisesRegex(ValueError,'expired'):
                validate_launch(root/'cloud-supervisor-launch.json',project,plan['new'])
        self.assertEqual((root/'adaptive_200/wallet_account.json').read_bytes(),account_before)
        target=root/'paired-supervisor/paired-test/adaptive_200'
        self.assertEqual(reviews(target/'llm_review_state.sqlite'),before)
        life=json.loads((target/'supervisor_lifecycle.json').read_text())
        original=json.loads((f.supervisor_root/'supervisor_lifecycle.json').read_text())
        self.assertEqual({k:v for k,v in life.items() if k!='binding'},{k:v for k,v in original.items() if k!='binding'})
        self.assertTrue((root/'cloud-supervisor-launch.py').is_file())
        self.assertEqual(json.loads((root/'cloud-supervisor-launch.json').read_text())['transport'],'local')
