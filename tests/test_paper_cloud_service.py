import asyncio
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock
import unittest
from unittest.mock import patch

from scripts.paper_cloud_service import serve_cloud,install_default


class CloudServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_start_skips_completed_source_updates(self):
        from hermes_trading.reflection_supervision import _hash
        from shutil import copyfile
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);scripts=project/'scripts';scripts.mkdir()
            source=Path(__file__).resolve().parents[1]/'scripts'
            for name in ('cloud_release_plan.json','discord_format_release_plan.json','storage_limit_release_plan.json',
                         'backup_restart_release_plan.json','lot1_release_plan.json','entry_history_release_plan.json'):
                copyfile(source/name,scripts/name)
            installed=project/'state/.paper-cloud-cutover/installed.json';installed.parent.mkdir(parents=True)
            successor=json.loads((scripts/'lot1_release_plan.json').read_text())
            installed.write_text(json.dumps({'release_hash':_hash(successor['new'])}))
            # The checkout is the lot 1 release (LAT-120), last step of the chain.
            with patch('scripts.paper_cloud_update.update_installed') as update, \
                    patch('scripts.paper_cloud_service.install',return_value={'status':'already_installed'}) as install:
                self.assertEqual(install_default(project)['status'],'already_installed')
            update.assert_not_called()
            self.assertEqual(install.call_args.args[1]['new'],successor['new'])

    async def test_checkout_matching_no_reviewed_plan_is_never_installed(self):
        # A checkout that differs from the last reviewed step (here, the bare
        # backup restart release, without lot 1) is refused, without any
        # source update.
        from hermes_trading.reflection_supervision import _hash
        from shutil import copyfile
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);scripts=project/'scripts';scripts.mkdir()
            source=Path(__file__).resolve().parents[1]/'scripts'
            for name in ('cloud_release_plan.json','discord_format_release_plan.json','storage_limit_release_plan.json',
                         'backup_restart_release_plan.json','lot1_release_plan.json','entry_history_release_plan.json'):
                copyfile(source/name,scripts/name)
            installed=project/'state/.paper-cloud-cutover/installed.json';installed.parent.mkdir(parents=True)
            backup=json.loads((scripts/'backup_restart_release_plan.json').read_text())
            installed.write_text(json.dumps({'release_hash':_hash(backup['new'])}))
            with patch('scripts.paper_cloud_update.update_installed') as update, \
                    patch('scripts.hermes_watch._supervisor_release',return_value=backup['new']), \
                    patch('scripts.paper_cloud_service.install') as install, \
                    self.assertRaisesRegex(ValueError,'storage limit source map differs'):
                install_default(project)
            update.assert_not_called();install.assert_not_called()

    async def test_start_advances_only_the_pending_storage_source(self):
        from hermes_trading.reflection_supervision import _hash
        from shutil import copyfile
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);scripts=project/'scripts';scripts.mkdir()
            source=Path(__file__).resolve().parents[1]/'scripts'
            for name in ('cloud_release_plan.json','discord_format_release_plan.json','storage_limit_release_plan.json',
                         'backup_restart_release_plan.json','lot1_release_plan.json','entry_history_release_plan.json'):
                copyfile(source/name,scripts/name)
            installed=project/'state/.paper-cloud-cutover/installed.json';installed.parent.mkdir(parents=True)
            predecessor=json.loads((scripts/'discord_format_release_plan.json').read_text())
            successor=json.loads((scripts/'storage_limit_release_plan.json').read_text())
            installed.write_text(json.dumps({'release_hash':_hash(predecessor['new'])}))
            with patch('scripts.paper_cloud_update.update_installed',return_value={'status':'updated'}) as update, \
                    patch('scripts.paper_cloud_service.install',return_value={'status':'installed'}) as install:
                self.assertEqual(install_default(project)['status'],'installed')
            backup=json.loads((scripts/'backup_restart_release_plan.json').read_text())
            lot1=json.loads((scripts/'lot1_release_plan.json').read_text())
            self.assertEqual([call.args[1] for call in update.call_args_list],[successor,backup,lot1])
            self.assertEqual(install.call_args.args[1],lot1)

    async def test_installed_backup_restart_release_advances_only_to_lot1(self):
        # Production case (LAT-120): the backup restart release is installed;
        # only the lot 1 step runs, then the checkout is installed.
        from hermes_trading.reflection_supervision import _hash
        from shutil import copyfile
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);scripts=project/'scripts';scripts.mkdir()
            source=Path(__file__).resolve().parents[1]/'scripts'
            for name in ('cloud_release_plan.json','discord_format_release_plan.json','storage_limit_release_plan.json',
                         'backup_restart_release_plan.json','lot1_release_plan.json','entry_history_release_plan.json'):
                copyfile(source/name,scripts/name)
            installed=project/'state/.paper-cloud-cutover/installed.json';installed.parent.mkdir(parents=True)
            backup=json.loads((scripts/'backup_restart_release_plan.json').read_text())
            lot1=json.loads((scripts/'lot1_release_plan.json').read_text())
            installed.write_text(json.dumps({'release_hash':_hash(backup['new'])}))
            with patch('scripts.paper_cloud_update.update_installed',return_value={'status':'updated'}) as update, \
                    patch('scripts.paper_cloud_service.install',return_value={'status':'installed'}) as install:
                self.assertEqual(install_default(project)['status'],'installed')
            self.assertEqual([call.args[1] for call in update.call_args_list],[lot1])
            self.assertEqual(install.call_args.args[1],lot1)

    async def test_expired_supervision_does_not_prevent_protective_worker_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);launch=project/'launch.json'
            launch.write_text(json.dumps({'expires_at':'2026-01-01T00:00:00+00:00'}))
            ran=[]
            async def worker(root):ran.append(root)
            install=Mock(return_value={'status':'already_installed','launch_file':str(launch)})
            with patch('scripts.paper_cloud_service._run_bounded') as child:
                await serve_cloud(project,installer=install,worker=worker,stream=io.StringIO())
            self.assertEqual(ran,[project/'state'])
            child.assert_not_called()

    async def test_worker_still_runs_while_the_cloud_login_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);stream=io.StringIO();children=[]
            def spawn(command,**kwargs):
                child=Mock();child.poll.return_value=None;children.append((command,child));return child
            async def worker(root):self.assertEqual(root,project/'state');return {'private':'wallet'}
            install=Mock(return_value={'status':'installed','launch_file':str(project/'state/launch.json')})
            await serve_cloud(project,installer=install,worker=worker,spawn=spawn,stream=stream)
            self.assertEqual(len(children),2)
            self.assertTrue(any('scripts.paper_cloud_auth' in row[0] for row in children))
            self.assertNotIn('private',stream.getvalue());self.assertNotIn('wallet',stream.getvalue())
            for _,child in children:child.terminate.assert_called_once()

    async def test_failed_installation_never_starts_a_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            install=Mock(side_effect=ValueError('private state'));worker=Mock();spawn=Mock()
            with self.assertRaises(ValueError):
                await serve_cloud(Path(tmp),installer=install,worker=worker,spawn=spawn,stream=io.StringIO())
            spawn.assert_not_called();worker.assert_not_called()


if __name__=='__main__':unittest.main()
