"""Whole-domain admission behavior; synthetic state, no strategy evaluation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hermes_trading import archive_capacity as capacity


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'state'
        self.root.mkdir()

    def domain(self, **kwargs):
        options = dict(domain_id='synthetic-pc', limit_bytes=512 * 1024,
                       reserve_bytes=64 * 1024, enabled=True)
        options.update(kwargs)
        return capacity.StorageDomain(self.root, **options)

    def initialized(self, **kwargs):
        domain = self.domain(**kwargs)
        domain.initialize()
        return domain

    def child(self, source):
        return subprocess.run([sys.executable, '-B', '-c', source, str(self.root)],
                              cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, timeout=10)

    def test_complete_inventory_counts_eight_accounts_legacy_sidecars_and_incomplete_stages(self):
        extra = self.base / 'archive'
        extra.mkdir()
        for index in range(8):
            account = self.root / ('account' + str(index))
            account.mkdir()
            (account / 'account.sqlite').write_bytes(b'a' * 11)
            (account / 'costs.jsonl').write_bytes(b'costs')
        for name, size in [('legacy.sqlite', 17), ('runtime.sqlite', 19),
                           ('receiver.sqlite', 23), ('receiver.sqlite-journal', 29),
                           ('nonce.sqlite', 31), ('nonce.sqlite-wal', 37), ('nonce.sqlite-shm', 41)]:
            (self.root / name).write_bytes(b'x' * size)
        stage = extra / 'incomplete-export'
        stage.mkdir()
        (stage / 'partial.zip').write_bytes(b'z' * 43)
        (extra / 'retained.zip').write_bytes(b'z' * 47)
        domain = self.initialized(extra_roots=(extra,))
        with domain.admit(100) as lease:
            self.assertEqual(lease.report['state_bytes'], 415 + domain.owner_path.stat().st_size)
            self.assertEqual(lease.report['files'], 26)
            self.assertEqual(lease.report['root_bytes'][str(extra)], 90)

    def test_additional_peak_must_fit_alongside_existing_state_and_protected_reserve(self):
        domain = self.initialized()
        (self.root / 'legacy').write_bytes(b'x' * 5000)
        current = 5000 + domain.owner_path.stat().st_size
        headroom = 512 * 1024 - 64 * 1024 - current
        with domain.admit(headroom) as lease:
            lease.check(remaining_peak_bytes=headroom)
        with self.assertRaises(capacity.CapacityExceeded):
            with domain.admit(headroom + 1):
                (self.root / 'denied-stage').mkdir()
        self.assertFalse((self.root / 'denied-stage').exists())
        self.assertEqual((self.root / 'legacy').read_bytes(), b'x' * 5000)

    def test_free_space_reserves_future_peak_separately_from_logical_limit(self):
        domain = self.initialized()
        with patch.object(capacity.shutil, 'disk_usage', return_value=SimpleNamespace(free=64 * 1024 + 99)):
            with self.assertRaises(capacity.CapacityExceeded):
                with domain.admit(100):
                    self.fail('insufficient free space admitted')
        with patch.object(capacity.shutil, 'disk_usage', side_effect=OSError('unavailable')):
            with self.assertRaises(capacity.CapacityUnavailable):
                with domain.admit(0):
                    self.fail('unknown free space admitted')

    def test_failed_bootstrap_does_not_create_owner_or_stage(self):
        domain = self.domain(limit_bytes=65536, reserve_bytes=65535)
        with self.assertRaises(capacity.CapacityExceeded):
            domain.initialize()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_default_disabled_and_missing_owner_never_initialize_a_request(self):
        for domain in (self.domain(enabled=False), self.domain()):
            with self.subTest(enabled=domain.enabled), self.assertRaises((ValueError, capacity.CapacityUnavailable)):
                with domain.admit(1):
                    self.fail('disabled or uninitialized request admitted')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_two_processes_cannot_spend_the_same_headroom_and_growth_survives_release(self):
        domain = self.initialized(limit_bytes=200000, reserve_bytes=10000)
        source = '''import sys
from hermes_trading.archive_capacity import StorageDomain,CapacityBusy,CapacityExceeded
d=StorageDomain(sys.argv[1],domain_id='synthetic-pc',limit_bytes=200000,reserve_bytes=10000,enabled=True)
try:
    with d.admit(100000):pass
except CapacityBusy:print('busy')
except CapacityExceeded:print('exceeded')
else:print('admitted')
'''
        with domain.admit(100000):
            result = self.child(source)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, 'busy'), result.stderr)
            (self.root / 'accepted-growth').write_bytes(b'x' * 100000)
        result = self.child(source)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'exceeded'), result.stderr)
        self.assertEqual((self.root / 'accepted-growth').stat().st_size, 100000)

    def test_process_crash_releases_lock_without_reinitializing_original_policy(self):
        domain = self.initialized()
        original = domain.owner_path.read_bytes()
        result = self.child('''import os,sys
from hermes_trading.archive_capacity import StorageDomain
d=StorageDomain(sys.argv[1],domain_id='synthetic-pc',limit_bytes=524288,reserve_bytes=65536,enabled=True)
with d.admit(100):os._exit(23)
''')
        self.assertEqual(result.returncode, 23, result.stderr)
        with domain.admit(100) as lease:
            lease.check()
        self.assertEqual(domain.owner_path.read_bytes(), original)

    def test_policy_changes_and_corrupt_owner_fail_without_rewriting_evidence(self):
        domain = self.initialized()
        original = domain.owner_path.read_bytes()
        changed = self.domain(limit_bytes=600000)
        with self.assertRaises(ValueError):
            with changed.admit(0):
                self.fail('different policy borrowed owner')
        self.assertEqual(domain.owner_path.read_bytes(), original)
        domain.owner_path.write_bytes(original[:-1] + b'!')
        corrupted = domain.owner_path.read_bytes()
        with self.assertRaises(ValueError):
            with domain.admit(0):
                self.fail('corrupt owner admitted')
        self.assertEqual(domain.owner_path.read_bytes(), corrupted)

    def test_leases_only_admit_registered_paths_and_do_not_create_them(self):
        domain = self.initialized()
        stage = self.root / 'staging' / 'future.zip'
        with domain.admit(100) as lease:
            self.assertEqual(lease.require_path(stage), stage)
            with self.assertRaises(ValueError):
                lease.require_path(self.base / 'unregistered' / 'future.zip')
            with self.assertRaises(ValueError):
                lease.require_path(self.root / '..' / 'state' / 'future.zip')
        self.assertFalse(stage.parent.exists())

    def test_released_or_foreign_thread_lease_cannot_be_reused(self):
        domain = self.initialized()
        errors = []
        with domain.admit(100) as lease:
            def use_foreign():
                try:
                    lease.require_path(self.root / 'bad')
                except ValueError as error:
                    errors.append(error)
            thread = threading.Thread(target=use_foreign)
            thread.start()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            lease.check()
        with self.assertRaises(ValueError):
            lease.check()

    def test_expired_lease_blocks_further_admission_work(self):
        current = [0.0]
        domain = self.initialized(clock=lambda: current[0])
        with self.assertRaises(capacity.CapacityUnavailable):
            with domain.admit(100, timeout_seconds=1) as lease:
                current[0] = 1.0
                lease.require_path(self.root / 'late')
        self.assertFalse((self.root / 'late').exists())
        current[0] = 2.0
        with domain.admit(0):
            pass

    def test_actual_growth_and_remaining_peak_cannot_exceed_original_envelope(self):
        domain = self.initialized()
        with domain.admit(100) as lease:
            (self.root / 'new').write_bytes(b'x' * 60)
            lease.check(remaining_peak_bytes=40)
            with self.assertRaises(capacity.CapacityExceeded):
                lease.check(remaining_peak_bytes=41)
        with self.assertRaises(capacity.CapacityExceeded):
            with domain.admit(10):
                (self.root / 'too-large').write_bytes(b'x' * 11)
        self.assertEqual((self.root / 'too-large').stat().st_size, 11)

    def test_linked_files_and_overlapping_inventory_are_rejected(self):
        nested = self.root / 'nested'
        nested.mkdir()
        with self.assertRaises(ValueError):
            self.domain(extra_roots=(nested,))
        original = self.root / 'original'
        original.write_bytes(b'preserve')
        linked = self.root / 'hard-linked'
        try:
            os.link(original, linked)
        except OSError as error:
            self.skipTest('hard links unavailable: ' + str(error))
        with self.assertRaises(ValueError):
            self.domain().initialize()
        self.assertFalse(self.domain().owner_path.exists())

    def test_added_symlink_or_reparse_storage_blocks_before_stage(self):
        domain = self.initialized()
        outside = self.base / 'outside'
        outside.mkdir()
        try:
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest('symlink creation unavailable: ' + str(error))
        with self.assertRaises(ValueError):
            with domain.admit(1):
                (self.root / 'bad-stage').mkdir()
        self.assertFalse((self.root / 'bad-stage').exists())

    def test_empty_directories_also_consume_bounded_scan_entries(self):
        domain = self.initialized(max_entries=4)
        for index in range(5):
            (self.root / str(index)).mkdir()
        with self.assertRaises(capacity.CapacityUnavailable):
            with domain.admit(0):
                self.fail('unbounded directory inventory admitted')

    def test_one_root_free_space_cannot_be_offset_by_another(self):
        extra = self.base / 'archive'
        extra.mkdir()
        domain = self.initialized(extra_roots=(extra,))
        def free(path):
            return SimpleNamespace(free=1 if Path(path) == self.root else 10**12)
        with patch.object(capacity.shutil, 'disk_usage', side_effect=free):
            with self.assertRaises(capacity.CapacityExceeded):
                with domain.admit(100):
                    self.fail('extra root free space offset an exhausted root')

    def test_invalid_bounds_are_rejected_before_bootstrap(self):
        for change in ({'reserve_bytes': True}, {'limit_bytes': 0},
                       {'limit_bytes': 10, 'reserve_bytes': 10}, {'max_entries': 0},
                       {'domain_id': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.domain(**change)
        domain = self.initialized()
        for peak, timeout in ((True, 1), (-1, 1), (0, float('inf')), (0, 11)):
            with self.subTest(peak=peak, timeout=timeout), self.assertRaises(ValueError):
                with domain.admit(peak, timeout_seconds=timeout):
                    pass

    def test_retained_domain_owner_precedes_real_prepared_receiver_and_survives_requests(self):
        import test_archive_protective_witness as kernel
        from hermes_trading.archive_protective_receiver import ProtectiveReceiver
        fixture = kernel.ProtectiveWitnessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        domain = capacity.StorageDomain(fixture.path.parent, domain_id='prepared-synthetic',
                                        limit_bytes=4 * 1024**2, reserve_bytes=64 * 1024, enabled=True)
        domain.initialize()
        with domain.ownership() as owner:
            receiver = ProtectiveReceiver(fixture.path, fixture.binding, fixture.identity,
                                          enabled=True, max_bytes=1024**2)
            try:
                with owner.admit(1024**2, timeout_seconds=120, timeout_ceiling_seconds=120) as lease:
                    lease.require_path(fixture.path)
                    original = receiver.prepare(timeout_seconds=120)
                for _ in range(2):
                    with owner.admit(1024**2) as lease:
                        self.assertIs(lease.owner, owner)
                        self.assertEqual(receiver.read(), original)
                with self.assertRaises(capacity.CapacityBusy):
                    with domain.admit(1):
                        self.fail('prepared owner released between requests')
            finally:
                receiver.close()
        with domain.admit(1):
            pass

    def test_retained_owner_blocks_nested_spending_and_remains_after_denied_subadmission(self):
        domain = self.initialized()
        with domain.ownership() as owner:
            with self.assertRaises(capacity.CapacityExceeded):
                with owner.admit(512 * 1024):
                    self.fail('oversized subadmission accepted')
            with owner.admit(100) as lease:
                initial_bytes = lease.report['state_bytes']
                with self.assertRaises(capacity.CapacityBusy):
                    with owner.admit(100):
                        self.fail('nested reservation double-spent capacity')
                (self.root / 'first').write_bytes(b'x' * 50)
            with owner.admit(100) as next_lease:
                self.assertEqual(next_lease.report['state_bytes'], initial_bytes + 50)
        with self.assertRaises(ValueError):
            with owner.admit(1):
                self.fail('released retained owner reused')

    def test_admitted_policy_limits_cannot_be_changed_without_rebinding_original_record(self):
        domain = self.initialized()
        for name, value in [('limit_bytes', 1024**4), ('reserve_bytes', 0),
                            ('roots', (self.root,)), ('max_entries', 10**9)]:
            with self.subTest(name=name), self.assertRaises(AttributeError):
                setattr(domain, name, value)
