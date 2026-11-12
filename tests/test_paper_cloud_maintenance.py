import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from contextlib import redirect_stdout
from scripts import paper_cloud_maintenance as maintenance


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_failure_pauses_supervision_and_still_starts_protective_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            with patch.object(maintenance,'maintain_storage',side_effect=ValueError('PRIVATE_VALUES')), \
                    patch.object(maintenance,'serve_cloud',new_callable=AsyncMock) as serve,redirect_stdout(io.StringIO()) as output:
                await maintenance.run(project)
            serve.assert_awaited_once_with(project)
            self.assertEqual(json.loads((project/'state/watcher_maintenance.json').read_bytes()),maintenance.MARKER)
            self.assertNotIn('PRIVATE_VALUES',output.getvalue())

    def test_marker_does_not_overwrite_or_remove_another_operator_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();path=root/'watcher_maintenance.json';original=b'{"operator":"another pause"}'
            path.write_bytes(original);maintenance.marker(root,enabled=True);maintenance.marker(root,enabled=False)
            self.assertEqual(path.read_bytes(),original)

    def test_owned_marker_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();maintenance.marker(root,enabled=True);maintenance.marker(root,enabled=False)
            self.assertFalse((root/'watcher_maintenance.json').exists())
