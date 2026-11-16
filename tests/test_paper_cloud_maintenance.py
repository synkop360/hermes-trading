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
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        digest=lambda runtime:hashlib.sha256(json.dumps(runtime,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        previous=json.loads((scripts/'discord_format_release_plan.json').read_text())['new']['runtime']
        pilot=json.loads((scripts/'pilot_release_plan.json').read_text())['new']['runtime']
        lot1=json.loads((scripts/'lot1_release_plan.json').read_text())
        self.assertEqual(digest(previous),maintenance.PREVIOUS_RUNTIME)
        self.assertEqual(digest(pilot),maintenance.PILOT_RUNTIME)
        self.assertEqual(digest(lot1['old']['runtime']),maintenance.PILOT_RUNTIME)
        self.assertEqual(digest(lot1['new']['runtime']),maintenance.CURRENT_RUNTIME)
        from hermes_trading.paper_publication import _release_hashes
        self.assertEqual(digest(_release_hashes()),maintenance.CURRENT_RUNTIME)
        tags={'after-legacy-archive',maintenance.PREVIOUS_RECOVERY_TAG,maintenance.PILOT_RECOVERY_TAG,maintenance.RECOVERY_TAG}
        self.assertEqual(len(tags),4)
        approvals={maintenance.PREVIOUS_RECOVERY_APPROVAL,maintenance.PILOT_RECOVERY_APPROVAL,maintenance.RECOVERY_APPROVAL}
        self.assertEqual(len(approvals),3)

    def maintain(self,project,previous,pilot='absent'):
        from unittest.mock import MagicMock
        (project/'state/storage_limits.json').write_text('{}')
        lease=MagicMock();lease.__enter__.return_value.execute.return_value.fetchone.return_value=('{"storage_stop":false}',)
        ready={'status':'ready','reason':None,'state_bytes':1,'free_bytes':2}
        with patch.object(maintenance,'install_default'),patch.object(maintenance,'compact_history',return_value={}), \
                patch.object(maintenance,'repack_archives',return_value={}),patch.object(maintenance,'inspect_storage',return_value=ready), \
                patch.object(maintenance,'recovery_status',side_effect=[previous,pilot]) as status, \
                patch.object(maintenance,'recover_storage',return_value={}) as recover,patch.object(maintenance,'ownership',return_value=lease), \
                redirect_stdout(io.StringIO()):
            result=maintenance.maintain_storage(project)
        from unittest.mock import call
        self.assertEqual(status.call_args_list,[
            call(project/'state',approval_ref=maintenance.PREVIOUS_RECOVERY_APPROVAL,
                source_runtime=maintenance.PREVIOUS_RUNTIME,recovery_tag=maintenance.PREVIOUS_RECOVERY_TAG),
            call(project/'state',approval_ref=maintenance.PILOT_RECOVERY_APPROVAL,
                source_runtime=maintenance.PILOT_RUNTIME,recovery_tag=maintenance.PILOT_RECOVERY_TAG)])
        return result,recover

    def test_completed_pilot_recovery_is_verified_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            result,recover=self.maintain(project,'absent','completed')
            self.assertEqual(result['status'],'completed')
            recover.assert_not_called()

    def test_two_completed_predecessor_recoveries_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            with self.assertRaisesRegex(ValueError,'recovered twice'):self.maintain(project,'completed','completed')

    def test_completed_predecessor_recovery_is_verified_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            result,recover=self.maintain(project,'completed')
            self.assertEqual(result['status'],'completed')
            recover.assert_not_called()

    def test_completed_predecessor_with_successor_audit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            (project/'state'/('.paper-storage-recovery-'+maintenance.RECOVERY_TAG)).mkdir()
            with self.assertRaisesRegex(ValueError,'recovered twice'):self.maintain(project,'completed')

    def test_absent_predecessor_recovery_runs_under_the_lot1_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            result,recover=self.maintain(project,'absent')
            self.assertEqual(result['status'],'completed')
            recover.assert_called_once_with(project/'state',approval_ref=maintenance.RECOVERY_APPROVAL,
                expected_runtime=maintenance.CURRENT_RUNTIME,recovery_tag=maintenance.RECOVERY_TAG)

    def test_maintenance_never_replays_the_pinned_legacy_database_step(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp).resolve();(project/'state').mkdir()
            (project/'state/storage_limits.json').write_text('{}')
            lease=MagicMock();lease.__enter__.return_value.execute.return_value.fetchone.return_value=('{"storage_stop":false}',)
            ready={'status':'ready','reason':None,'state_bytes':1,'free_bytes':2}
            with patch.object(maintenance,'install_default'),patch.object(maintenance,'compact_history',return_value={}) as compact, \
                    patch.object(maintenance,'repack_archives',return_value={}),patch.object(maintenance,'inspect_storage',return_value=ready), \
                    patch.object(maintenance,'recovery_status',return_value='absent'), \
                    patch.object(maintenance,'recover_storage',return_value={}) as recover,patch.object(maintenance,'ownership',return_value=lease), \
                    redirect_stdout(io.StringIO()):
                result=maintenance.maintain_storage(project)
            self.assertEqual(result['status'],'completed')
            compact.assert_called_once_with(project/'state',enabled=True,retire_originals=True)
            recover.assert_called_once_with(project/'state',approval_ref=maintenance.RECOVERY_APPROVAL,
                expected_runtime=maintenance.CURRENT_RUNTIME,recovery_tag=maintenance.RECOVERY_TAG)
