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

    def test_second_pause_recovery_is_bound_to_the_installed_runtime_and_a_new_tag(self):
        import hashlib
        runtime=json.loads((Path(__file__).resolve().parents[1]/'scripts/discord_format_release_plan.json').read_text())['new']['runtime']
        self.assertEqual(hashlib.sha256(json.dumps(runtime,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                         maintenance.CURRENT_RUNTIME)
        self.assertNotEqual(maintenance.RECOVERY_TAG,'after-legacy-archive')

    def test_maintenance_never_replays_the_pinned_legacy_database_step(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            (project/'state/storage_limits.json').write_text('{}')
            lease=MagicMock();lease.__enter__.return_value.execute.return_value.fetchone.return_value=('{"storage_stop":false}',)
            ready={'status':'ready','reason':None,'state_bytes':1,'free_bytes':2}
            with patch.object(maintenance,'install_default'),patch.object(maintenance,'compact_history',return_value={}) as compact, \
                    patch.object(maintenance,'repack_archives',return_value={}),patch.object(maintenance,'inspect_storage',return_value=ready), \
                    patch.object(maintenance,'recover_storage',return_value={}) as recover,patch.object(maintenance,'ownership',return_value=lease), \
                    redirect_stdout(io.StringIO()):
                result=maintenance.maintain_storage(project)
            self.assertEqual(result['status'],'completed')
            compact.assert_called_once_with(project/'state',enabled=True,retire_originals=True)
            recover.assert_called_once_with(project/'state',approval_ref=maintenance.RECOVERY_APPROVAL,
                expected_runtime=maintenance.CURRENT_RUNTIME,recovery_tag=maintenance.RECOVERY_TAG)
