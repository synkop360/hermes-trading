from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_global_reflection as fixtures
from hermes_trading.supervisor_sync import capture_startup_release, make_snapshot
from scripts import hermes_watch

NOW = fixtures.NOW


class SupervisedWatcherTests(unittest.TestCase):
    setUp = fixtures.GlobalReviewTests.setUp
    tearDown = fixtures.GlobalReviewTests.tearDown
    write_trades = fixtures.GlobalReviewTests.write_trades
    runner = fixtures.GlobalReviewTests.runner
    audit = fixtures.GlobalReviewTests.audit

    def configure_lifecycle(self,*,max_model_attempts=1,max_runtime_seconds=7200):
        from hermes_trading.reflection_supervision import _hash
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        limits={'schema_version':1,'experiment_id':policy['experiment_id'],'policy_hash':_hash(policy),
                'activated_at':fixtures.START.isoformat(),'max_cycles':10,'max_runtime_seconds':max_runtime_seconds,
                'interval_seconds':60,'max_model_attempts':max_model_attempts,
                'release_hash':_hash(hermes_watch._supervisor_release())}
        (self.state/'supervisor_limits.json').write_text(json.dumps(limits))

    def test_terminal_lifecycle_prevents_all_refresh_and_runner_side_effects(self):
        self.configure_lifecycle(max_runtime_seconds=3600)
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_refresh_supervised_access') as access, \
                patch.object(hermes_watch,'_sync_supervised_usage') as usage,patch.object(hermes_watch,'_remote_snapshot') as snapshot, \
                patch.object(hermes_watch,'_strict_runner') as runner:
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['status'],'supervisor_stopped')
        access.assert_not_called();usage.assert_not_called();snapshot.assert_not_called();runner.assert_not_called()

    def test_zero_model_allowance_blocks_due_runner_but_refreshes_evidence(self):
        self.configure_lifecycle(max_model_attempts=0);snapshot=self.snapshot()
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner') as runner,patch.object(hermes_watch,'_sync_to_mirror'):
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['status'],'supervisor_stopped');self.assertEqual(result['reason'],'model_attempt_budget');runner.assert_not_called()
        state=json.loads((self.state/'supervisor_lifecycle.json').read_text())
        self.assertEqual(state['stop_reason'],'model_attempt_budget')

    def test_failed_runner_construction_consumes_model_allowance(self):
        self.configure_lifecycle();snapshot=self.snapshot()
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner',side_effect=RuntimeError('failed')) as runner,patch.object(hermes_watch,'_sync_to_mirror'):
            with self.assertRaises(RuntimeError):hermes_watch._run_supervised_watch(now=NOW)
            result=hermes_watch._run_supervised_watch(now=NOW+timedelta(seconds=60))
        self.assertEqual(result['status'],'supervisor_stopped');self.assertEqual(runner.call_count,1)

    def test_direct_supervised_cycle_respects_maintenance(self):
        (self.state/'watcher_maintenance.json').write_text('{}')
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot') as snapshot:
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['status'],'maintenance')
        snapshot.assert_not_called();self.assertFalse((self.state/'watcher_cycle.sqlite').exists())

    def test_stop_arriving_during_snapshot_prevents_due_review(self):
        self._assert_marker_during_snapshot('supervisor_operator_stop.json')

    def test_maintenance_arriving_during_snapshot_prevents_due_review(self):
        self._assert_marker_during_snapshot('watcher_maintenance.json')

    def _assert_marker_during_snapshot(self,marker):
        self.configure_lifecycle();snapshot=self.snapshot()
        def transport():
            (self.state/marker).write_text(json.dumps({'experiment_id':'test'}))
            return snapshot
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',side_effect=transport), \
                patch.object(hermes_watch,'_strict_runner') as runner,patch.object(hermes_watch,'_publish_remote') as publication:
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['status'],'supervisor_stopped')
        runner.assert_not_called();publication.assert_not_called()

    def test_remote_transports_have_finite_timeouts(self):
        result=subprocess.CompletedProcess([],0,'{}','')
        with patch.object(hermes_watch,'_run',return_value=result) as run:
            hermes_watch._remote_snapshot();hermes_watch._publish_remote({});hermes_watch._install_remote_usage({})
        self.assertTrue(all(call.kwargs.get('timeout_seconds')==30 for call in run.call_args_list))

    def test_snapshot_timeout_releases_lease_and_preserves_cycle_count(self):
        self.configure_lifecycle();snapshot=self.snapshot()
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',side_effect=subprocess.TimeoutExpired([],30)), \
                patch.object(hermes_watch,'_strict_runner') as runner:
            with self.assertRaises(subprocess.TimeoutExpired):hermes_watch._run_supervised_watch(now=NOW)
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner') as runner,patch.object(hermes_watch,'_sync_to_mirror'):
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['reason'],'supervisor_interval')
        self.assertEqual(json.loads((self.state/'supervisor_lifecycle.json').read_text())['cycles_started'],1)
        runner.assert_not_called()

    def test_publication_timeout_preserves_pending_and_uses_no_model_allowance(self):
        self.configure_lifecycle(max_model_attempts=0);snapshot=self.snapshot()
        runner,_=self.runner({'outcome':'change','asset':'BTC/EUR','variable':'entry.threshold','value':31,'reason':'test'})
        from hermes_trading.global_reflection import run_global_review
        from hermes_trading.paper_publication import publish_paper_request
        run_global_review(self.state,runner,now=NOW)
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_publish_remote',side_effect=subprocess.TimeoutExpired([],30)),patch.object(hermes_watch,'_strict_runner') as factory:
            with self.assertRaises(subprocess.TimeoutExpired):hermes_watch._run_supervised_watch(now=NOW)
        factory.assert_not_called();self.assertEqual(self.audit()['decision']['status'],'awaiting_publication')
        def transport(request):return publish_paper_request(self.state,request,env={'HERMES_TRADING_MODE':'training'},now=NOW+timedelta(seconds=60))
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_publish_remote',side_effect=transport),patch.object(hermes_watch,'_strict_runner') as factory, \
                patch.object(hermes_watch,'_sync_to_mirror'):
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW+timedelta(seconds=60))['status'],'published')
        factory.assert_not_called()
        state=json.loads((self.state/'supervisor_lifecycle.json').read_text())
        self.assertEqual(state['model_attempts_reserved'],0);self.assertEqual(state['cycles_started'],2)

    def test_new_candidate_cost_receipt_precedes_publication(self):
        self._assert_cost_before_publication(already_pending=False)

    def test_pending_candidate_cost_receipt_precedes_publication(self):
        self._assert_cost_before_publication(already_pending=True)

    def test_new_candidate_final_cost_failure_prevents_publication(self):
        self._assert_cost_before_publication(already_pending=False, fail_final=True)

    def test_pending_candidate_final_cost_failure_prevents_publication(self):
        self._assert_cost_before_publication(already_pending=True, fail_final=True)

    def _assert_cost_before_publication(self, *, already_pending, fail_final=False):
        from hermes_trading.global_reflection import run_global_review
        from hermes_trading.paper_publication import publish_paper_request
        self.configure_lifecycle(max_model_attempts=0 if already_pending else 1)
        snapshot=self.snapshot(); original=(self.state/'strategy.yaml').read_bytes()
        runner,command=self.runner({'outcome':'change','asset':'BTC/EUR','variable':'entry.threshold','value':31,'reason':'test'})
        if already_pending:run_global_review(self.state,runner,now=NOW)
        events=[]
        @contextmanager
        def factory(*args,**kwargs):yield runner
        def usage(*args,**kwargs):
            events.append('usage')
            if fail_final and events.count('usage')==2:raise ValueError('final cost receipt rejected')
            return {'source_sha256':'a'*64}
        def publication(request):
            if not fail_final:self.assertEqual(events,['usage','usage'])
            events.append('publication')
            return publish_paper_request(self.state,request,env={'HERMES_TRADING_MODE':'training'},now=NOW)
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner',factory),patch.object(hermes_watch,'_sync_supervised_usage',side_effect=usage), \
                patch.object(hermes_watch,'_publish_remote',side_effect=publication) as publish,patch.object(hermes_watch,'_sync_to_mirror'):
            if fail_final:
                with self.assertRaisesRegex(ValueError,'final cost receipt'):hermes_watch._run_supervised_watch(now=NOW)
                publish.assert_not_called()
                self.assertEqual((self.state/'strategy.yaml').read_bytes(),original)
                self.assertEqual(self.audit()['decision']['status'],'awaiting_publication')
                review_id=self.audit()['review_id']
                events.clear();fail_final=False
                recovered=hermes_watch._run_supervised_watch(now=NOW+timedelta(seconds=60))
                self.assertEqual(recovered['status'],'published')
                self.assertEqual(recovered['review_id'],review_id)
                self.assertEqual(events,['usage','usage','publication'])
            else:
                result=hermes_watch._run_supervised_watch(now=NOW)
                self.assertEqual(result['status'],'published')
                self.assertEqual(result['usage_source_sha256'],'a'*64)
                self.assertEqual(events,['usage','usage','publication'])
        self.assertEqual(command.call_count,1)
        state=json.loads((self.state/'supervisor_lifecycle.json').read_text())
        self.assertEqual(state['model_attempts_reserved'],0 if already_pending else 1)

    def test_stop_during_final_cost_receipt_preserves_candidate(self):
        from hermes_trading.global_reflection import run_global_review
        self.configure_lifecycle(max_model_attempts=0);snapshot=self.snapshot()
        runner,command=self.runner({'outcome':'change','asset':'BTC/EUR','variable':'entry.threshold','value':31,'reason':'test'})
        run_global_review(self.state,runner,now=NOW)
        calls=[]
        def usage(*args,**kwargs):
            calls.append(True)
            if len(calls)==2:(self.state/'supervisor_operator_stop.json').write_text(json.dumps({'experiment_id':'test'}))
            return {'source_sha256':'a'*64}
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_sync_supervised_usage',side_effect=usage),patch.object(hermes_watch,'_publish_remote') as publish:
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['reason'],'operator_stop');publish.assert_not_called()
        self.assertEqual(self.audit()['decision']['status'],'awaiting_publication')
        self.assertEqual(command.call_count,1)

    def test_invocation_reserve_defers_due_review_without_consuming_watermark(self):
        from hermes_trading.supervisor_budget import InvocationBudget
        from hermes_trading.reflection_supervision import ReviewLease
        self.configure_lifecycle();snapshot=self.snapshot();clock=[100.0];usage_calls=[]
        budget=InvocationBudget(335,clock=lambda:clock[0])
        def access(*args,**kwargs):clock[0]+=40
        def usage(*args,**kwargs):
            clock[0]+=30;usage_calls.append(clock[0])
            return {'source_sha256':'a'*64}
        def transport():clock[0]+=70;return snapshot
        with budget.activate(),patch.object(hermes_watch,'STATE_DIR',self.state), \
                patch.object(hermes_watch,'_refresh_supervised_access',side_effect=access), \
                patch.object(hermes_watch,'_sync_supervised_usage',side_effect=usage), \
                patch.object(hermes_watch,'_remote_snapshot',side_effect=transport), \
                patch.object(hermes_watch,'_strict_runner') as runner,patch.object(hermes_watch,'_sync_to_mirror'):
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['status'],'deferred');self.assertEqual(result['reason'],'invocation_budget')
        self.assertEqual(result['usage_source_sha256'],'a'*64);self.assertEqual(len(usage_calls),2)
        runner.assert_not_called()
        with ReviewLease(self.state) as lease:
            self.assertIsNone(lease.pending_review())
            self.assertTrue(lease.due(hermes_watch._load_jsonl(self.state/'trades.jsonl'),now=NOW)['eligible'])
        self.assertEqual(json.loads((self.state/'supervisor_lifecycle.json').read_text())['model_attempts_reserved'],0)

    def test_invocation_reserve_keeps_candidate_pending_after_cost_transfer(self):
        from hermes_trading.global_reflection import run_global_review
        from hermes_trading.supervisor_budget import InvocationBudget
        self.configure_lifecycle(max_model_attempts=0);snapshot=self.snapshot()
        runner,command=self.runner({'outcome':'change','asset':'BTC/EUR','variable':'entry.threshold','value':31,'reason':'test'})
        run_global_review(self.state,runner,now=NOW)
        review_id=self.audit()['review_id'];original=(self.state/'strategy.yaml').read_bytes()
        clock=[100.0];budget=InvocationBudget(200,clock=lambda:clock[0]);calls=[]
        def usage(*args,**kwargs):
            calls.append(True)
            if len(calls)==2:clock[0]=170
            return {'source_sha256':'a'*64}
        with budget.activate(),patch.object(hermes_watch,'STATE_DIR',self.state), \
                patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_sync_supervised_usage',side_effect=usage), \
                patch.object(hermes_watch,'_publish_remote') as publish,patch.object(hermes_watch,'_sync_to_mirror'):
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['status'],'awaiting_publication');self.assertEqual(result['reason'],'invocation_budget')
        self.assertEqual(result['usage_source_sha256'],'a'*64);publish.assert_not_called()
        self.assertEqual(self.audit()['review_id'],review_id)
        self.assertEqual(self.audit()['decision']['status'],'awaiting_publication')
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),original);self.assertEqual(command.call_count,1)

    def test_deadline_crossing_snapshot_prevents_model_admission(self):
        self.configure_lifecycle(max_runtime_seconds=3601);snapshot=self.snapshot();current=[NOW]
        def transport():current[0]=NOW+timedelta(seconds=2);return snapshot
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_remote_snapshot',side_effect=transport), \
                patch('hermes_trading.reflection_supervision._now',side_effect=lambda value=None:current[0]), \
                patch.object(hermes_watch,'_strict_runner') as runner:
            result=hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(result['reason'],'deadline');runner.assert_not_called()

    def test_access_refresh_precedes_worker_snapshot_and_never_dispatches_on_bad_receipt(self):
        from hermes_trading.reflection_supervision import _hash
        from hermes_trading.model_access_refresh import install_free_access
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        policy['model']={'provider':'nous','name':'upstage/solar-mini4:free'};policy['model_access_renewal_required']=True
        (self.state/'reflection_owner.json').write_text(json.dumps(policy))
        account={'schema_version':1,'experiment_id':'test','currency':'EUR','activated_at':fixtures.START.isoformat(),'starting_balance':'100'}
        (self.state/'wallet_account.json').write_text(json.dumps(account))
        access={'schema_version':1,'provider':'nous','model':policy['model']['name'],'policy_hash':_hash(policy),
                'payment':'verified_free','provenance_ref':'initial-reviewed-diagnostic','verified_at':fixtures.START.isoformat(),
                'valid_until':NOW.isoformat(),'tools_allowed':False,'paid_calls_authorized':False}
        (self.state/'model_access.json').write_text(json.dumps(access))
        proof=dict(access,verified_at=NOW.isoformat(),valid_until=(NOW+timedelta(minutes=5)).isoformat(),
            renewal={'kind':'read_only_catalog_and_session','catalog_source':'https://inference-api.nousresearch.com/v1/models',
                'catalog_sha256':'a'*64,'pricing':{'prompt':'0','completion':'0'},'subscription_source':'https://portal.nousresearch.com/api/billing/subscription',
                'subscription_context':'personal','subscription_current_is_null':True,'inference_test_performed':False})
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_probe_model_access',return_value=proof), \
                patch.object(hermes_watch,'_install_remote_access',return_value={'status':'renewed','access_hash':'wrong'}), \
                patch.object(hermes_watch,'_remote_snapshot') as snapshot,patch.object(hermes_watch,'_strict_runner') as runner:
            with self.assertRaises(ValueError):hermes_watch._run_supervised_watch(now=NOW)
            snapshot.assert_not_called();runner.assert_not_called()
        self.assertEqual(json.loads((self.state/'model_access.json').read_text()),access)
        with tempfile.TemporaryDirectory() as tmp:
            remote=Path(tmp)
            for name in ('reflection_owner.json','wallet_account.json','model_access.json'):
                (remote/name).write_bytes((self.state/name).read_bytes())
            def transport(request):return install_free_access(remote,request,env={'HERMES_TRADING_MODE':'training'},now=NOW)
            def snapshot():
                self.assertEqual(json.loads((remote/'model_access.json').read_text()),proof)
                raise ValueError('snapshot not yet ready')
            with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_probe_model_access',return_value=proof), \
                    patch.object(hermes_watch,'_install_remote_access',transport),patch.object(hermes_watch,'_remote_snapshot',side_effect=snapshot):
                with self.assertRaisesRegex(ValueError,'snapshot'):hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual(json.loads((self.state/'model_access.json').read_text()),proof)

    def configure_usage(self):
        from hermes_trading.reflection_supervision import _hash
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        account={'schema_version':1,'experiment_id':'test','currency':'EUR','starting_balance':'100','activated_at':fixtures.START.isoformat()}
        (self.state/'wallet_account.json').write_text(json.dumps(account))
        (self.state/'wallet_cost_sources.json').write_text(json.dumps({'schema_version':1,'experiment_id':'test','currency':'EUR',
            'scope_start':fixtures.START.isoformat(),'categories':{'ai':{'mode':'supervised','source':'supervised_ai_with_subscription_v1',
                'policy_hash':_hash(policy),'subscription':{'daily_rate':None,'quality':'missing','source':'unknown_subscription_allocation'}}}}))
        return policy,account

    def test_cost_feed_bootstraps_before_unhealthy_snapshot_without_model_call(self):
        policy,account=self.configure_usage()
        from hermes_trading.supervisor_usage_feed import install_supervisor_usage
        with tempfile.TemporaryDirectory() as tmp:
            remote=Path(tmp)
            for name in ('wallet_account.json','wallet_cost_sources.json','reflection_owner.json'):
                (remote/name).write_bytes((self.state/name).read_bytes())
            def transport(request):return install_supervisor_usage(remote,request,env={'HERMES_TRADING_MODE':'training'},now=NOW)
            def snapshot():
                self.assertTrue((remote/'supervisor_usage_snapshot.json').exists())
                raise ValueError('wallet not yet healthy')
            with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_install_remote_usage',transport), \
                    patch.object(hermes_watch,'_remote_snapshot',side_effect=snapshot),patch.object(hermes_watch,'_strict_runner') as runner:
                with self.assertRaisesRegex(ValueError,'healthy'):hermes_watch._run_supervised_watch(now=NOW)
                runner.assert_not_called()

    def test_usage_transport_receipt_must_match_exported_source(self):
        policy,_=self.configure_usage()
        with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_install_remote_usage',return_value={'status':'imported','source_sha256':'wrong'}):
            with self.assertRaises(ValueError):hermes_watch._sync_supervised_usage(policy,now=NOW)

    def test_completed_review_cost_is_transferred_after_dispatch(self):
        from datetime import datetime
        from hermes_trading.costs import CostLedger
        from hermes_trading.reflection_supervision import _hash
        from hermes_trading.supervisor_usage_feed import install_supervisor_usage
        self.configure_usage()
        report={'status':'observed','experiment_id':'test','observed_at':NOW.isoformat()}
        (self.state/'wallet_metrics.json').write_text(json.dumps(report))
        snapshot=self.snapshot();runner,command=self.runner({'outcome':'no_change','reason':'test'})
        runner.cost_ledger=CostLedger(self.state)
        @contextmanager
        def factory(*args,**kwargs):yield runner
        original=hermes_watch._sync_supervised_usage;times=iter((NOW,NOW+timedelta(seconds=2)))
        def sync(policy,**kwargs):return original(policy,now=next(times))
        class CostClock(datetime):
            @classmethod
            def now(cls,tz=None):return NOW+timedelta(seconds=1)
        with tempfile.TemporaryDirectory() as tmp:
            remote=Path(tmp)
            for name in ('wallet_account.json','wallet_cost_sources.json','reflection_owner.json'):
                (remote/name).write_bytes((self.state/name).read_bytes())
            def transport(request):return install_supervisor_usage(remote,request,env={'HERMES_TRADING_MODE':'training'},
                now=datetime.fromisoformat(request['snapshot']['covered_through']))
            with patch.object(hermes_watch,'STATE_DIR',self.state),patch.object(hermes_watch,'_install_remote_usage',transport), \
                    patch.object(hermes_watch,'_sync_supervised_usage',side_effect=sync),patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                    patch.object(hermes_watch,'_strict_runner',factory),patch.object(hermes_watch,'_sync_to_mirror'),patch('hermes_trading.costs.datetime',CostClock):
                result=hermes_watch._run_supervised_watch(now=NOW+timedelta(seconds=1))
            source=json.loads((remote/'supervisor_usage_snapshot.json').read_text())
            self.assertEqual(result['status'],'no_change')
            self.assertEqual(result['usage_source_sha256'],_hash(source))
            self.assertEqual(len(source['model_usage']['events']),1)
            self.assertIsNone(source['model_usage']['events'][0]['amount'])
            self.assertEqual(command.call_count,1)

    def snapshot(self):
        heartbeat={'timestamp': NOW.isoformat(), 'mode': 'training',
            'execution': 'simulated', 'status': 'ok', 'strategy_version': '01',
            'worker_release': capture_startup_release(self.state, now=fixtures.START)}
        if (self.state/'wallet_metrics.json').exists():
            from hermes_trading.reflection_supervision import _hash
            heartbeat['wallet_report_hash']=_hash(json.loads((self.state/'wallet_metrics.json').read_text()))
        (self.state/'heartbeat.json').write_text(json.dumps(heartbeat))
        return make_snapshot(self.state, now=NOW)

    def test_external_main_uses_supervised_branch_without_legacy_sync_or_upload(self):
        with patch.object(hermes_watch,'STATE_DIR',self.state), patch.object(hermes_watch,'_load_dotenv'), \
                patch.object(hermes_watch,'_run_supervised_watch',return_value={'status':'not_due'}) as run, \
                patch.object(hermes_watch,'_download_remote_file') as download, patch.object(hermes_watch,'_upload_remote_file') as upload:
            self.assertEqual(hermes_watch.main(),0)
        run.assert_called_once()
        download.assert_not_called(); upload.assert_not_called()

    def test_no_change_cycle_makes_one_global_call_and_no_upload(self):
        snapshot=self.snapshot(); runner, command=self.runner({'outcome':'no_change','reason':'test'})
        @contextmanager
        def factory(*args,**kwargs): yield runner
        with patch.object(hermes_watch,'STATE_DIR',self.state), patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner',factory), patch.object(hermes_watch,'_sync_to_mirror'), \
                patch.object(hermes_watch,'_upload_remote_file') as upload:
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['status'],'no_change')
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['status'],'not_due')
        self.assertEqual(command.call_count,1); upload.assert_not_called()

    def test_invalid_snapshot_cannot_overwrite_state_or_open_model_runner(self):
        snapshot=self.snapshot(); snapshot['disk_release']={}
        original=(self.state/'strategy.yaml').read_bytes()
        with patch.object(hermes_watch,'STATE_DIR',self.state), patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_strict_runner') as runner:
            with self.assertRaises(ValueError): hermes_watch._run_supervised_watch(now=NOW)
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),original); runner.assert_not_called()

    def test_pending_publication_is_retried_before_opening_runner(self):
        self.configure_lifecycle(max_model_attempts=0)
        snapshot=self.snapshot(); runner,_=self.runner({'outcome':'change','asset':'BTC/EUR',
            'variable':'entry.threshold','value':31,'reason':'test'})
        from hermes_trading.global_reflection import run_global_review
        run_global_review(self.state,runner,now=NOW)
        from hermes_trading.paper_publication import publish_paper_request
        def transport(request): return publish_paper_request(self.state,request,env={'HERMES_TRADING_MODE':'training'},now=NOW)
        with patch.object(hermes_watch,'STATE_DIR',self.state), patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                patch.object(hermes_watch,'_publish_remote',transport), patch.object(hermes_watch,'_strict_runner') as factory, \
                patch.object(hermes_watch,'_sync_to_mirror'):
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['status'],'published')
        factory.assert_not_called(); self.assertEqual(self.audit()['outcome'],'changed')
        self.assertEqual(json.loads((self.state/'supervisor_lifecycle.json').read_text())['model_attempts_reserved'],0)

    def test_missing_model_access_configuration_blocks_before_dispatch(self):
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        with patch.object(hermes_watch,'STATE_DIR',self.state), patch('hermes_trading.model_runner.subprocess.run') as command:
            with self.assertRaises(ValueError):
                with hermes_watch._strict_runner(policy,now=NOW): pass
        command.assert_not_called()

    def test_explicit_access_binds_pin_payment_evidence_and_expiry(self):
        from hermes_trading.reflection_supervision import _hash
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        access={'schema_version':1,'policy_hash':_hash(policy),'provider':'test','model':'fixed-v1',
                'valid_until':(NOW+timedelta(hours=1)).isoformat(),'payment':'verified_free','provenance_ref':'test fixture'}
        with patch.object(hermes_watch,'STATE_DIR',self.state):
            for change in ({'valid_until':NOW.isoformat()},{'model':'other'},{'payment':'paid_authorized'},
                           {'provenance_ref':''},{'schema_version':True}):
                (self.state/'model_access.json').write_text(json.dumps({**access,**change}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    with hermes_watch._strict_runner(policy,now=NOW): pass
                self.assertFalse((self.state/'llm_providers.sqlite').exists())
            (self.state/'model_access.json').write_text(json.dumps(access))
            with hermes_watch._strict_runner(policy,now=NOW) as runner:
                self.assertTrue(runner.strict_pinned); self.assertFalse(runner.paid_calls_authorized)
                self.assertIsNone(runner.local_run); self.assertIsNone(runner.openai_run)
                self.assertEqual(runner.cost_ledger.state_dir,self.state)

    def test_competing_cycle_cannot_sync_during_model_review(self):
        import sqlite3
        snapshot=self.snapshot(); runner,command=self.runner({'outcome':'no_change','reason':'test'})
        @contextmanager
        def factory(*args,**kwargs): yield runner
        def dispatch(command,**kwargs):
            with self.assertRaises(sqlite3.OperationalError): hermes_watch._run_supervised_watch(now=NOW)
            return subprocess.CompletedProcess(command,0,'{"outcome":"no_change","reason":"test"}','')
        command.side_effect=dispatch
        with patch.object(hermes_watch,'STATE_DIR',self.state), patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot) as sync, \
                patch.object(hermes_watch,'_strict_runner',factory), patch.object(hermes_watch,'_sync_to_mirror'):
            self.assertEqual(hermes_watch._run_supervised_watch(now=NOW)['status'],'no_change')
        self.assertEqual(sync.call_count,1)

    def test_separate_remote_publication_marks_old_mirror_as_awaiting_refresh(self):
        snapshot=self.snapshot(); runner,_=self.runner({'outcome':'change','asset':'BTC/EUR',
            'variable':'entry.threshold','value':31,'reason':'test'})
        from hermes_trading.paper_publication import publish_paper_request
        @contextmanager
        def factory(*args,**kwargs): yield runner
        with tempfile.TemporaryDirectory() as tmp:
            worker=Path(tmp)
            for name in ('strategy.yaml','goal.yaml','reflection_owner.json'):
                (worker/name).write_bytes((self.state/name).read_bytes())
            def transport(request): return publish_paper_request(worker,request,env={'HERMES_TRADING_MODE':'training'},now=NOW)
            with patch.object(hermes_watch,'STATE_DIR',self.state), patch.object(hermes_watch,'_remote_snapshot',return_value=snapshot), \
                    patch.object(hermes_watch,'_publish_remote',transport), patch.object(hermes_watch,'_strict_runner',factory), \
                    patch.object(hermes_watch,'_sync_to_mirror'):
                result=hermes_watch._run_supervised_watch(now=NOW)
            self.assertEqual(result['status'],'published')
            self.assertEqual(result['mirror_state'],'awaiting_worker_refresh')
            self.assertTrue(json.loads((self.state/'supervisor_status.json').read_text())['refresh_required'])
            self.assertNotEqual((worker/'strategy.yaml').read_bytes(),(self.state/'strategy.yaml').read_bytes())

    def test_fresh_post_publication_snapshot_refreshes_separate_mirror(self):
        self.snapshot(); runner,command=self.runner({'outcome':'change','asset':'BTC/EUR',
            'variable':'entry.threshold','value':31,'reason':'test'})
        from hermes_trading.paper_publication import publish_paper_request
        @contextmanager
        def factory(*args,**kwargs): yield runner
        with tempfile.TemporaryDirectory() as tmp:
            worker=Path(tmp)
            for name in ('strategy.yaml','goal.yaml','reflection_owner.json','heartbeat.json','trades.jsonl'):
                (worker/name).write_bytes((self.state/name).read_bytes())
            def transport(request):
                receipt=publish_paper_request(worker,request,env={'HERMES_TRADING_MODE':'training'},now=NOW)
                heartbeat=json.loads((worker/'heartbeat.json').read_text())
                heartbeat['strategy_version']='02'
                (worker/'heartbeat.json').write_text(json.dumps(heartbeat))
                return receipt
            with patch.object(hermes_watch,'STATE_DIR',self.state), \
                    patch.object(hermes_watch,'_remote_snapshot',side_effect=lambda: make_snapshot(worker,now=NOW)), \
                    patch.object(hermes_watch,'_publish_remote',transport), patch.object(hermes_watch,'_strict_runner',factory), \
                    patch.object(hermes_watch,'_sync_to_mirror'):
                result=hermes_watch._run_supervised_watch(now=NOW)
            self.assertEqual(result['mirror_state'],'current')
            self.assertEqual((worker/'strategy.yaml').read_bytes(),(self.state/'strategy.yaml').read_bytes())
            self.assertFalse(json.loads((self.state/'supervisor_status.json').read_text())['refresh_required'])
            self.assertEqual(command.call_count,1)

    def test_publication_transport_passes_json_on_stdin_and_rejects_gateway_payload(self):
        with patch.object(hermes_watch,'_run',return_value=subprocess.CompletedProcess([],0,'{"status":"published"}','')) as run:
            self.assertEqual(hermes_watch._publish_remote({'review_id':'test'})['status'],'published')
        self.assertIn('hermes_trading.paper_publication',run.call_args.args[0][-1])
        self.assertEqual(json.loads(run.call_args.kwargs['input_text']),{'review_id':'test'})
        with patch.object(hermes_watch,'_run',return_value=subprocess.CompletedProcess([],0,'{"status":"ready","account":{}}','')):
            with self.assertRaises(ValueError): hermes_watch._publish_remote({})


if __name__=='__main__': unittest.main()
