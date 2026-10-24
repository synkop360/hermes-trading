"""One frozen plan applied independently to two synthetic host views.

Each host owns one actual peer and an original, closed reference of the other.
Reference copies are never booted. No transport or coordination framework is
introduced; the operator must collect both scoped receipts and native checks.
"""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from hermes_trading.reflection_supervision import _connect, _hash
from release_transition_fixtures import NativeTransitionFixture, read_json
from scripts import paper_release_transition as transition


class CrossHostTransitionTests(unittest.IsolatedAsyncioTestCase):
    def files(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob('*') if path.is_file()}

    def rows(self, path, table):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            return db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()

    def opening(self, root):
        from hermes_trading.paired_inception import read_paired_inception
        def database(path, *, check):
            check()
            return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True))
        return read_paired_inception(root, database_factory=database)

    async def test_two_host_views_require_two_scoped_receipts_and_actual_native_checks(self):
        from hermes_trading.supervisor_launch import validate_launch
        from hermes_trading.supervisor_lifecycle import admit_cycle
        from hermes_trading.supervisor_sync import make_snapshot, validate_snapshot

        f = await NativeTransitionFixture.create(self)
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-cross-host-')
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        worker_host = base / 'worker-host'
        pc_host = base / 'pc-host'
        worker_host.mkdir()
        pc_host.mkdir()
        # Both references are captured before either actual peer is changed.
        # Closed-copy admission is strict: never hide or remove SQLite sidecars.
        for root in (f.worker_root, f.supervisor_root):
            self.assertFalse(any(path.name.endswith(('-wal', '-shm', '-journal'))
                                 for path in root.rglob('*') if path.is_file()))
        pc_reference = worker_host / 'original-pc-reference'
        worker_reference = pc_host / 'original-worker-reference'
        launch_reference = worker_host / 'original-launch-reference.json'
        shutil.copytree(f.supervisor_root, pc_reference)
        shutil.copytree(f.worker_root, worker_reference)
        shutil.copy2(f.launch_file, launch_reference)
        actual_pc_before = self.files(f.supervisor_root)
        actual_launch_before = f.launch_file.read_bytes()
        actual_runtime_before = self.rows(f.worker_root / 'worker_runtime.sqlite', 'lifecycle')
        worker_before = json.loads(actual_runtime_before[0][1])
        pc_before = read_json(f.supervisor_root / 'supervisor_lifecycle.json')
        manifest_before = (f.worker_root / 'manifest.json').read_bytes()
        account_before = {p['directory']: (f.worker_root / p['directory'] /
                          'wallet_account.json').read_bytes() for p in f.manifest['profiles']}
        batch_before = self.rows(f.primary / 'paper_publications.sqlite', 'paired_batches')
        history_before = {}
        for profile in f.manifest['profiles']:
            history_before[(f.worker_root / profile['directory'] / 'wallet_equity.sqlite',
                            'observations')] = self.rows(
                                f.worker_root / profile['directory'] / 'wallet_equity.sqlite', 'observations')
        for database, table in (('model_access_refresh.sqlite', 'renewals'),
                                ('supervisor_usage_feed.sqlite', 'events'),
                                ('supervisor_usage_feed.sqlite', 'snapshots')):
            history_before[(f.primary / database, table)] = self.rows(f.primary / database, table)
        pc_refresh_before = (f.supervisor_root / 'model_access_refresh.sqlite').read_bytes()
        pc_reviews_before = self.rows(f.supervisor_root / 'llm_review_state.sqlite', 'reviews')
        worker_options = dict(worker_root=f.worker_root, supervisor_root=pc_reference,
                              launch_file=launch_reference, audit_dir=worker_host / 'audit')
        pc_options = dict(worker_root=worker_reference, supervisor_root=f.supervisor_root,
                          launch_file=f.launch_file, audit_dir=pc_host / 'audit')
        plan = transition.prepare_plan(
            f.worker_root, pc_reference, launch_reference,
            release_plan=f.release_plan, expected_identity=f.expected_identity)
        peer_plan = transition.prepare_plan(
            worker_reference, f.supervisor_root, f.launch_file,
            release_plan=f.release_plan, expected_identity=f.expected_identity)
        self.assertEqual(peer_plan, plan)

        worker_receipt = transition.apply_transition(
            plan, **worker_options, apply=True, approval_ref='synthetic-two-host-owner-approval')
        self.assertEqual(worker_receipt['status'], 'completed')
        self.assertEqual(self.files(f.supervisor_root), actual_pc_before)
        self.assertEqual(f.launch_file.read_bytes(), actual_launch_before)
        self.assertFalse(pc_options['audit_dir'].exists())
        worker_intent = read_json(worker_options['audit_dir'] / 'intent.json')
        self.assertEqual(worker_intent['transition_id'], plan['transition_id'])
        self.assertEqual(worker_intent['root_binding'], _hash({
            'worker': str(f.worker_root.resolve()), 'supervisor': str(pc_reference.resolve()),
            'launch': str(launch_reference.resolve())}))
        if 'root_binding' in worker_receipt:
            self.assertEqual(worker_receipt['root_binding'], worker_intent['root_binding'])
        # One host's completion is insufficient: the actual PC still rejects
        # the successor launch, and its local audit has never been prepared.
        with self.assertRaisesRegex(ValueError, 'release|lifecycle'):
            validate_launch(f.launch_file, f.project_root, f.new_release)
        wrong_audit = dict(pc_options, audit_dir=worker_options['audit_dir'])
        with self.assertRaisesRegex(ValueError, 'different transition|copies|root'):
            transition.apply_transition(plan, **wrong_audit, apply=True,
                                        approval_ref='synthetic-two-host-owner-approval')
        self.assertEqual(self.files(f.supervisor_root), actual_pc_before)

        pc_receipt = transition.apply_transition(
            plan, **pc_options, apply=True, approval_ref='synthetic-two-host-owner-approval')
        pc_intent = read_json(pc_options['audit_dir'] / 'intent.json')
        self.assertEqual(pc_receipt['status'], 'completed')
        self.assertEqual(pc_receipt['transition_id'], worker_receipt['transition_id'])
        self.assertEqual(pc_intent['plan'], worker_intent['plan'])
        self.assertEqual(pc_intent['root_binding'], _hash({
            'worker': str(worker_reference.resolve()), 'supervisor': str(f.supervisor_root.resolve()),
            'launch': str(f.launch_file.resolve())}))
        self.assertNotEqual(pc_intent['root_binding'], worker_intent['root_binding'])
        if 'root_binding' in pc_receipt:
            self.assertEqual(pc_receipt['root_binding'], pc_intent['root_binding'])
        for options in (worker_options, pc_options):
            inspected = transition.inspect_transition(plan, **options)
            self.assertTrue(all(c['state'] == 'after' for c in inspected['components']))
        self.assertEqual(self.rows(f.worker_root / 'worker_runtime.sqlite', 'lifecycle'), actual_runtime_before)
        self.assertEqual(self.rows(f.primary / 'paper_publications.sqlite', 'paired_batches'), batch_before)
        self.assertEqual(self.opening(f.worker_root), f.opening)
        self.assertEqual((f.worker_root / 'manifest.json').read_bytes(), manifest_before)
        for (path, table), rows in history_before.items():
            self.assertEqual(self.rows(path, table), rows)
        for name, raw in account_before.items():
            self.assertEqual((f.worker_root / name / 'wallet_account.json').read_bytes(), raw)
        pc_after = read_json(f.supervisor_root / 'supervisor_lifecycle.json')
        self.assertEqual({k: v for k, v in pc_after.items() if k != 'binding'},
                         {k: v for k, v in pc_before.items() if k != 'binding'})
        self.assertEqual((f.supervisor_root / 'model_access_refresh.sqlite').read_bytes(), pc_refresh_before)
        self.assertEqual(self.rows(f.supervisor_root / 'llm_review_state.sqlite', 'reviews'), pc_reviews_before)
        # Two binding receipts still do not certify a restarted worker: its
        # genuine heartbeat carries the retained old startup release.
        with f.release_context(f.new_release), self.assertRaisesRegex(ValueError, 'release'):
            make_snapshot(f.primary, now=f.at)

        f.at += timedelta(seconds=60)
        f.renew_access(f.new_release)
        f.import_usage(f.new_release)
        worker_after = await f.advance_worker(f.new_release, seconds=0)
        self.assertEqual(worker_after['completed_batches'], worker_before['completed_batches'] + 1)
        for key in ('configuration_hash', 'activated_at', 'administrative_activated_at', 'deadline', 'limits'):
            self.assertEqual(worker_after[key], worker_before[key])
        self.assertEqual(self.opening(f.worker_root), f.opening)
        self.assertEqual((f.worker_root / 'manifest.json').read_bytes(), manifest_before)
        self.assertEqual(self.rows(f.primary / 'paper_publications.sqlite', 'paired_batches')[:1], batch_before)
        for (path, table), rows in history_before.items():
            self.assertEqual(self.rows(path, table)[:len(rows)], rows)
        for name, raw in account_before.items():
            self.assertEqual((f.worker_root / name / 'wallet_account.json').read_bytes(), raw)
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
        for receipt in (worker_receipt, pc_receipt):
            self.assertEqual(receipt['qualification'], 'binding_correction_only_no_profitability_or_activation')
        self.assertIsNone(read_json(f.worker_root / 'paper_safety_state.json')['supervisor'])
        self.assertTrue(read_json(f.primary / 'heartbeat.json')['entry_admission']['blocked'])


if __name__ == '__main__':
    unittest.main()
