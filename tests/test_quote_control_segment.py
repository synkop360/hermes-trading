"""Original quote-control prefix inclusion in a later recorded cohort."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import asyncio
import hashlib
import importlib.util
import inspect
import json
import secrets
import shutil
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hermes_trading import archive_journal as journal, archive_quote_state as state, archive_store as store
from hermes_trading.archive_capacity import CapacityExceeded, StorageDomain
from hermes_trading.archive_coordinator import capture
from hermes_trading.archive_quote_retention import retain_quote_outcomes
from hermes_trading.archive_snapshot import ownership, snapshot
from hermes_trading.control_auth import sign_command
from hermes_trading.paired_worker import _stats
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash, _json
from venue_fixtures import venue_record
import test_paired_primary_archive as native


class QuoteControlSegmentAvailabilityTests(unittest.TestCase):
    def test_original_control_prefix_reader_is_available(self):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.archive_quote_segment'),
                             'original quote-control segment reader is not implemented')

    def test_archived_controls_are_admitted_before_native_cohort_validation(self):
        from hermes_trading.archive_quote_retention import _verify_archive
        self.assertIn('preflight', inspect.signature(_verify_archive).parameters,
                      'archive verification needs bounded metadata admission before native validation')
        with tempfile.TemporaryDirectory() as temporary, closing(sqlite3.connect(':memory:')) as db:
            root = Path(temporary)
            (root / 'adaptive_200').mkdir()
            (root / 'adaptive_200/wallet_account.json').write_text('{"experiment_id":"test"}')
            manifest = {'profiles': [{'directory': 'adaptive_200'}]}
            request = {'operation_id': 'a'*32, 'archive_sha256': 'b'*64, 'experiment_id': 'test'}
            seen = []

            def reject(original):
                seen.append(original)
                raise ValueError('archived aggregate control admission rejected')

            with patch('hermes_trading.archive_quote_retention.journal._checkpoint', return_value=None), \
                 patch('hermes_trading.archive_quote_retention.journal._read_journal',
                       return_value={'pending': False, 'cohort_sha256': _hash(manifest)}), \
                 patch('hermes_trading.archive_quote_retention._requests', return_value=[request]), \
                 patch('hermes_trading.archive_quote_retention._chain', return_value=[('b'*64, {})]), \
                 patch('hermes_trading.archive_quote_retention._restore'), \
                 patch('hermes_trading.archive_quote_retention._cohort',
                       side_effect=AssertionError('native validation preceded archived control admission')):
                with self.assertRaisesRegex(ValueError, 'archived aggregate control admission rejected'):
                    _verify_archive(db, root, manifest, 'a'*32, root/'archive', root/'cache',
                                    SimpleNamespace(check=lambda: None), None, preflight=reject)
            self.assertEqual(seen, [root/'cache/cohort/worker'])


class QuoteControlSegmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = unittest.TestCase()
        cls.addClassCleanup(cls.fixtures.doCleanups)

        async def build():
            f, selected = native.fresh_selected(cls.fixtures, 200, fees=True)
            f.limits['max_cycles'] = 8
            (f.root / 'worker_limits.json').write_text(json.dumps(f.limits))

            async def collect(assets):
                quote = replace(f.snapshot, price=dict(f.snapshot.price, received_at=f.at.isoformat()),
                                venue_rules=venue_record(f.snapshot.asset, f.at))
                return {quote.asset: quote}

            f.market.collect.side_effect = collect

            async def advance_to(count):
                async def pause(seconds):
                    await f.sleep(seconds)
                    if _stats(f.primary)['complete'] == count:
                        raise RuntimeError('invented completed-cut pause')
                with cls.fixtures.assertRaisesRegex(RuntimeError, 'completed-cut pause'):
                    await f.invoke(sleep=pause)

            await advance_to(5)
            pc = native.controller_for(cls.fixtures, f, selected)
            tmp = tempfile.TemporaryDirectory()
            cls.fixtures.addCleanup(tmp.cleanup)
            base = Path(tmp.name)
            archive = base / 'archive'
            domain = StorageDomain(base, domain_id='quote-segment-fixture',
                                   limit_bytes=512 * 1024**2, reserve_bytes=1024**2,
                                   extra_roots=(f.root,), enabled=True)
            domain.initialize()

            def archived(label, through, first_batch, parent=None):
                cohort = base / label
                result = capture(pc, cohort, enabled=True,
                    worker_capture=lambda target, **limits: snapshot(f.root, target, enabled=True, **limits),
                    experiment_id='paired-test', expected_release=_release_hashes(), through_sequence=through,
                    max_bytes=64 * 1024**2, timeout_seconds=90, now=f.at)
                inventory = json.loads((cohort / 'snapshot_inventory.json').read_text())
                names = [row['path'] for row in inventory['files']] + ['snapshot_inventory.json']
                digest = store.seal(cohort, archive, names, experiment='paired-test',
                                    first_batch=first_batch, last_batch=result['certificate']['observed_complete'],
                                    parent=parent)
                cert = result['certificate']
                shared = f.primary / 'paper_publications.sqlite'
                request = journal.prepare(shared, enabled=True, experiment_id='paired-test',
                    release_sha256=cert['release_sha256'], archive_sha256=digest,
                    account_snapshots=cert['account_snapshots'], copies={'pc': 'e' * 64, 'backup': 'f' * 64},
                    through_sequence=through)
                keys = {name: secrets.token_hex(32) for name in ('pc', 'backup')}
                body = _json(request)
                receipts = {name: dict(body=body, nonce=request['operation_id'], timestamp=f.at.isoformat(),
                    signature=sign_command(key, request['operation_id'], f.at, body)) for name, key in keys.items()}
                journal.acknowledge(shared, request['operation_id'], receipts, keys, now=f.at, enabled=True)
                journal.retire(shared, request['operation_id'], enabled=True)
                return request

            a = archived('cohort-a', 4, 1)
            with domain.ownership() as owner, patch.object(state, 'MAX_ITEMS', 2):
                q1 = retain_quote_outcomes(f.root, archive_root=archive,
                    archive_operation_id=a['operation_id'], storage_owner=owner, cache_root=base,
                    enabled=True, max_bytes=64 * 1024**2, timeout_seconds=120, now=f.at)
            await advance_to(7)
            b = archived('cohort-b', 6, 6, a['archive_sha256'])
            before_q2 = base / 'before-q2-worker'
            shutil.copytree(f.root, before_q2)
            floors_before = cls.floor_rows(f.root, f.manifest)
            with domain.ownership() as owner, patch.object(state, 'MAX_ITEMS', 2):
                q2 = retain_quote_outcomes(f.root, archive_root=archive,
                    archive_operation_id=a['operation_id'], operation_id=secrets.token_hex(16),
                    storage_owner=owner, cache_root=base, enabled=True,
                    max_bytes=64 * 1024**2, timeout_seconds=120, now=f.at)
            cls.case = dict(worker=f.root, before_q2=before_q2, archive=archive,
                            manifest=f.manifest, a=a, b=b, q1=q1, q2=q2,
                            now=f.at, floors_before=floors_before,
                            floors_after=cls.floor_rows(f.root, f.manifest))

        asyncio.run(build())

    @staticmethod
    def floor_rows(root, manifest):
        result = {}
        for p in manifest['profiles']:
            with closing(sqlite3.connect(root / p['directory'] / 'trading.db')) as db:
                for key, raw in db.execute("SELECT incident_id,reason FROM incidents WHERE incident_id LIKE 'archive:quote-floor:%'"):
                    result[p['directory'], key] = state.unpack(raw)['through']
        return result

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.worker = self.root / 'worker'
        self.archive = self.root / 'archive'
        shutil.copytree(self.case['worker'], self.worker)
        shutil.copytree(self.case['archive'], self.archive)
        self.shared = self.worker / 'adaptive_200/paper_publications.sqlite'
        self.domain = StorageDomain(self.root, domain_id='quote-control-segment',
                                    limit_bytes=512 * 1024**2, reserve_bytes=1024**2, enabled=True)
        self.domain.initialize()
        self.owner = self.enterContext(self.domain.ownership())

    def pin(self, **changes):
        from hermes_trading.archive_quote_segment import quote_control_segment_pin
        options = dict(archive_root=self.archive, archive_operation_id=self.case['b']['operation_id'],
                       from_ordinal=1, through_ordinal=1, storage_owner=self.owner,
                       cache_root=self.root, enabled=True, max_bytes=64 * 1024**2,
                       timeout_seconds=120, now=self.case['now'])
        options.update(changes)
        return quote_control_segment_pin(self.worker, **options)

    @staticmethod
    def inventory(root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}

    def finances(self):
        result = {}
        for p in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(self.worker / p['directory'] / 'trading.db')) as db:
                for table in ('positions', 'order_intents', 'orders', 'order_fills'):
                    result[p['directory'], table] = list(db.execute('SELECT rowid,* FROM ' + table + ' ORDER BY rowid'))
        return result

    def test_native_fixture_has_two_completed_retention_chunks_and_advanced_current_floors(self):
        self.assertEqual(self.case['q1']['retired_outcomes'], 2)
        self.assertEqual(self.case['q2']['retired_outcomes'], 2)
        self.assertNotEqual(self.case['q1']['operation_id'], self.case['q2']['operation_id'])
        self.assertTrue(any(self.case['floors_after'].get(key, before) > before
                            for key, before in self.case['floors_before'].items()))
        with closing(sqlite3.connect(self.shared)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paired_quote_retention').fetchone()[0], 2)

    def test_later_recorded_cohort_proves_prefix_with_stable_pin_after_newer_floor_updates(self):
        current_finances = self.finances()
        before_worker, before_archive = self.inventory(self.worker), self.inventory(self.archive)
        after = self.pin()
        prior_worker = self.root / 'prior-worker'
        shutil.copytree(self.case['before_q2'], prior_worker)
        from hermes_trading.archive_quote_segment import quote_control_segment_pin
        before = quote_control_segment_pin(prior_worker, archive_root=self.archive,
            archive_operation_id=self.case['b']['operation_id'], from_ordinal=1, through_ordinal=1,
            storage_owner=self.owner, cache_root=self.root, enabled=True,
            max_bytes=64 * 1024**2, timeout_seconds=120, now=self.case['now'])
        self.assertEqual(after['pin'], before['pin'])
        self.assertEqual(after['pin_sha256'], before['pin_sha256'])
        self.assertEqual(after['pin']['purpose'], 'paired_quote_control_prefix_v1')
        self.assertEqual(after['pin']['from_ordinal'], 1)
        self.assertEqual(after['pin']['through_ordinal'], 1)
        self.assertEqual(after['pin']['archive_sha256'], self.case['b']['archive_sha256'])
        self.assertEqual(set(after['pin']['accounts']), journal.ACCOUNTS)
        expected = hashlib.sha256(json.dumps(after['pin'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.assertEqual(after['pin_sha256'], expected)
        self.assertFalse(after['compaction_authorized'])
        self.assertFalse(after['activated'])
        self.assertEqual(current_finances, self.finances())
        self.assertEqual(before_worker, self.inventory(self.worker))
        self.assertEqual(before_archive, self.inventory(self.archive))

    def test_original_bundle_archive_cannot_prove_control_records_created_later(self):
        before = self.inventory(self.worker), self.inventory(self.archive)
        with self.assertRaises((ValueError, sqlite3.Error)):
            self.pin(archive_operation_id=self.case['a']['operation_id'])
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_current_raw_plan_and_progress_whitespace_changes_are_rejected(self):
        for column in ('plan', 'progress'):
            with self.subTest(column=column):
                with closing(sqlite3.connect(self.shared)) as db:
                    raw = db.execute('SELECT ' + column + ' FROM paired_quote_retention WHERE rowid=1').fetchone()[0]
                    equivalent = json.dumps(json.loads(raw), indent=2)
                    self.assertNotEqual(equivalent, raw)
                    db.execute('UPDATE paired_quote_retention SET ' + column + '=? WHERE rowid=1', (equivalent,))
                    db.commit()
                with self.assertRaises((ValueError, sqlite3.Error)):
                    self.pin()
                with closing(sqlite3.connect(self.shared)) as db:
                    db.execute('UPDATE paired_quote_retention SET ' + column + '=? WHERE rowid=1', (raw,))
                    db.commit()

    def test_current_raw_zero_target_account_receipt_whitespace_change_is_rejected(self):
        path = self.worker / 'control_200/trading.db'
        with closing(sqlite3.connect(path)) as db:
            key = state.RECEIPT + self.case['q1']['operation_id']
            raw = db.execute('SELECT reason FROM incidents WHERE incident_id=?', (key,)).fetchone()[0]
            db.execute('UPDATE incidents SET reason=? WHERE incident_id=?', (json.dumps(json.loads(raw), indent=2), key))
            db.commit()
        before = self.inventory(self.worker), self.inventory(self.archive)
        with self.assertRaises((ValueError, sqlite3.Error)):
            self.pin()
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_selected_original_control_rowid_change_is_rejected_without_mutation(self):
        with closing(sqlite3.connect(self.shared)) as db:
            db.execute('UPDATE paired_quote_retention SET rowid=3 WHERE rowid=1')
            db.commit()
        before = self.inventory(self.worker), self.inventory(self.archive)
        # Both the cohort gate and original-prefix reader reject this gap.
        # The contract is rejection without mutation, independent of which
        # validator first detects the invalid ordinal.
        with self.assertRaises(ValueError):
            self.pin()
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_corrupt_referenced_b_object_is_rejected_without_extra_mutations(self):
        head = self.archive / 'manifests' / (self.case['b']['archive_sha256'] + '.json')
        manifest = json.loads(head.read_text())
        member = next(row for row in manifest['files']
                      if row['path'] == 'worker/adaptive_200/paper_publications.sqlite')
        path = self.archive / 'objects' / member['objects'][0]
        raw = path.read_bytes()
        self.assertTrue(raw)
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        before = self.inventory(self.worker), self.inventory(self.archive)
        with self.assertRaises(ValueError):
            self.pin()
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_existing_source_sidecars_are_preserved_and_rejected_before_sqlite_lease(self):
        paths = (self.worker / 'worker_runtime.sqlite', self.shared,
                 self.worker / 'control_200/trading.db')
        for path in paths:
            for suffix in ('-wal', '-shm', '-journal'):
                with self.subTest(database=path.name, directory=path.parent.name, suffix=suffix):
                    sidecar = path.with_name(path.name + suffix)
                    self.assertFalse(sidecar.exists())
                    sidecar.write_bytes(b'existing source sidecar evidence')
                    try:
                        before = self.inventory(self.worker), self.inventory(self.archive)
                        with patch('hermes_trading.archive_quote_segment._exclusive',
                                   side_effect=AssertionError('SQLite lease preceded source sidecar rejection')):
                            with self.assertRaisesRegex(ValueError, 'SQLite sidecars'):
                                self.pin()
                        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))
                    finally:
                        sidecar.unlink()

    def test_competing_worker_lease_blocks_without_source_mutation(self):
        before = self.inventory(self.worker), self.inventory(self.archive)
        with ownership(self.worker, role='worker', enabled=True):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                self.pin()
            self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_deadline_expiry_after_reading_recorded_b_manifest_preserves_sources(self):
        elapsed = [0]
        head = self.archive / 'manifests' / (self.case['b']['archive_sha256'] + '.json')
        original_read = store._read

        def expire_after_manifest(path, limit):
            raw = original_read(path, limit)
            if path == head:
                elapsed[0] = 121
            return raw

        before = self.inventory(self.worker), self.inventory(self.archive)
        with patch.object(self.domain, 'clock', lambda: time.monotonic() + elapsed[0]), \
             patch.object(store, '_read', side_effect=expire_after_manifest):
            with self.assertRaisesRegex(TimeoutError, 'absolute deadline exceeded'):
                self.pin()
        self.assertEqual(elapsed[0], 121, 'the recorded B manifest was actually read')
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_peak_above_domain_capacity_is_rejected_before_sqlite_lease(self):
        before = self.inventory(self.worker), self.inventory(self.archive)
        with patch('hermes_trading.archive_quote_segment._exclusive',
                   side_effect=AssertionError('SQLite lease preceded peak admission')):
            with self.assertRaises(CapacityExceeded):
                self.pin(max_bytes=self.domain.limit_bytes)
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_disabled_invalid_ranges_and_tiny_budget_leave_sources_unchanged(self):
        for changes in ({'enabled': False}, {'from_ordinal': 0}, {'through_ordinal': 0},
                        {'from_ordinal': 2, 'through_ordinal': 1}, {'through_ordinal': 3},
                        {'max_bytes': 4096}, {'timeout_seconds': 10**1000}):
            with self.subTest(changes=changes):
                before = self.inventory(self.worker), self.inventory(self.archive)
                with self.assertRaises((ValueError, OSError, sqlite3.Error, TimeoutError)):
                    self.pin(**changes)
                self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_archive_outage_cannot_issue_pin_or_change_financial_state(self):
        before_worker, before_finances = self.inventory(self.worker), self.finances()
        missing = self.root / 'missing-archive'
        with self.assertRaises((ValueError, OSError, sqlite3.Error)):
            self.pin(archive_root=missing)
        self.assertEqual(before_worker, self.inventory(self.worker))
        self.assertEqual(before_finances, self.finances())

    def test_aggregate_current_plan_bytes_are_rejected_before_archive_restoration(self):
        with closing(sqlite3.connect(self.shared)) as db:
            for column in ('plan', 'progress'):
                raw = db.execute('SELECT ' + column + ' FROM paired_quote_retention WHERE rowid=1').fetchone()[0]
                db.execute('UPDATE paired_quote_retention SET ' + column + '=? WHERE rowid=1',
                           (raw + ' ' * (2 * 1024**2),))
            db.commit()
        before = self.inventory(self.worker), self.inventory(self.archive)
        with patch('hermes_trading.archive_quote_segment._verify_archive',
                   side_effect=AssertionError('archive restoration started before aggregate control admission')):
            with self.assertRaises((ValueError, sqlite3.Error)):
                self.pin()
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_aggregate_eight_account_receipt_bytes_are_rejected_before_archive_restoration(self):
        for p in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(self.worker / p['directory'] / 'trading.db')) as db:
                key = state.RECEIPT + self.case['q1']['operation_id']
                raw = db.execute('SELECT reason FROM incidents WHERE incident_id=?', (key,)).fetchone()[0]
                db.execute('UPDATE incidents SET reason=? WHERE incident_id=?',
                           (raw + ' ' * (600 * 1024), key))
                db.commit()
        before = self.inventory(self.worker), self.inventory(self.archive)
        with patch('hermes_trading.archive_quote_segment._verify_archive',
                   side_effect=AssertionError('archive restoration started before aggregate receipt admission')):
            with self.assertRaises((ValueError, sqlite3.Error)):
                self.pin()
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def assert_wal_input_rejected_without_mutation(self, path):
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            db.commit()
        self.assertFalse(path.with_name(path.name + '-wal').exists())
        before = self.inventory(self.worker), self.inventory(self.archive)
        with self.assertRaises((ValueError, sqlite3.Error)):
            self.pin()
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_wal_worker_owner_is_rejected_without_changing_its_header(self):
        self.assert_wal_input_rejected_without_mutation(self.worker / 'worker_runtime.sqlite')

    def test_wal_shared_journal_is_rejected_without_changing_its_header(self):
        self.assert_wal_input_rejected_without_mutation(self.shared)


if __name__ == '__main__':
    unittest.main()
