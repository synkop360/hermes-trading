import asyncio
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock
import unittest
from unittest.mock import patch

from scripts.paper_cloud_service import serve_cloud


class CloudServiceTests(unittest.IsolatedAsyncioTestCase):
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
