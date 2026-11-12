"""The transition inputs must be produced and accepted by native consumers."""
from contextlib import closing
from datetime import timedelta
import hashlib
import json
import sqlite3
import unittest

from hermes_trading.reflection_supervision import _hash
from release_transition_fixtures import NativeTransitionFixture, read_json


class NativeTransitionFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_opening_common_batch_bindings_and_history(self):
        fixture = await NativeTransitionFixture.create(self)
        from hermes_trading.paired_inception import read_paired_inception
        from hermes_trading.paired_packet_storage import load_packet
        from hermes_trading.supervisor_launch import validate_launch
        from hermes_trading.supervisor_sync import make_snapshot, validate_snapshot
        self.assertEqual(read_paired_inception(fixture.worker_root), fixture.opening)
        self.assertEqual(len(fixture.opening['accounts']), 8)
        self.assertEqual(fixture.opening['accounts']['adaptive_200']['net_equity'], '199.99')
        with closing(sqlite3.connect(fixture.primary / 'paper_publications.sqlite')) as db:
            rows = db.execute('SELECT status,payload,completed FROM paired_batches').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 'complete')
            self.assertEqual(load_packet(db, rows[0][1])['release'], fixture.old_release['runtime'])
            self.assertEqual(len(load_packet(db, rows[0][2])), 8)
        with closing(sqlite3.connect(fixture.worker_root / 'worker_runtime.sqlite')) as db:
            lifecycle = json.loads(db.execute('SELECT state FROM lifecycle').fetchone()[0])
        self.assertEqual(lifecycle['completed_batches'], 1)
        self.assertEqual(lifecycle['status'], 'running')
        self.assertEqual(lifecycle['limits']['max_cycles'], 4)
        for name, table in (('model_access_refresh.sqlite', 'renewals'),
                            ('supervisor_usage_feed.sqlite', 'snapshots')):
            with self.subTest(database=name), closing(sqlite3.connect(fixture.primary / name)) as db:
                binding = json.loads(db.execute('SELECT payload FROM binding').fetchone()[0])
                self.assertEqual(binding['release'], _hash(fixture.old_release['runtime']))
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 1)
        with closing(sqlite3.connect(fixture.primary / 'supervisor_usage_feed.sqlite')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
        with closing(sqlite3.connect(fixture.supervisor_root / 'llm_review_state.sqlite')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM reviews').fetchone()[0], 1)
        self.assertEqual(fixture.usage['model_usage']['events'][0]['amount'], '0')
        access = read_json(fixture.primary / 'model_access.json')
        self.assertEqual(access['valid_until'], (fixture.inception_fixture.at + timedelta(minutes=5)).isoformat())
        self.assertEqual(access['provenance_ref'], 'synthetic_reviewed_initial_free_diagnostic')
        self.assertEqual(fixture.expected_identity['manifest_sha256'],
                         hashlib.sha256((fixture.worker_root / 'manifest.json').read_bytes()).hexdigest())
        with fixture.release_context(fixture.old_release):
            launch = validate_launch(fixture.launch_file, fixture.project_root, fixture.old_release)
            self.assertEqual(launch['local_state_dir'], fixture.supervisor_root)
            snapshot = make_snapshot(fixture.primary, now=fixture.at)
            validate_snapshot(snapshot, fixture.policy, now=fixture.at,
                              expected_primary=fixture.selection,
                              expected_account_hash=_hash(fixture.account))
        self.assertEqual(read_json(fixture.primary / 'heartbeat.json')['status'], 'ok')
        for profile in fixture.manifest['profiles']:
            report = read_json(fixture.worker_root / profile['directory'] / 'wallet_metrics.json')
            self.assertEqual(report['status'], 'observed')
            with closing(sqlite3.connect(fixture.worker_root / profile['directory'] / 'wallet_equity.sqlite')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)

    async def test_native_restart_and_new_imports_preserve_original_history(self):
        fixture = await NativeTransitionFixture.create(self)
        from hermes_trading.paired_inception import read_paired_inception
        with closing(sqlite3.connect(fixture.worker_root / 'worker_runtime.sqlite')) as db:
            before = json.loads(db.execute('SELECT state FROM lifecycle').fetchone()[0])
        pc_history = (fixture.supervisor_root / 'model_access_refresh.sqlite').read_bytes()
        old_rows = {}
        for name, table in (('model_access_refresh.sqlite', 'renewals'),
                            ('supervisor_usage_feed.sqlite', 'snapshots')):
            with closing(sqlite3.connect(fixture.primary / name)) as db:
                old_rows[name] = db.execute('SELECT * FROM ' + table).fetchall()
        fixture.at += timedelta(seconds=60)
        fixture.renew_access(fixture.old_release)
        fixture.import_usage(fixture.old_release)
        after = await fixture.advance_worker(fixture.old_release, seconds=0)
        self.assertEqual(after['completed_batches'], 2)
        for key in ('configuration_hash', 'activated_at', 'administrative_activated_at', 'deadline', 'limits'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(read_paired_inception(fixture.worker_root), fixture.opening)
        self.assertEqual((fixture.supervisor_root / 'model_access_refresh.sqlite').read_bytes(), pc_history)
        for name, table in (('model_access_refresh.sqlite', 'renewals'),
                            ('supervisor_usage_feed.sqlite', 'snapshots')):
            with self.subTest(database=name), closing(sqlite3.connect(fixture.primary / name)) as db:
                rows = db.execute('SELECT * FROM ' + table + ' ORDER BY at').fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[:1], old_rows[name])
