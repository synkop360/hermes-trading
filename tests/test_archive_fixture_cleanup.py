"""Real reused async fixtures must release their own directories explicitly."""
import gc
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ReusedFixtureCleanupTests(unittest.TestCase):
    def test_nested_async_fixtures_release_owned_roots_before_garbage_collection(self):
        owners = (
            ('test_archive_protective_session', 'ProtectiveSessionTests'),
            ('test_archive_protective_worker', 'ProtectiveWorkerTests'),
            ('test_archive_protective_transport', 'AuthenticatedProtectiveSourceTests'),
            ('test_archive_receiver', 'CachedAuthenticatedWorkerTests'),
            ('test_archive_witness_transport', 'AuthenticatedNativeWorkerTests'),
            ('test_archive_witness_socket', 'WireNativeWorkerTests'),
        )
        original = tempfile.TemporaryDirectory
        for module_name, class_name in owners:
            with self.subTest(owner=class_name):
                allocated = []
                def owned_directory(*args, **kwargs):
                    result = original(*args, **kwargs)
                    allocated.append(result)
                    # The regression itself owns fallback cleanup even when the
                    # nested fixture's original cleanup registration is broken.
                    self.addCleanup(result.cleanup)
                    return result
                case_type = getattr(importlib.import_module(module_name), class_name)
                method = unittest.defaultTestLoader.getTestCaseNames(case_type)[0]
                result = unittest.TestResult()
                with patch.object(tempfile, 'TemporaryDirectory', owned_directory):
                    case_type(method).run(result)
                self.assertTrue(result.wasSuccessful(), (result.errors, result.failures))
                remaining = [str(item.name) for item in allocated if Path(item.name).exists()]
                self.assertEqual(remaining, [], 'fixture delegated cleanup to an uninitialized async runner')
                gc.collect()
