from datetime import timedelta
from decimal import Decimal
from dataclasses import replace
import base64,hashlib,json,subprocess,unittest
from unittest.mock import patch
import test_global_reflection as fixtures
from hermes_trading.costs import CostEvent,CostLedger
from hermes_trading.global_reflection import run_global_review
from hermes_trading.reflection_supervision import ReviewLease,_hash
from hermes_trading.supervisor_usage_source import export_supervisor_usage

START=fixtures.START
NOW=fixtures.NOW


class UsageSourceTests(unittest.TestCase):
    tearDown=fixtures.GlobalReviewTests.tearDown
    audit=fixtures.GlobalReviewTests.audit
    write_trades=fixtures.GlobalReviewTests.write_trades

    def setUp(self):
        fixtures.GlobalReviewTests.setUp(self)
        from hermes_trading.reflection_supervision import initialize_supervisor
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        policy['model']={'provider':'nous','name':'upstage/solar-mini4:free'}
        (self.state/'reflection_owner.json').write_text(json.dumps(policy))
        (self.state/'llm_review_state.sqlite').unlink()
        initialize_supervisor(self.state,now=START)

    def runner(self,output):
        from hermes_trading.providers import ModelCandidate
        runner,command=fixtures.GlobalReviewTests.runner(self,output)
        runner.pinned_candidate=ModelCandidate('nous','upstage/solar-mini4:free',True,'test')
        return runner,command

    def account(self,start=START):
        return {'schema_version':1,'experiment_id':json.loads((self.state/'reflection_owner.json').read_text())['experiment_id'],'currency':'EUR','activated_at':start.isoformat()}

    def receipt(self,review):
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        response={'model':policy['model']['name'],'usage':{'prompt_tokens':10,'completion_tokens':2}}
        raw=json.dumps(response).encode()
        return CostEvent('hermes',Decimal('0'),'USD','actual','hermes_usage',NOW,
            metadata={'provider':policy['model']['provider'],'model':policy['model']['name'],
                'experiment_id':policy['experiment_id'],'policy_hash':_hash(policy),'review_id':review['review_id'],
                'request_started_at':review['started_at'],'input_tokens':10,'output_tokens':2,
                'pricing_verification':{'source':'https://inference-api.nousresearch.com/v1/models','model':policy['model']['name'],
                    'verified_at':(NOW-timedelta(seconds=1)).isoformat(),'payment':'verified_zero_catalog_price',
                    'catalog_sha256':'a'*64,'pricing':{'prompt':'0','completion':'0'}},
                'raw_http_response_b64':base64.b64encode(raw).decode(),'raw_http_sha256':hashlib.sha256(raw).hexdigest()})

    def reviewed(self):
        runner,_=self.runner({'outcome':'no_change','reason':'test'})
        run_global_review(self.state,runner,now=NOW)
        return self.audit()

    def snapshot(self):return export_supervisor_usage(self.state,self.account(),now=NOW+timedelta(seconds=1))

    def deferred_review(self):
        from hermes_trading.supervisor_budget import InvocationBudget
        from hermes_trading.reflection_supervision import ReviewLease
        from unittest.mock import patch
        clock=[100.0];runner,command=self.runner({'outcome':'no_change','reason':'test'})
        runner.cost_ledger=CostLedger(self.state)
        original=ReviewLease.record_material
        def slow(lease,*args,**kwargs):
            result=original(lease,*args,**kwargs)
            if kwargs.get('request') is not None:clock[0]+=50
            return result
        with InvocationBudget(200,clock=lambda:clock[0]).activate(),patch.object(ReviewLease,'record_material',slow):
            result=run_global_review(self.state,runner,now=NOW)
        self.assertEqual(result['status'],'deferred');command.assert_not_called()
        return self.audit()

    def test_verified_no_dispatch_deferral_needs_no_model_usage_receipt(self):
        self.deferred_review()
        source=self.snapshot()
        self.assertEqual(source['model_usage']['events'],[])
        self.assertEqual(source['covered_through'],(NOW+timedelta(seconds=1)).isoformat())

    def test_empty_usage_file_cleanup_failure_preserves_no_dispatch_proof(self):
        from unittest.mock import patch
        from pathlib import Path
        original=Path.unlink;retained=[]
        def fail_usage_unlink(path,*args,**kwargs):
            if path.name.startswith('hermes-usage-'):
                retained.append(path);raise PermissionError('test empty receipt locked')
            return original(path,*args,**kwargs)
        try:
            with patch.object(Path,'unlink',fail_usage_unlink):review=self.deferred_review()
            self.assertEqual(self.snapshot()['model_usage']['events'],[])
            self.assertIn('cleanup failed',review['deferred_detail'])
            self.assertEqual(len(retained),1)
            self.assertEqual(retained[0].stat().st_size,0)
        finally:
            for path in retained:original(path,missing_ok=True)

    def test_deferred_review_cannot_hide_actual_model_usage(self):
        review=self.deferred_review()
        CostLedger(self.state).record(self.receipt(review))
        with self.assertRaisesRegex(ValueError,'undispatched'):self.snapshot()

    def test_deferred_review_cannot_hide_response_or_integer_dispatch_flag(self):
        from hermes_trading.reflection_supervision import ReviewLease
        review=self.deferred_review()
        for altered in (dict(review,response={'output':'model answer'}),
                dict(review,decision=dict(review['decision'],model_dispatched=0))):
            with ReviewLease(self.state) as lease:
                lease.db.execute('UPDATE reviews SET record=? WHERE review_id=?',(json.dumps(altered),review['review_id']))
                lease.db.commit()
            with self.assertRaisesRegex(ValueError,'undispatched'):self.snapshot()

    def test_empty_initialized_scope_is_explicit_covered_zero(self):
        result=self.snapshot()
        self.assertEqual(result['model_usage']['events'],[])
        self.assertEqual(result['scope_start'],START.isoformat())
        self.assertTrue((self.state/'supervisor_usage_snapshot.json').exists())

    def test_inception_must_match_supervisor(self):
        with self.assertRaisesRegex(ValueError,'inception'):
            export_supervisor_usage(self.state,self.account(START+timedelta(seconds=1)),now=NOW)

    def test_verified_zero_receipt_exports_zero_with_provenance(self):
        review=self.reviewed();CostLedger(self.state).record(self.receipt(review))
        result=self.snapshot();event=result['model_usage']['events'][0]
        self.assertEqual((event['amount'],event['currency'],event['quality']),('0','EUR','actual'))
        self.assertEqual(event['id'],'review:'+review['review_id'])
        self.assertNotIn('raw_http_response_b64',json.dumps(result))

    def test_verified_export_import_and_wallet_cost_producer_integration(self):
        from pathlib import Path
        import tempfile
        from hermes_trading.supervisor_usage_feed import build_usage_request,install_supervisor_usage
        from hermes_trading.wallet_cost_accrual import refresh_wallet_costs
        review=self.reviewed();CostLedger(self.state).record(self.receipt(review))
        source=export_supervisor_usage(self.state,self.account(),now=NOW)
        policy=json.loads((self.state/'reflection_owner.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            remote=Path(tmp)
            (remote/'reflection_owner.json').write_text(json.dumps(policy))
            (remote/'wallet_account.json').write_text(json.dumps(self.account()))
            (remote/'wallet_cost_sources.json').write_text(json.dumps({'schema_version':1,'experiment_id':policy['experiment_id'],'currency':'EUR',
                'scope_start':START.isoformat(),'categories':{'hosting':{'mode':'recurring','daily_rate':'0','quality':'estimated','source':'test'},
                    'tools':{'mode':'recurring','daily_rate':'0','quality':'estimated','source':'test'},
                    'funding':{'mode':'spot_zero','quality':'actual','source':'unleveraged_spot_no_borrowing'},
                    'ai':{'mode':'supervised','source':'supervised_ai_with_subscription_v1','policy_hash':_hash(policy),
                          'subscription':{'daily_rate':'.24','quality':'estimated','source':'test_full_allocation'}}}}))
            install_supervisor_usage(remote,build_usage_request(source,policy,self.account()),env={'HERMES_TRADING_MODE':'training'},now=NOW)
            result=refresh_wallet_costs(remote,now=NOW)
            self.assertEqual(Decimal(result['totals']['ai']['amount']),Decimal('.01'))
            self.assertEqual(result['supervisor_usage_hash'],_hash(source))

    def test_no_cost_receipt_cannot_become_zero(self):
        self.reviewed()
        with self.assertRaisesRegex(ValueError,'receipt'):self.snapshot()

    def test_false_free_claim_stays_unpriced(self):
        review=self.reviewed();event=self.receipt(review)
        event=replace(event,metadata=dict(event.metadata,pricing_verification={}))
        CostLedger(self.state).record(event)
        row=self.snapshot()['model_usage']['events'][0]
        self.assertIsNone(row['amount']);self.assertEqual(row['quality'],'missing')

    def test_usd_charge_requires_fx_and_is_not_zero(self):
        review=self.reviewed();CostLedger(self.state).record(replace(self.receipt(review),amount=Decimal('1.25')))
        row=self.snapshot()['model_usage']['events'][0]
        self.assertIsNone(row['amount']);self.assertEqual(row['original_amount'],'1.25')

    def test_orphan_or_duplicate_usage_rejected(self):
        review=self.reviewed();event=self.receipt(review)
        CostLedger(self.state).record(event);CostLedger(self.state).record(event)
        with self.assertRaisesRegex(ValueError,'duplicate'):self.snapshot()

    def test_pending_uncertain_request_blocks_coverage(self):
        with ReviewLease(self.state) as lease:lease.prepare(self.rows,now=NOW)
        with self.assertRaisesRegex(ValueError,'receipt|pending'):self.snapshot()

    def test_historical_unpriced_usage_is_not_charged_to_new_scope(self):
        CostLedger(self.state).record(CostEvent('openai',None,'USD','missing','legacy',START-timedelta(seconds=1)))
        self.assertEqual(self.snapshot()['model_usage']['events'],[])

    def test_second_writer_cannot_enter_while_snapshot_publishes(self):
        from hermes_trading import supervisor_usage_source
        original=supervisor_usage_source._temporary
        def inspect(*args,**kwargs):
            from hermes_trading.reflection_supervision import ReviewBusy
            with self.assertRaises(ReviewBusy):
                with ReviewLease(self.state):pass
            return original(*args,**kwargs)
        with patch.object(supervisor_usage_source,'_temporary',side_effect=inspect):self.snapshot()

    def test_global_runner_attributes_usage_and_restores_ledger(self):
        runner,command=self.runner({'outcome':'no_change','reason':'test'})
        ledger=CostLedger(self.state);runner.cost_ledger=ledger
        def invoke(args,**kwargs):
            from pathlib import Path
            Path(args[args.index('--usage-file')+1]).write_text(json.dumps({'input_tokens':10,'output_tokens':2}))
            return subprocess.CompletedProcess(args,0,json.dumps({'outcome':'no_change','reason':'test'}),'')
        command.side_effect=invoke
        result=run_global_review(self.state,runner,now=NOW)
        event=json.loads((self.state/'cost_events.jsonl').read_text())
        self.assertEqual(event['metadata']['review_id'],result['review_id'])
        self.assertEqual(event['metadata']['experiment_id'],json.loads((self.state/'reflection_owner.json').read_text())['experiment_id'])
        self.assertIs(runner.cost_ledger,ledger)

    def test_snapshot_time_cannot_regress(self):
        self.snapshot()
        with self.assertRaisesRegex(ValueError,'regress'):
            export_supervisor_usage(self.state,self.account(),now=NOW)

    def test_late_review_receipt_cannot_rewrite_covered_zero(self):
        self.snapshot()
        review=self.reviewed();CostLedger(self.state).record(self.receipt(review))
        with self.assertRaisesRegex(ValueError,'coverage|backdated'):
            export_supervisor_usage(self.state,self.account(),now=NOW+timedelta(seconds=2))

    def test_repeatable_frozen_snapshot_and_projection_recovery(self):
        from hermes_trading import supervisor_usage_source
        with patch.object(supervisor_usage_source,'_temporary',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.snapshot()
        first=self.snapshot();self.assertEqual(self.snapshot(),first)

    def test_failure_restores_runner_ledger(self):
        runner,command=self.runner({'outcome':'no_change','reason':'test'})
        ledger=CostLedger(self.state);runner.cost_ledger=ledger
        command.side_effect=subprocess.CalledProcessError(75,[],output='',stderr='uncertain')
        from hermes_trading.model_runner import ModelResponseCaptureError
        with self.assertRaises(ModelResponseCaptureError):run_global_review(self.state,runner,now=NOW)
        self.assertIs(runner.cost_ledger,ledger)

    def test_completed_change_awaiting_publication_can_export_receipt(self):
        runner,_=self.runner({'outcome':'change','asset':'BTC/EUR','variable':'entry.threshold','value':31,'reason':'test'})
        result=run_global_review(self.state,runner,now=NOW)
        self.assertEqual(result['status'],'awaiting_publication')
        CostLedger(self.state).record(self.receipt(self.audit()))
        self.assertEqual(self.snapshot()['model_usage']['events'][0]['amount'],'0')

    def test_invalid_raw_usage_cannot_prove_zero(self):
        review=self.reviewed();event=self.receipt(review)
        raw=json.dumps({'model':event.metadata['model'],'usage':{'prompt_tokens':10.0,'completion_tokens':2}}).encode()
        event=replace(event,metadata=dict(event.metadata,raw_http_response_b64=base64.b64encode(raw).decode(),raw_http_sha256=hashlib.sha256(raw).hexdigest()))
        CostLedger(self.state).record(event)
        self.assertIsNone(self.snapshot()['model_usage']['events'][0]['amount'])

    def test_missing_pending_audit_cannot_claim_empty_coverage(self):
        with ReviewLease(self.state) as lease:
            lease.prepare(self.rows,now=NOW)
            lease.db.execute('DELETE FROM reviews');lease.db.commit()
        with self.assertRaisesRegex(ValueError,'audit|pending'):self.snapshot()

    def test_missing_completed_audit_cannot_claim_empty_coverage(self):
        self.reviewed()
        with ReviewLease(self.state) as lease:
            lease.db.execute('DELETE FROM reviews');lease.db.commit()
        with self.assertRaisesRegex(ValueError,'audit|completed'):self.snapshot()

    def test_orphan_usage_is_not_silently_ignored(self):
        event=self.receipt({'review_id':'untracked','started_at':NOW.isoformat()})
        CostLedger(self.state).record(event)
        with self.assertRaisesRegex(ValueError,'orphan'):self.snapshot()

    def test_seen_receipt_cannot_be_rewritten(self):
        review=self.reviewed();event=self.receipt(review);CostLedger(self.state).record(event)
        self.snapshot()
        (self.state/'cost_events.jsonl').write_text(json.dumps(dict(json.loads((self.state/'cost_events.jsonl').read_text()),amount='1'))+'\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            export_supervisor_usage(self.state,self.account(),now=NOW+timedelta(seconds=2))

