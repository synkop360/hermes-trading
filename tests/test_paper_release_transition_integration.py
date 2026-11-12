"""Offline native successor composition on declared invented paper inputs."""
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tarfile
import unittest
from unittest.mock import patch

from hermes_trading.reflection_supervision import _connect, _hash
from release_transition_fixtures import NativeTransitionFixture, PAPER_ENV, read_json
from scripts import paper_release_transition as transition


class NativeReleaseTransitionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def rows(self, path, table):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            return db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()

    def opening(self, root):
        from hermes_trading.paired_inception import read_paired_inception
        def database(path, *, check):
            check()
            return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True))
        return read_paired_inception(root, database_factory=database)

    async def test_successor_resumes_original_native_state_and_copies_plus_evidence(self):
        from hermes_trading.archive_snapshot import ownership
        from hermes_trading.model_access_refresh import build_access_request, install_free_access
        from hermes_trading.paired_packet_storage import load_packet
        from hermes_trading.paper_backup import create_daily_backup
        from hermes_trading.paper_safety import record_worker_result
        from hermes_trading.supervisor_launch import validate_launch
        from hermes_trading.supervisor_lifecycle import admit_cycle
        from hermes_trading.supervisor_sync import make_snapshot, validate_snapshot
        from hermes_trading.supervisor_usage_feed import build_usage_request, install_supervisor_usage
        from scripts.pull_paper_backup import pull_completed

        f = await NativeTransitionFixture.create(self)
        # These original bytes predate the frozen plan and must travel with the
        # retained experiment. They are never excluded or renamed.
        evidence = {'lost+found/evidence+raw.bin': b'\x00synthetic+original\xff\n',
                    'lost+found/nested+dir/packet+01.dat': bytes(range(256))}
        for relative, payload in evidence.items():
            path = f.worker_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        # Record actual native failed observations. A later successful native
        # batch must retain the durable latch; no healthy receipt is fabricated.
        with patch('hermes_trading.paper_safety._utc_now', side_effect=lambda: f.at):
            for _ in range(3):
                record_worker_result(f.worker_root, success=False,
                                     error_kind='synthetic_worker_failure', now=f.at)
        safety_before = (f.worker_root / 'paper_safety_state.json').read_bytes()
        self.assertTrue(json.loads(safety_before)['worker']['entry_halt'])
        manifest_before = (f.worker_root / 'manifest.json').read_bytes()
        account_before = {profile['directory']: (f.worker_root / profile['directory'] /
                          'wallet_account.json').read_bytes() for profile in f.manifest['profiles']}
        economics_before = {f.worker_root / profile['directory'] / name:
                            (f.worker_root / profile['directory'] / name).read_bytes()
                            for profile in f.manifest['profiles']
                            for name in ('strategy.yaml', 'goal.yaml', 'wallet_cost_sources.json')}
        runtime_before = self.rows(f.worker_root / 'worker_runtime.sqlite', 'lifecycle')
        worker_before = json.loads(runtime_before[0][1])
        pc_before = read_json(f.supervisor_root / 'supervisor_lifecycle.json')
        historical_pc_refresh = (f.supervisor_root / 'model_access_refresh.sqlite').read_bytes()
        review_before = self.rows(f.supervisor_root / 'llm_review_state.sqlite', 'reviews')
        batch_before = self.rows(f.primary / 'paper_publications.sqlite', 'paired_batches')
        self.assertEqual(len(batch_before), 1)
        immutable_history = {}
        for profile in f.manifest['profiles']:
            state = f.worker_root / profile['directory']
            for database, table in (('wallet_equity.sqlite', 'observations'),
                                    ('wallet_cost_accrual.sqlite', 'observations'),
                                    ('wallet_cost_accrual.sqlite', 'events')):
                immutable_history[(state / database, table)] = self.rows(state / database, table)
        for database, table in (('model_access_refresh.sqlite', 'renewals'),
                                ('supervisor_usage_feed.sqlite', 'snapshots'),
                                ('supervisor_usage_feed.sqlite', 'events')):
            immutable_history[(f.primary / database, table)] = self.rows(f.primary / database, table)
        with f.release_context(f.old_release):
            old_access_request = build_access_request(read_json(f.primary / 'model_access.json'),
                                                      f.policy, f.account)
            old_usage_request = build_usage_request(f.usage, f.policy, f.account)

        options = dict(worker_root=f.worker_root, supervisor_root=f.supervisor_root,
                       launch_file=f.launch_file, audit_dir=f.audit_dir)
        plan = transition.prepare_plan(f.worker_root, f.supervisor_root, f.launch_file,
                                      release_plan=f.release_plan, expected_identity=f.expected_identity)
        receipt = transition.apply_transition(plan, **options, apply=True,
                                              approval_ref='synthetic-owner-approval')
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['original_opening_hash'], _hash(f.opening))
        inspected = transition.inspect_transition(plan, **options)
        self.assertTrue(all(row['state'] == 'after' for row in inspected['components']))
        self.assertEqual(self.opening(f.worker_root), f.opening)
        self.assertEqual((f.worker_root / 'manifest.json').read_bytes(), manifest_before)
        self.assertEqual(self.rows(f.worker_root / 'worker_runtime.sqlite', 'lifecycle'), runtime_before)
        self.assertEqual((f.worker_root / 'paper_safety_state.json').read_bytes(), safety_before)
        self.assertEqual(self.rows(f.primary / 'paper_publications.sqlite', 'paired_batches'), batch_before)
        for key, rows in immutable_history.items():
            self.assertEqual(self.rows(*key), rows, str(key))
        pc_bound = read_json(f.supervisor_root / 'supervisor_lifecycle.json')
        self.assertNotEqual(pc_bound['binding'], pc_before['binding'])
        self.assertEqual({k: v for k, v in pc_bound.items() if k != 'binding'},
                         {k: v for k, v in pc_before.items() if k != 'binding'})

        # Old requests fail the successor's release check. An old executable
        # also cannot consume the newly bound journal, even with its own map.
        for release in (f.new_release, f.old_release):
            with self.subTest(old_importer_release=release['runtime']['archive_snapshot.py']), \
                    f.release_context(release):
                with self.assertRaisesRegex(ValueError, 'release|binding'):
                    install_free_access(f.primary, old_access_request, env=PAPER_ENV, now=f.at)
                with self.assertRaisesRegex(ValueError, 'release|binding'):
                    install_supervisor_usage(f.primary, old_usage_request, env=PAPER_ENV, now=f.at)
        f.at += timedelta(seconds=60)
        renewal = f.renew_access(f.new_release)
        usage = f.import_usage(f.new_release)
        self.assertEqual(renewal['verified_at'], f.at.isoformat())
        self.assertEqual(renewal['valid_until'], (f.at + timedelta(minutes=5)).isoformat())
        self.assertEqual(usage['covered_through'], f.at.isoformat())
        self.assertEqual(read_json(f.primary / 'model_access.json')['provenance_ref'],
                         old_access_request['access']['provenance_ref'])
        worker_after = await f.advance_worker(f.new_release, seconds=0)
        self.assertEqual(worker_after['completed_batches'], worker_before['completed_batches'] + 1)
        for key in ('configuration_hash', 'activated_at', 'administrative_activated_at', 'deadline', 'limits'):
            self.assertEqual(worker_after[key], worker_before[key], key)
        self.assertEqual(self.opening(f.worker_root), f.opening)
        self.assertEqual((f.worker_root / 'manifest.json').read_bytes(), manifest_before)
        self.assertEqual((f.supervisor_root / 'model_access_refresh.sqlite').read_bytes(), historical_pc_refresh)
        self.assertEqual(self.rows(f.supervisor_root / 'llm_review_state.sqlite', 'reviews'), review_before)
        with closing(sqlite3.connect(f.primary / 'paper_publications.sqlite')) as db:
            batches = db.execute('SELECT batch_id,status,payload,completed FROM paired_batches ORDER BY rowid').fetchall()
            self.assertEqual(len(batches), 2)
            for row, release in zip(batches, (f.old_release, f.new_release)):
                self.assertEqual(row[1], 'complete')
                self.assertEqual(load_packet(db, row[2])['release'], release['runtime'])
                self.assertEqual(len(load_packet(db, row[3])), 8)
        self.assertEqual(self.rows(f.primary / 'paper_publications.sqlite', 'paired_batches')[:1], batch_before)
        for key, rows in immutable_history.items():
            actual = self.rows(*key)
            self.assertEqual(actual[:len(rows)], rows, str(key))
        for database, table in (('model_access_refresh.sqlite', 'renewals'),
                                ('supervisor_usage_feed.sqlite', 'snapshots')):
            self.assertEqual(len(self.rows(f.primary / database, table)), 2)
            binding = json.loads(self.rows(f.primary / database, 'binding')[0][1])
            self.assertEqual(binding['release'], _hash(f.new_release['runtime']))
        for name, raw in account_before.items():
            self.assertEqual((f.worker_root / name / 'wallet_account.json').read_bytes(), raw)
            report = read_json(f.worker_root / name / 'wallet_metrics.json')
            self.assertEqual(report['status'], 'observed')
            self.assertEqual(report['observed_at'], f.at.isoformat())
            self.assertEqual(Decimal(report['observation']['net_equity']),
                             Decimal(f.opening['accounts'][name]['net_equity']))
        for path, raw in economics_before.items():
            self.assertEqual(path.read_bytes(), raw, str(path))
        safety_after = read_json(f.worker_root / 'paper_safety_state.json')
        self.assertTrue(safety_after['worker']['entry_halt'])
        self.assertEqual(safety_after['worker']['consecutive_errors'], 3)
        self.assertIsNone(safety_after['supervisor'])
        self.assertTrue(read_json(f.primary / 'heartbeat.json')['entry_admission']['blocked'])
        # New financial/renewal/usage work makes the original rollback obsolete.
        # Check before native snapshot readers open WAL auxiliary files.
        with self.assertRaisesRegex(ValueError, 'new work|non-binding|changed'):
            transition.rollback_transition(plan, **options, apply=True,
                                           approval_ref='synthetic-obsolete-rollback')
        with f.release_context(f.new_release):
            launch = validate_launch(f.launch_file, f.project_root, f.new_release)
            self.assertEqual(launch['local_state_dir'], f.supervisor_root)
            files = validate_snapshot(make_snapshot(f.primary, now=f.at), f.policy, now=f.at,
                                      expected_primary=f.selection, expected_account_hash=_hash(f.account))
            self.assertEqual(json.loads(files['heartbeat.json'])['worker_release']['hashes'],
                             f.new_release['runtime'])
            with closing(_connect(f.supervisor_root / 'watcher_cycle.sqlite')) as owner:
                owner.commit()
                cycle = admit_cycle(f.supervisor_root, f.policy, f.new_release, now=f.at)
        self.assertEqual(cycle.status, 'admitted')
        self.assertEqual(cycle.state['cycles_started'], pc_before['cycles_started'] + 1)
        self.assertEqual(cycle.state['deadline'], pc_before['deadline'])
        self.assertEqual(cycle.state['model_attempts_reserved'], pc_before['model_attempts_reserved'])
        # Use a new destination, since the fixture's earlier same-day backup is
        # correctly immutable. This genuine native capture sees the plus files.
        daily_destination = f.project_root.parent / 'composed-daily'
        with ownership(f.worker_root, role='worker', enabled=True) as owner:
            daily = create_daily_backup(f.worker_root, daily_destination, enabled=True,
                                        owner_db=owner, now=f.at, max_bytes=500000000,
                                        timeout_seconds=30, keep=2)
        self.assertEqual(daily['status'], 'completed')
        self.assertIs(daily['off_host_verified'], False)
        daily_path = Path(daily['path'])
        for relative, payload in evidence.items():
            self.assertEqual((daily_path / relative).read_bytes(), payload)
        def download(name, target):
            self.assertEqual(name, daily_path.name)
            with tarfile.open(target, 'w') as archive:
                archive.add(daily_path, arcname=name, recursive=True)
        pulled = pull_completed(f.project_root.parent / 'synthetic-offhost',
                                source_identity=daily['source_identity'],
                                day=f.at.date().isoformat(), download=download)
        self.assertEqual(pulled['status'], 'completed')
        self.assertIs(pulled['off_host_verified'], True)
        self.assertEqual(pulled['sha256'], daily['sha256'])
        for relative, payload in evidence.items():
            self.assertEqual((Path(pulled['path']) / relative).read_bytes(), payload)
            self.assertEqual((f.worker_root / relative).read_bytes(), payload)
        self.assertEqual(self.opening(Path(pulled['path'])), f.opening)
        self.assertEqual(read_json(f.worker_root / 'paper_safety_state.json'), safety_after)


if __name__ == '__main__':
    unittest.main()
