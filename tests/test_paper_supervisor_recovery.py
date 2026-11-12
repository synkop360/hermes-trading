"""Synthetic recovery only: no network, inference or installed state."""
from contextlib import contextmanager
import copy
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import unittest
import sqlite3
import tempfile
import base64
import io
from unittest.mock import patch

import test_paper_safety as fixtures
from hermes_trading import paper_safety as safety
from hermes_trading.reflection_supervision import _hash

NOW = fixtures.NOW


class RecoveryTransitionTests(unittest.TestCase):
    setUp = fixtures.PaperSafetyTests.setUp
    policy = fixtures.PaperSafetyTests.policy
    inventory = fixtures.PaperSafetyTests.inventory

    def prepare(self):
        self.policy_value = self.policy()
        for n in range(3):
            safety.record_supervisor_result(self.root, success=False, event_id=f'fail-{n}',
                now=NOW - timedelta(seconds=4-n), error_kind='supervisor_exception')
        safety.record_supervisor_result(self.root, success=True, event_id='healthy', now=NOW)
        safety.record_worker_result(self.root, success=True, now=NOW)
        self.before = safety._state(self.root, self.policy_value)
        self.request = dict(schema_version=1, purpose='paper_supervisor_recovery_v1', request_id='a'*32,
            experiment_id=self.config['experiment_id'], approval_ref='synthetic-approval',
            prepared_at=NOW.isoformat(), valid_until=(NOW+timedelta(seconds=120)).isoformat(),
            state_hash=_hash(self.before), supervisor_event_id='healthy', supervisor_observed_at=NOW.isoformat(),
            bindings={'synthetic': 'verified'}, pc_evidence={})

    @contextmanager
    def preflight(self):
        yield self.request['bindings']

    def recover(self, **kwargs):
        method = getattr(safety, 'recover_supervisor', None)
        self.assertTrue(callable(method), 'checked supervisor recovery is absent')
        return method(self.root, self.request, preflight=self.preflight, now=kwargs.pop('now', NOW+timedelta(seconds=1)), **kwargs)

    def test_opt_in_is_required_without_mutation(self):
        self.prepare(); before = self.inventory()
        with self.assertRaises(ValueError): self.recover()
        self.assertEqual(self.inventory(), before)

    def test_only_latch_fields_change_and_audit_preserves_old_row(self):
        self.prepare()
        result = self.recover(enabled=True)
        self.assertEqual(result['status'], 'completed')
        after = safety._state(self.root, self.policy_value)
        expected = copy.deepcopy(self.before)
        expected['supervisor'].update(entry_halt=False, consecutive_errors=0)
        self.assertEqual(after, expected)
        self.assertFalse(safety.entry_status(self.root, policy=self.policy_value, now=NOW+timedelta(seconds=2))['blocked'])
        intent = json.loads((self.root/'paper_supervisor_recovery'/('a'*32+'.intent.json')).read_text())
        self.assertEqual(intent['before'], self.before)
        self.assertEqual(intent['after_hash'], _hash(after))

    def test_stale_request_changed_health_and_failed_preflight_cannot_clear(self):
        for case in ('stale', 'changed', 'preflight'):
            with self.subTest(case=case):
                self.prepare()
                if case == 'changed':
                    safety.record_supervisor_result(self.root, success=True, event_id='new', now=NOW+timedelta(seconds=1))
                before = (self.root/'paper_safety_state.json').read_bytes()
                with patch.object(self, 'preflight', side_effect=ValueError('invalid')) if case=='preflight' else patch.dict({}, {}):
                    with self.assertRaises(ValueError):
                        self.recover(enabled=True, now=NOW+timedelta(seconds=121 if case=='stale' else 2))
                self.assertEqual((self.root/'paper_safety_state.json').read_bytes(), before)
                # Reinitialize this synthetic case only; never an installed state.
                (self.root/'paper_safety_state.json').unlink()

    def test_pending_crash_blocks_entries_then_exact_unchanged_retry_completes(self):
        self.prepare()
        original = safety._write
        def crash(root, value):
            original(root, value)
            raise OSError('synthetic crash after state replace')
        with patch.object(safety, '_write', side_effect=crash), self.assertRaises(OSError):
            self.recover(enabled=True)
        self.assertIn('supervisor_recovery_pending', safety.entry_status(self.root, policy=self.policy_value, now=NOW)['reasons'])
        self.assertEqual(self.recover(enabled=True)['status'], 'completed')

    def test_pending_recovery_preserves_new_health_and_abort_never_rewinds_it(self):
        self.prepare()
        original = safety._write
        def crash(root, value):
            original(root, value); raise OSError('synthetic crash')
        with patch.object(safety, '_write', side_effect=crash), self.assertRaises(OSError):
            self.recover(enabled=True)
        safety.record_worker_result(self.root, success=False, now=NOW+timedelta(seconds=2))
        safety.record_supervisor_result(self.root, success=False, event_id='new-error', now=NOW+timedelta(seconds=2))
        current = safety._state(self.root, self.policy_value)
        with self.assertRaises(ValueError): self.recover(enabled=True)
        result = self.recover(enabled=True, abort=True)
        expected = copy.deepcopy(current)
        expected['supervisor'].update(entry_halt=True, consecutive_errors=3)
        self.assertEqual(safety._state(self.root, self.policy_value), expected)
        self.assertEqual(result['status'], 'aborted')

    def test_conservative_abort_preserves_original_halt_after_state_replace(self):
        self.prepare()
        original = safety._write
        def crash(root, value):
            original(root, value); raise OSError('synthetic crash')
        with patch.object(safety, '_write', side_effect=crash), self.assertRaises(OSError):
            self.recover(enabled=True)
        self.assertEqual(self.recover(enabled=True, abort=True, now=NOW+timedelta(minutes=10))['status'], 'aborted')
        self.assertEqual(safety._state(self.root, self.policy_value), self.before)
        self.assertIn('supervisor_error_halt', safety.entry_status(self.root, policy=self.policy_value, now=NOW)['reasons'])

    def test_completed_retry_does_not_clear_a_newer_halt(self):
        self.prepare(); self.recover(enabled=True)
        for n in range(3):
            safety.record_supervisor_result(self.root, success=False, event_id=f'new-{n}', now=NOW+timedelta(seconds=n+2))
        before = (self.root/'paper_safety_state.json').read_bytes()
        result = self.recover(enabled=True)
        self.assertEqual(result['status'], 'already_completed')
        self.assertTrue(result['current_supervisor_halted'])
        self.assertEqual((self.root/'paper_safety_state.json').read_bytes(), before)
        self.request['approval_ref'] = 'different'
        with self.assertRaises(ValueError): self.recover(enabled=True)

    def test_marker_is_checked_after_state_read_and_malformed_marker_blocks(self):
        self.prepare(); self.recover(enabled=True)
        original = safety._state
        def interleave(root, policy):
            state = original(root, policy)
            (root/'paper_supervisor_recovery_pending.json').write_text('{invalid')
            return state
        with patch.object(safety, '_state', side_effect=interleave):
            status = safety.entry_status(self.root, policy=self.policy_value, now=NOW)
        self.assertTrue(status['blocked'])
        self.assertIn('supervisor_recovery_pending', status['reasons'])

    def test_every_durable_boundary_keeps_halt_or_pending_and_retries(self):
        for boundary in ('intent', 'marker', 'state', 'receipt', 'remove'):
            with self.subTest(boundary=boundary):
                f = RecoveryTransitionTests(); f.setUp(); self.addCleanup(f.doCleanups); f.prepare()
                once, write, clear = safety._recovery_once, safety._write, safety._recovery_clear_marker
                def persist(path, value):
                    once(path, value)
                    if ((boundary == 'intent' and path.name.endswith('.intent.json')) or
                        (boundary == 'marker' and path.name == safety._RECOVERY_PENDING) or
                        (boundary == 'receipt' and path.name.endswith('.receipt.json'))):
                        raise OSError('synthetic boundary')
                def replace(root, value):
                    write(root, value)
                    if boundary == 'state': raise OSError('synthetic boundary')
                def remove(root, value):
                    if boundary == 'remove': raise OSError('synthetic boundary')
                    clear(root, value)
                with patch.object(safety, '_recovery_once', side_effect=persist), patch.object(safety, '_write', side_effect=replace), patch.object(safety, '_recovery_clear_marker', side_effect=remove):
                    with self.assertRaises(OSError): f.recover(enabled=True)
                self.assertTrue(safety.entry_status(f.root, policy=f.policy_value, now=NOW)['blocked'])
                self.assertEqual(f.recover(enabled=True)['status'], 'completed')

    def test_absent_marker_cannot_reconcile_an_unreceipted_cleared_state(self):
        self.prepare();original=safety._write
        def crash(root,value):original(root,value);raise OSError('synthetic')
        with patch.object(safety,'_write',side_effect=crash),self.assertRaises(OSError):self.recover(enabled=True)
        (self.root/safety._RECOVERY_PENDING).unlink()  # Invent corrupt evidence on this temporary fixture only.
        with self.assertRaises(ValueError):self.recover(enabled=True)
        self.assertEqual(self.recover(enabled=True,abort=True)['status'],'aborted')

    def test_pending_completion_receipt_does_not_rewind_new_failures(self):
        self.prepare()
        with patch.object(safety,'_recovery_clear_marker',side_effect=OSError('synthetic')),self.assertRaises(OSError):self.recover(enabled=True)
        safety.record_supervisor_result(self.root,success=False,event_id='new',now=NOW+timedelta(seconds=2))
        with self.assertRaises(ValueError):self.recover(enabled=True)
        self.assertEqual(self.recover(enabled=True,abort=True)['status'],'aborted')
        current=safety._state(self.root,self.policy_value)
        self.assertEqual(current['supervisor']['event_id'],'new')
        self.assertFalse(current['supervisor']['success']);self.assertTrue(current['supervisor']['entry_halt'])
        self.assertEqual(self.recover(enabled=True)['status'],'already_aborted')

    def test_conflicting_marker_unknown_audit_and_capacity_fail_closed(self):
        for case in ('marker','unknown','capacity','link'):
            f=RecoveryTransitionTests();f.setUp();self.addCleanup(f.doCleanups);f.prepare()
            directory=f.root/'paper_supervisor_recovery';directory.mkdir()
            if case=='marker':(f.root/safety._RECOVERY_PENDING).write_text('{}')
            if case=='unknown':(directory/'unknown.json').write_text('{}')
            if case=='capacity':
                for n in range(32):(directory/(f'{n:032x}'+'.intent.json')).write_text('{}')
            if case=='link':
                import os
                os.link(f.root/'paper_safety_state.json',directory/('b'*32+'.intent.json'))
            before=(f.root/'paper_safety_state.json').read_bytes()
            with self.subTest(case=case),self.assertRaises(ValueError):f.recover(enabled=True)
            self.assertEqual((f.root/'paper_safety_state.json').read_bytes(),before)

    def test_health_failure_staleness_and_wrong_event_reject_without_mutation(self):
        for case in ('supervisor','worker','stale','event'):
            f=RecoveryTransitionTests();f.setUp();self.addCleanup(f.doCleanups);f.prepare()
            if case=='supervisor':safety.record_supervisor_result(f.root,success=False,event_id='new',now=NOW+timedelta(seconds=1))
            if case=='worker':safety.record_worker_result(f.root,success=False,now=NOW+timedelta(seconds=1))
            state=safety._state(f.root,f.policy_value);f.request['state_hash']=_hash(state)
            f.request['supervisor_event_id']=state['supervisor']['event_id'] if case!='event' else 'wrong'
            f.request['supervisor_observed_at']=state['supervisor']['last_seen']
            before=(f.root/'paper_safety_state.json').read_bytes()
            with self.subTest(case=case),self.assertRaises(ValueError):f.recover(enabled=True,now=NOW+timedelta(seconds=61 if case=='stale' else 2))
            self.assertEqual((f.root/'paper_safety_state.json').read_bytes(),before)

    def test_cli_recovery_is_explicit_bounded_and_redacted(self):
        from contextlib import redirect_stdout
        from hermes_trading import paper_supervisor_health as receiver
        for raw in (b'{"purpose":"secret-sentinel"}',b'{"schema_version":1,"schema_version":1}',b'x'*8193):
            out=io.StringIO()
            with redirect_stdout(out):
                result=receiver.main(['--root',str(self.root),'--recovery-base64',base64.b64encode(raw).decode()])
            self.assertEqual(result,2)
            self.assertEqual(json.loads(out.getvalue()),dict(status='rejected',error_kind='supervisor_recovery_rejected'))


class RecoveryPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_eight_wallet_preflight_rejections_then_checked_apply(self):
        from hermes_trading import paper_supervisor_health as receiver
        self.assertTrue(callable(getattr(receiver, 'receive_recovery', None)), 'native recovery receiver absent')
        from test_paper_boot import PaperBootTests
        from hermes_trading import paper_boot
        from hermes_trading.paired_worker import run_worker
        from hermes_trading.paired_inception import initialize_paired_inception
        self.module = lambda: paper_boot
        _, opening = PaperBootTests.ready_fixture(self)
        f = opening.fixture; root = f.root; primary = root/'adaptive_200'
        for key in ('wallet_required', 'model_access_renewal_required', 'supervisor_lifecycle_required'): f.policy[key] = True
        (primary/'reflection_owner.json').write_text(json.dumps(f.policy))
        access = json.loads((primary/'model_access.json').read_text()); access['policy_hash'] = _hash(f.policy)
        from hermes_trading.model_access_refresh import BASE, SUBSCRIPTION_URL
        access.update(verified_at=f.at.isoformat(),valid_until=(f.at+timedelta(minutes=5)).isoformat(),
            renewal=dict(kind='read_only_catalog_and_session',catalog_source=BASE+'/models',catalog_sha256='a'*64,
                pricing={'prompt':'0','completion':'0'},subscription_source=SUBSCRIPTION_URL,
                subscription_context='personal',subscription_current_is_null=True,inference_test_performed=False))
        (primary/'model_access.json').write_text(json.dumps(access))
        limits = json.loads((root/'worker_limits.json').read_text()); limits['max_cycles'] = 2
        (root/'worker_limits.json').write_text(json.dumps(limits))
        initialize_paired_inception(root, clock=lambda:f.at)
        f.at+=timedelta(seconds=1)
        for n in range(3):
            safety.record_supervisor_result(root, success=False, event_id=f'failure-{n}', now=f.at-timedelta(seconds=4-n))
        safety.record_supervisor_result(root, success=True, event_id='ready', now=f.at)
        env = dict(HERMES_TRADING_MODE='paper', HERMES_TRADING_ALLOW_REAL_ORDERS='false')
        observed = []
        async def inspect(seconds):
            if observed: return
            observed.append(True)
            pc, launch = self.pc_fixture(receiver, primary, f.at)
            evidence = receiver.prepare_recovery_pc_evidence(pc, launch, now=f.at)
            for path in (pc/'hermes_trading/paper_safety.py',launch.with_suffix('.py')):
                raw=path.read_bytes();path.write_bytes(raw+b'\n# synthetic mismatch\n')
                try:
                    with self.assertRaises(ValueError):receiver.prepare_recovery_pc_evidence(pc,launch,now=f.at)
                finally:path.write_bytes(raw)
            config=json.loads(launch.read_text());pc_state=Path(config['local_state_dir'])
            for marker in (pc/'state/watcher_maintenance.json',pc_state/'paper_supervisor_health_pending.json',pc_state/'supervisor_operator_stop.json'):
                marker.write_text('{}')
                try:
                    with self.assertRaises(ValueError):receiver.prepare_recovery_pc_evidence(pc,launch,now=f.at)
                finally:marker.unlink()
            request = receiver.build_recovery_request(root, approval_ref='synthetic-only', pc_evidence=evidence, now=f.at)
            state_before = (root/'paper_safety_state.json').read_bytes()
            for wrong_env in ({}, dict(env,HERMES_TRADING_MODE='live'), dict(env,HERMES_TRADING_ALLOW_REAL_ORDERS='true')):
                with self.assertRaises(ValueError): receiver.receive_recovery(root, request, enabled=True, env=wrong_env, now=f.at)
            with self.assertRaises(ValueError): receiver.receive_recovery(root, request, env=env, now=f.at)
            for field in ('state_hash','supervisor_event_id','experiment_id'):
                bad = copy.deepcopy(request); bad[field] = 'b'*64
                with self.subTest(field=field), self.assertRaises(ValueError):
                    receiver.receive_recovery(root, bad, enabled=True, env=env, now=f.at)
            for field in ('manifest_hash','opening_hash','safety_policy_hash','owner_hash','runtime_hash','supervisor_release_hash','selection_hash'):
                bad=copy.deepcopy(request);bad['bindings'][field]='b'*64
                with self.subTest(binding=field),self.assertRaises(ValueError):receiver.receive_recovery(root,bad,enabled=True,env=env,now=f.at)
            for field in ('runtime_hash','policy_hash','account_hash','release_hash'):
                bad=copy.deepcopy(request);bad['pc_evidence'][field]='b'*64
                with self.subTest(pc=field),self.assertRaises(ValueError):receiver.receive_recovery(root,bad,enabled=True,env=env,now=f.at)
            for relative, mutate in (
                ('adaptive_200/heartbeat.json', lambda v:v.update(mode='live')),
                ('adaptive_200/heartbeat.json', lambda v:v.update(model_ready=False)),
                ('adaptive_200/heartbeat.json', lambda v:v['worker_lifecycle'].update(daily_backup_error='failure')),
                ('adaptive_200/heartbeat.json', lambda v:v['worker_lifecycle'].update(storage_stop=True)),
                ('adaptive_200/heartbeat.json', lambda v:v['market_sources'].clear()),
                ('adaptive_200/model_access.json', lambda v:v.update(payment='paid_authorized')),
                ('control_50/wallet_account.json', lambda v:v.update(experiment_id='foreign')),
                ('manifest.json', lambda v:v.update(execution_enabled=False)),
            ):
                path = root/relative; original = path.read_bytes(); value=json.loads(original); mutate(value)
                path.write_text(json.dumps(value))
                try:
                    with self.subTest(relative=relative), self.assertRaises((ValueError, KeyError)):
                        receiver.receive_recovery(root, request, enabled=True, env=env, now=f.at)
                finally:path.write_bytes(original)
            # Reject changed operating policies even before a request is built.
            for relative in ('worker_limits.json','storage_limits.json'):
                path=root/relative;original=path.read_bytes();value=json.loads(original)
                key='max_cycles' if relative=='worker_limits.json' else 'max_state_bytes';value[key]+=1
                path.write_text(json.dumps(value))
                try:
                    with self.subTest(configuration=relative),self.assertRaises(ValueError):
                        receiver.build_recovery_request(root,approval_ref='synthetic',pc_evidence=evidence,now=f.at)
                finally:path.write_bytes(original)
            for relative in ('control_50/strategy.yaml','adaptive_200/goal.yaml'):
                import yaml
                path=root/relative;original=path.read_bytes();value=yaml.safe_load(original);value['unapproved']='changed'
                path.write_text(yaml.safe_dump(value))
                try:
                    with self.subTest(configuration=relative),self.assertRaises(ValueError):
                        receiver.build_recovery_request(root,approval_ref='synthetic',pc_evidence=evidence,now=f.at)
                finally:path.write_bytes(original)
            for marker in (root/'paper_stop.json', primary/'experiment_halt.json'):
                marker.write_text('{}')
                try:
                    with self.assertRaises(ValueError):receiver.receive_recovery(root, request, enabled=True, env=env, now=f.at)
                finally:marker.unlink()
            with closing(sqlite3.connect(primary/'paper_publications.sqlite',timeout=0,isolation_level=None)) as db:
                db.execute('BEGIN EXCLUSIVE')
                with self.assertRaises(sqlite3.OperationalError):receiver.receive_recovery(root,request,enabled=True,env=env,now=f.at)
                db.rollback()
                db.execute("UPDATE paired_batches SET status='pending'")
                try:
                    with self.assertRaises(ValueError):receiver.receive_recovery(root,request,enabled=True,env=env,now=f.at)
                finally:db.execute("UPDATE paired_batches SET status='complete'")
                risk=db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()[0]
                changed=json.loads(risk);changed['global_halted']=True
                db.execute('UPDATE paired_risk_state SET state=? WHERE id=1',(json.dumps(changed),))
                try:
                    with self.assertRaises(ValueError):receiver.receive_recovery(root,request,enabled=True,env=env,now=f.at)
                finally:db.execute('UPDATE paired_risk_state SET state=? WHERE id=1',(risk,))
            for table,status in (('positions','open'),('orders','submitted'),('incidents','open')):
                with closing(sqlite3.connect(root/'control_50/trading.db',isolation_level=None)) as db:
                    columns=db.execute('PRAGMA table_info('+table+')').fetchall()
                    names=[column[1] for column in columns]
                    values=['synthetic-recovery' if column[5] else status if column[1]=='status' else 0 if column[2]=='INTEGER' else '0' for column in columns]
                    db.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',values)
                    key=next(column[1] for column in columns if column[5])
                    try:
                        with self.subTest(table=table),self.assertRaises(ValueError):receiver.receive_recovery(root,request,enabled=True,env=env,now=f.at)
                    finally:db.execute('DELETE FROM '+table+' WHERE '+key+'=?',('synthetic-recovery',))
            self.assertEqual((root/'paper_safety_state.json').read_bytes(),state_before)
            original=safety._write;locked=[]
            def ensure_lease(root,value):
                with closing(sqlite3.connect(primary/'paper_publications.sqlite',timeout=0,isolation_level=None)) as db:
                    with self.assertRaises(sqlite3.OperationalError):db.execute('BEGIN EXCLUSIVE')
                locked.append(True);original(root,value)
            with patch.object(safety,'_write',side_effect=ensure_lease):
                result=receiver.receive_recovery(root, request, enabled=True, env=env, now=f.at)
            self.assertEqual(locked,[True])
            self.assertEqual(result['status'],'completed')
            self.assertEqual(receiver.receive_recovery(root, request, enabled=True, env=env, now=f.at)['status'],'already_completed')
        await run_worker(root, collector_factory=lambda:f.market, clock=lambda:f.at, sleep=inspect)
        self.assertEqual(observed,[True])

    def pc_fixture(self, receiver, primary, at):
        import shutil
        from hermes_trading.paired_primary import resolve_primary
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);project=Path(temp.name).resolve()
        source=Path(receiver.__file__).resolve().parent.parent
        release=receiver._recovery_release()
        for name in release['runtime']:
            dest=project/'hermes_trading'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/'hermes_trading'/name,dest)
        (project/'scripts').mkdir()
        for name in ('hermes_watch.py','native_hermes_watch.py'):shutil.copyfile(source/'scripts'/name,project/'scripts'/name)
        policy=json.loads((primary/'reflection_owner.json').read_text());account=json.loads((primary/'wallet_account.json').read_text())
        p=resolve_primary(json.loads((primary.parent/'manifest.json').read_text()))
        state=project/'state/paired-supervisor'/policy['experiment_id']/'adaptive_200';state.mkdir(parents=True)
        for name in ('reflection_owner.json','wallet_account.json'):shutil.copyfile(primary/name,state/name)
        limits=dict(schema_version=1,experiment_id=policy['experiment_id'],policy_hash=_hash(policy),release_hash=_hash(release),
            activated_at=account['activated_at'],max_cycles=100,max_runtime_seconds=86400,interval_seconds=60,max_model_attempts=1,
            primary_wallet_eur=200,primary_selection_sha256=p.selection_sha256,account_hash=_hash(account))
        (state/'supervisor_limits.json').write_text(json.dumps(limits))
        lifecycle=dict(schema_version=1,experiment_id=policy['experiment_id'],binding=_hash(dict(limits=limits,policy=policy,release=release)),
            deadline=(safety._time(account['activated_at'])+timedelta(days=1)).isoformat(),cycles_started=1,model_attempts_reserved=0,
            last_started_at=at.isoformat(),last_observed_at=at.isoformat(),status='running',stop_reason=None)
        (state/'supervisor_lifecycle.json').write_text(json.dumps(lifecycle))
        config=dict(schema_version=2,status='activated',enabled=True,experiment_id=policy['experiment_id'],project_dir=str(project),
            local_state_dir=str(state),mirror_dir=str(state.parent/'mirror'),remote_state_dir='/app/state/adaptive_200',
            policy_hash=_hash(policy),account_hash=_hash(account),release_hash=_hash(release),primary_wallet_eur=200,
            primary_selection_sha256=p.selection_sha256,expires_at=(safety._time(account['activated_at'])+timedelta(days=1)).isoformat(),
            wrapper_sha256=release['native_launcher'],max_script_seconds=240)
        launch=project/'scripts/native.json';launch.write_text(json.dumps(config))
        shutil.copyfile(project/'scripts/native_hermes_watch.py',launch.with_suffix('.py'))
        return project,launch
