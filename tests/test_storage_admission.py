import tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from hermes_trading.paired_storage_admission import validate_storage_limits,inspect_storage

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.limits={'schema_version':1,'experiment_id':'test','max_state_bytes':512*1024*1024,'reserve_bytes':128*1024*1024}
    def tearDown(self):self.tmp.cleanup()
    def test_known_sizes_and_recovery_margin(self):
        (self.root/'one').write_bytes(b'12345')
        with patch('hermes_trading.paired_storage_admission.shutil.disk_usage',return_value=SimpleNamespace(free=256*1024*1024)):
            result=inspect_storage(self.root,self.limits)
        self.assertEqual(result['state_bytes'],5);self.assertEqual(result['status'],'ready')
    def test_low_free_space_blocks_and_unknown_measurement_blocks(self):
        with patch('hermes_trading.paired_storage_admission.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
            self.assertEqual(inspect_storage(self.root,self.limits)['status'],'blocked')
        with patch('hermes_trading.paired_storage_admission.shutil.disk_usage',side_effect=OSError('unavailable')):
            self.assertEqual(inspect_storage(self.root,self.limits)['status'],'blocked')
    def test_invalid_identity_numbers_and_reserve_rejected(self):
        for change in ({'experiment_id':'other'},{'reserve_bytes':True},{'reserve_bytes':1},{'max_state_bytes':0},{'schema_version':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_storage_limits(dict(self.limits,**change),'test')

    def test_state_budget_retains_reserve_before_next_write(self):
        (self.root/'one').write_bytes(b'12345')
        limits=dict(self.limits,max_state_bytes=self.limits['reserve_bytes']+5)
        with patch('hermes_trading.paired_storage_admission.shutil.disk_usage',return_value=SimpleNamespace(free=256*1024*1024)):
            self.assertEqual(inspect_storage(self.root,limits)['status'],'blocked')

    def test_junction_or_symlink_attributes_are_rejected(self):
        import stat
        from hermes_trading.paired_storage_admission import _reject_link
        for info in (SimpleNamespace(st_mode=stat.S_IFLNK),SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=1024)):
            with self.assertRaises(ValueError):_reject_link(info)
