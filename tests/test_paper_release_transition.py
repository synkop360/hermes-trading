"""A release-only correction must not acquire permission to reset paper state."""
from copy import deepcopy
from contextlib import closing
import importlib
import importlib.util
import json
import sqlite3
import unittest

from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash


BASE = '6c9d520da5f8473b51c912d1eb0e269ee94d4b1b'
TARGET = dict(project='f78f42d9-20ec-44f6-ae3f-f8bb79ee7000',
              service='8b94cbc9-293e-4222-a8a7-698b656985f7',
              environment='fdcc71a3-00a9-410c-8d6f-59af9bc98d36', experiment_id='paired-test')


def api():
    if importlib.util.find_spec('scripts.paper_release_transition') is None:
        raise AssertionError('checked release transition helper is absent')
    return importlib.import_module('scripts.paper_release_transition')


def release_plan():
    current = _release_hashes()
    old = dict(runtime=current, watcher='a'*64, native_launcher='b'*64)
    new = deepcopy(old)
    old['runtime'] = dict(current, **{'archive_snapshot.py': 'c'*64})
    return dict(old_commit=BASE, new_commit='d'*40, target=deepcopy(TARGET), old=old, new=new)


def closed_opening(root):
    from hermes_trading.paired_inception import read_paired_inception
    def reader(path, *, check):
        check()
        return closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True))
    return read_paired_inception(root,database_factory=reader)


class ReleasePlanValidationTests(unittest.TestCase):
    def test_only_snapshot_runtime_digest_may_change(self):
        plan = release_plan(); before = deepcopy(plan)
        api().validate_release_plan(plan)
        self.assertEqual(plan, before)

    def test_other_runtime_watcher_launcher_keys_and_noop_are_rejected(self):
        original = release_plan()
        for case in ('runtime', 'watcher', 'launcher', 'extra', 'removed', 'noop'):
            plan = deepcopy(original)
            if case == 'runtime': plan['new']['runtime']['paired_worker.py'] = 'e'*64
            elif case == 'watcher': plan['new']['watcher'] = 'e'*64
            elif case == 'launcher': plan['new']['native_launcher'] = 'e'*64
            elif case == 'extra': plan['new']['runtime']['foreign.py'] = 'e'*64
            elif case == 'removed': plan['new']['runtime'].pop('paired_worker.py')
            else: plan['new'] = deepcopy(plan['old'])
            with self.subTest(case=case), self.assertRaises(ValueError):
                api().validate_release_plan(plan)

    def test_source_commits_target_and_malformed_digests_fail_closed(self):
        original = release_plan()
        for case in ('old_commit', 'new_commit', 'same_commit', 'service', 'digest', 'extra_key'):
            plan = deepcopy(original)
            if case == 'old_commit': plan['old_commit'] = '1'*40
            elif case == 'new_commit': plan['new_commit'] = 'not-a-commit'
            elif case == 'same_commit': plan['new_commit'] = BASE
            elif case == 'service': plan['target']['service'] = '0'*36
            elif case == 'digest': plan['new']['runtime']['archive_snapshot.py'] = 'G'*64
            else: plan['approval_implied'] = True
            with self.subTest(case=case), self.assertRaises(ValueError):
                api().validate_release_plan(plan)


class ClosedCopyTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from release_transition_fixtures import NativeTransitionFixture
        self.fixture = await NativeTransitionFixture.create(self)
        self.module = api()

    def options(self, fixture=None):
        f = fixture or self.fixture
        return dict(worker_root=f.worker_root, supervisor_root=f.supervisor_root,
                    launch_file=f.launch_file, audit_dir=f.audit_dir)

    def plan(self, fixture=None, identity=None):
        f = fixture or self.fixture
        return self.module.prepare_plan(f.worker_root, f.supervisor_root, f.launch_file,
            release_plan=f.release_plan, expected_identity=identity or f.expected_identity)

    def apply(self, plan, **extra):
        return self.module.apply_transition(plan, **self.options(), apply=True,
                                            approval_ref='explicit-synthetic-test-approval', **extra)

    def histories(self):
        f = self.fixture; result = {}
        for path, tables in ((f.primary/'model_access_refresh.sqlite', ('renewals',)),
                             (f.primary/'supervisor_usage_feed.sqlite', ('events', 'snapshots'))):
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                for table in tables:
                    result[table] = db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
        return result

    async def test_plan_inspection_and_default_apply_do_not_change_inputs(self):
        f = self.fixture
        before = {str(p): p.read_bytes() for root in (f.worker_root, f.supervisor_root)
                  for p in root.rglob('*') if p.is_file()}
        launch = f.launch_file.read_bytes()
        plan = self.plan()
        report = self.module.inspect_transition(plan, **self.options())
        self.assertEqual(report['status'], 'inspected')
        self.assertTrue(all(c['state'] == 'before' for c in report['components']))
        self.assertEqual(self.module.apply_transition(plan, **self.options()), report)
        self.assertFalse(f.audit_dir.exists())
        self.assertEqual(launch, f.launch_file.read_bytes())
        self.assertEqual(before, {str(p): p.read_bytes() for root in (f.worker_root, f.supervisor_root)
                                for p in root.rglob('*') if p.is_file()})

    async def test_exact_apply_and_retry_preserve_opening_history_limits_and_counters(self):
        f = self.fixture; plan = self.plan(); histories = self.histories()
        lifecycle = json.loads((f.supervisor_root/'supervisor_lifecycle.json').read_text())
        historical_pc_import = (f.supervisor_root/'model_access_refresh.sqlite').read_bytes()
        receipt = self.apply(plan)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(self.apply(plan), receipt)
        self.assertEqual(closed_opening(f.worker_root), f.opening)
        self.assertEqual(self.histories(), histories)
        self.assertEqual((f.supervisor_root/'model_access_refresh.sqlite').read_bytes(), historical_pc_import)
        after = json.loads((f.supervisor_root/'supervisor_lifecycle.json').read_text())
        self.assertEqual({k:v for k,v in after.items() if k!='binding'},
                         {k:v for k,v in lifecycle.items() if k!='binding'})
        status = self.module.inspect_transition(plan, **self.options())
        self.assertTrue(all(c['state'] == 'after' for c in status['components']))
        self.assertTrue((f.audit_dir/'intent.json').is_file())

    async def test_wrong_identity_economics_or_plan_edit_is_rejected_before_any_write(self):
        f = self.fixture
        for field in ('manifest_sha256', 'opening_hash', 'policy_hash'):
            identity = deepcopy(f.expected_identity); identity[field] = '0'*64
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan(identity=identity)
        plan = self.plan()
        bad = deepcopy(plan); bad['release_plan']['target']['service'] = '0'*36
        with self.assertRaises(ValueError): self.apply(bad)
        path = f.worker_root/'control_50/strategy.yaml'
        path.write_bytes(path.read_bytes()+b'\n# unexpected post-plan edit\n')
        with self.assertRaises(ValueError): self.apply(plan)
        self.assertFalse(f.audit_dir.exists())

    async def test_rehashed_plan_cannot_expand_paths_kind_or_non_release_fields(self):
        f = self.fixture; original = self.plan()
        for case in ('path','kind','counter','wrong_release','extra_component'):
            bad = deepcopy(original)
            if case == 'path': bad['components'][0]['path'] = '../foreign.sqlite'
            elif case == 'kind': bad['components'][0]['kind'] = 'json'
            elif case == 'extra_component': bad['components'].append(deepcopy(bad['components'][0]))
            else:
                component = next(c for c in bad['components'] if c['name']=='pc_limits')
                body = json.loads(component['after'])
                body['max_cycles' if case=='counter' else 'release_hash'] = 1 if case=='counter' else '0'*64
                component['after'] = json.dumps(body)
                import hashlib
                component['after_hash'] = hashlib.sha256(component['after'].encode()).hexdigest()
            bad['transition_id'] = _hash({k:v for k,v in bad.items() if k!='transition_id'})
            with self.subTest(case=case), self.assertRaises(ValueError): self.apply(bad)
        self.assertFalse(f.audit_dir.exists())

    async def test_changed_preplan_economics_are_refused_against_native_original_bindings(self):
        import yaml
        f = self.fixture
        for name, mutate in (
            ('control_50/strategy.yaml',lambda v: v['entry'].update(threshold='31')),
            ('adaptive_200/goal.yaml',lambda v:v.update(assets=['BTC/EUR'])),
            ('worker_limits.json',lambda v:v.update(max_cycles=v['max_cycles']+1)),
            ('storage_limits.json',lambda v:v.update(reserve_bytes=v['reserve_bytes']+1)),
            ('paper_safety.json',lambda v:v.update(diagnostics_generations=2)),
        ):
            path = f.worker_root/name; original = path.read_bytes()
            try:
                value = yaml.safe_load(original) if path.suffix=='.yaml' else json.loads(original)
                mutate(value)
                path.write_text(yaml.safe_dump(value) if path.suffix=='.yaml' else json.dumps(value))
                with self.subTest(path=name),self.assertRaises(ValueError):self.plan()
            finally:path.write_bytes(original)
        self.assertFalse(f.audit_dir.exists())

    async def test_unexpected_importer_trigger_cannot_modify_preserved_history(self):
        f = self.fixture
        with closing(sqlite3.connect(f.primary/'model_access_refresh.sqlite')) as db:
            db.execute('CREATE TRIGGER unexpected AFTER UPDATE ON binding BEGIN DELETE FROM renewals; END')
            db.commit()
        with self.assertRaises(ValueError):self.plan()
        self.assertEqual(len(self.histories()['renewals']),1)
        self.assertFalse(f.audit_dir.exists())

    async def test_cli_defaults_to_inspection_and_bounded_reads_do_not_write(self):
        from itertools import count
        from unittest.mock import patch
        from pathlib import Path
        import subprocess,sys
        f = self.fixture; plan = self.plan()
        plan_file = f.project_root/'transition-plan.json'
        plan_file.write_text(json.dumps(plan))
        args = [sys.executable,'-B','-m','scripts.paper_release_transition',
                '--plan',str(plan_file),'--worker-root',str(f.worker_root),
                '--supervisor-root',str(f.supervisor_root),'--launch-file',str(f.launch_file),
                '--audit-dir',str(f.audit_dir)]
        result = subprocess.run(args,cwd=Path(__file__).resolve().parents[1],
                                capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'inspected')
        self.assertFalse(f.audit_dir.exists())
        with self.assertRaises(ValueError):
            self.module.apply_transition(plan,**self.options(),apply=True)
        ticks = count(0,31)
        with patch('scripts.paper_release_transition.time.monotonic',side_effect=lambda:next(ticks)):
            with self.assertRaises(TimeoutError): self.apply(plan)
        self.assertFalse(f.audit_dir.exists())

    async def test_existing_wal_is_refused_without_ignoring_or_deleting_it(self):
        f = self.fixture; plan = self.plan()
        with closing(sqlite3.connect(f.primary/'model_access_refresh.sqlite')) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE binding SET payload=payload WHERE id=1");writer.commit()
            wal = f.primary/'model_access_refresh.sqlite-wal'
            self.assertTrue(wal.is_file())
            before = wal.read_bytes()
            with self.assertRaisesRegex(ValueError,'sidecar-free'):
                self.module.inspect_transition(plan,**self.options())
            self.assertEqual(wal.read_bytes(),before)
        self.assertFalse(f.audit_dir.exists())

    async def test_active_native_worker_cycle_review_or_importer_refuses_apply(self):
        from hermes_trading.reflection_supervision import _connect
        f = self.fixture; plan = self.plan()
        paths = (f.worker_root/'worker_runtime.sqlite', f.supervisor_root/'watcher_cycle.sqlite',
                 f.supervisor_root/'llm_review_state.sqlite', f.primary/'model_access_refresh.sqlite',
                 f.primary/'supervisor_usage_feed.sqlite')
        for path in paths:
            with self.subTest(path=path.name), closing(_connect(path)) as owner:
                owner.commit()
                with self.assertRaises((ValueError, RuntimeError, sqlite3.OperationalError)):
                    self.apply(plan)
            self.assertFalse(f.audit_dir.exists())

    async def test_pending_batch_publication_review_and_delivery_are_not_cleared(self):
        f = self.fixture
        path = f.primary/'paper_publications.sqlite'
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE paired_batches SET status='pending'"); db.commit()
        with self.assertRaises(ValueError): self.plan()
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT status FROM paired_batches').fetchone()[0], 'pending')
            db.execute("UPDATE paired_batches SET status='complete'"); db.commit()
            db.execute('CREATE TABLE IF NOT EXISTS publications(review_id TEXT PRIMARY KEY,request_hash TEXT,request TEXT,base_bytes TEXT,receipt TEXT)')
            db.execute("INSERT INTO publications VALUES('synthetic-pending','hash','{}','original',NULL)");db.commit()
        with self.assertRaises(ValueError): self.plan()
        with closing(sqlite3.connect(path)) as db:
            self.assertIsNone(db.execute("SELECT receipt FROM publications WHERE review_id='synthetic-pending'").fetchone()[0])
            db.execute("DELETE FROM publications WHERE review_id='synthetic-pending'");db.commit()
        marker = f.supervisor_root/'paper_supervisor_health_pending.json'
        marker.write_text('{"pending":"synthetic delivery"}')
        with self.assertRaises(ValueError): self.plan()
        self.assertTrue(marker.exists()); marker.unlink()
        with closing(sqlite3.connect(f.supervisor_root/'llm_review_state.sqlite')) as db:
            state = json.loads(db.execute('SELECT state FROM supervisor WHERE id=1').fetchone()[0])
            state['pending_review'] = {'review_id':'unresolved-synthetic-inference'}
            db.execute('UPDATE supervisor SET state=? WHERE id=1', (json.dumps(state),)); db.commit()
        with self.assertRaises(ValueError): self.plan()
        self.assertFalse(f.audit_dir.exists())

    async def test_commit_before_receipt_requires_reconciliation_and_resumes_exactly(self):
        f = self.fixture; plan = self.plan(); calls = []
        def interrupt(point, component):
            if point == 'component_commit' and not calls:
                calls.append(component); raise OSError('synthetic lost component receipt')
        with self.assertRaises(OSError): self.apply(plan, fault=interrupt)
        self.assertTrue(calls)
        self.assertTrue((f.audit_dir/'intent.json').exists())
        with self.assertRaises(ValueError): self.apply(plan)
        receipt = self.apply(plan, reconcile=True)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(self.apply(plan), receipt)
        self.assertEqual(self.histories()['events'][0][0], f.usage['model_usage']['events'][0]['id'])

    async def test_checked_rollback_restores_bindings_without_rewinding_new_history(self):
        from datetime import timedelta
        f = self.fixture; plan = self.plan(); histories = self.histories()
        self.apply(plan)
        result = self.module.rollback_transition(plan, **self.options(), apply=True,
                                                approval_ref='explicit-synthetic-rollback')
        self.assertEqual(result['status'], 'rolled_back')
        self.assertEqual(self.histories(), histories)
        self.assertEqual(closed_opening(f.worker_root), f.opening)
        self.assertTrue(all(c['state']=='before' for c in self.module.inspect_transition(plan, **self.options())['components']))
        # A separate transition audit is needed after a completed rollback.
        f.audit_dir = f.audit_dir.with_name('second-transition-audit')
        second = self.plan(); second_receipt = self.apply(second)
        f.at += timedelta(seconds=1); f.renew_access(f.new_release)
        after_new_history = self.histories()
        self.assertEqual(self.apply(second),second_receipt,'completed replay must not rewind new work')
        with self.assertRaises(ValueError):
            self.module.rollback_transition(second, **self.options(), apply=True,
                                            approval_ref='synthetic-attempt-after-new-row')
        self.assertEqual(self.histories(), after_new_history)

    async def test_interruption_at_every_component_commit_and_receipt_is_reconciled(self):
        from release_transition_fixtures import NativeTransitionFixture
        names = ['worker_renewal','worker_usage','worker_limits','pc_limits','pc_lifecycle','pc_launch']
        for boundary in ('component_commit','component_receipt'):
            for name in names:
                with self.subTest(boundary=boundary,name=name):
                    f = await NativeTransitionFixture.create(self); plan = self.plan(f)
                    options = self.options(f)
                    def crash(point,component):
                        if (point,component)==(boundary,name):raise OSError('synthetic interrupted transition')
                    with self.assertRaises(OSError):
                        self.module.apply_transition(plan,**options,apply=True,
                            approval_ref='synthetic-boundary-approval',fault=crash)
                    with self.assertRaises(ValueError):
                        self.module.apply_transition(plan,**options,apply=True,approval_ref='synthetic-boundary-approval')
                    result = self.module.apply_transition(plan,**options,apply=True,reconcile=True,
                                                          approval_ref='synthetic-boundary-reconciliation')
                    self.assertEqual(result['status'],'completed')
                    self.assertTrue(all(c['state']=='after' for c in self.module.inspect_transition(plan,**options)['components']))


if __name__ == '__main__':
    unittest.main()
