from datetime import datetime,timedelta,timezone
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
import json,tempfile,unittest

from hermes_trading.wallet_cost_accrual import refresh_wallet_costs


class CostAccrualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.start=datetime(2026,10,10,tzinfo=timezone.utc);self.now=self.start+timedelta(days=1)
        self.account={'schema_version':1,'experiment_id':'exp','currency':'EUR','starting_balance':'100','activated_at':self.start.isoformat()}
        (self.path/'wallet_account.json').write_text(json.dumps(self.account))
        self.sources={'schema_version':1,'experiment_id':'exp','currency':'EUR','scope_start':self.start.isoformat(),
            'categories':{'hosting':{'mode':'recurring','daily_rate':'0.2','source':'declared_estimate','quality':'estimated'},
                          'tools':{'mode':'recurring','daily_rate':'0','source':'verified_no_paid_tools','quality':'estimated'},
                          'funding':{'mode':'spot_zero','source':'unleveraged_spot_no_borrowing','quality':'actual'},
                          'ai':{'mode':'events','source':'complete_usage_sensor','coverage_start':self.start.isoformat(),'coverage_end':self.now.isoformat(),'events':[]}}}
        self.write()
    def tearDown(self):self.tmp.cleanup()
    def write(self):(self.path/'wallet_cost_sources.json').write_text(json.dumps(self.sources))
    def refresh(self,now=None):return refresh_wallet_costs(self.path,now=now or self.now)

    def supervised_source(self,subscription='0.12',amount='0.03'):
        self.sources['categories']['ai']={'mode':'supervised','source':'supervised_ai_with_subscription_v1',
            'policy_hash':'a'*64,'subscription':{'daily_rate':subscription,'quality':'estimated' if subscription is not None else 'missing',
                'source':'declared_standalone_subscription_allocation'}}
        self.write()
        return {'schema_version':1,'experiment_id':'exp','policy_hash':'a'*64,'currency':'EUR',
            'scope_start':self.start.isoformat(),'covered_through':self.now.isoformat(),'scope':'supervised_inference_only',
            'fixed_subscription_included':False,'invoice_reconciled':False,'source_hashes':{'ledger':'b'*64,'reviews':'c'*64},
            'model_usage':{'mode':'events','source':'single_writer_supervisor_usage_v1','coverage_start':self.start.isoformat(),
                'coverage_end':self.now.isoformat(),'events':[{'id':'review:00000000-0000-4000-8000-000000000001',
                    'occurred_at':self.now.isoformat(),'currency':'EUR','amount':amount,
                    'quality':'actual' if amount is not None else 'missing','original_amount':amount,'original_currency':'EUR','source_event_hash':'d'*64}]}}

    def test_supervised_usage_plus_full_subscription_allocation(self):
        source=self.supervised_source()
        result=refresh_wallet_costs(self.path,now=self.now,supervisor_usage=source)
        self.assertEqual(Decimal(result['totals']['ai']['amount']),Decimal('.15'))
        self.assertEqual(result['totals']['ai']['quality'],'estimated')
        self.assertEqual(result['supervisor_usage_hash'],__import__('hermes_trading.reflection_supervision',fromlist=['_hash'])._hash(source))

    def test_supervised_unknown_subscription_cannot_become_free(self):
        source=self.supervised_source(subscription=None,amount='0')
        result=refresh_wallet_costs(self.path,now=self.now,supervisor_usage=source)
        self.assertIsNone(result['totals']['ai']['amount'])

    def test_supervised_subscription_plan_cannot_change_after_observation(self):
        source=self.supervised_source()
        refresh_wallet_costs(self.path,now=self.now,supervisor_usage=source)
        self.sources['categories']['ai']['subscription']['daily_rate']='0';self.write()
        with self.assertRaisesRegex(ValueError,'plan'):
            refresh_wallet_costs(self.path,now=self.now+timedelta(seconds=1),supervisor_usage=source)

    def test_supervised_stale_or_unpriced_stream_remains_unknown(self):
        source=self.supervised_source(amount=None)
        result=refresh_wallet_costs(self.path,now=self.now,supervisor_usage=source)
        self.assertIsNone(result['totals']['ai']['amount'])
        result=refresh_wallet_costs(self.path,now=self.now+timedelta(minutes=6),supervisor_usage=source)
        self.assertIsNone(result['totals']['ai']['amount'])

    def test_missing_supervised_stream_cannot_refresh_previous_projection(self):
        self.supervised_source()
        with self.assertRaises(ValueError):self.refresh()

    def test_malformed_supervised_inputs_raise_validation_errors(self):
        source=self.supervised_source()
        import copy
        for quality in ([],{},True):
            bad=copy.deepcopy(source);bad['model_usage']['events'][0]['quality']=quality
            with self.subTest(quality=quality),self.assertRaises(ValueError):
                refresh_wallet_costs(self.path,now=self.now,supervisor_usage=bad)
        for sources in ([],{'categories':[]},{'categories':{'ai':[]}}):
            (self.path/'wallet_cost_sources.json').write_text(json.dumps(sources))
            with self.subTest(sources=sources),self.assertRaises(ValueError):self.refresh()
    def test_compressed_and_legacy_cost_observations_replay_identically(self):
        import sqlite3
        from hermes_trading.reflection_supervision import _json
        result=self.refresh();db=sqlite3.connect(self.path/'wallet_cost_accrual.sqlite')
        try:
            self.assertIsInstance(db.execute('SELECT payload FROM observations').fetchone()[0],bytes)
            db.execute('UPDATE observations SET payload=?',(_json(result),));db.commit()
        finally:db.close()
        self.assertEqual(self.refresh(),result)
    def test_elapsed_recurring_and_explicit_empty_covered_stream(self):
        result=self.refresh();self.assertEqual(Decimal(result['totals']['hosting']['amount']),Decimal('0.2'))
        self.assertEqual(result['totals']['ai']['amount'],'0');self.assertEqual(result['totals']['funding']['amount'],'0')
        self.assertEqual(result,self.refresh())
    def test_missing_prices_stay_unknown_even_with_some_known_charges(self):
        self.sources['categories']['ai']['events']=[{'id':'1','occurred_at':self.now.isoformat(),'amount':'0.01','quality':'actual'},
                                                    {'id':'2','occurred_at':self.now.isoformat(),'amount':None,'quality':'missing'}]
        self.write();result=self.refresh();self.assertIsNone(result['totals']['ai']['amount'])
        self.assertEqual(result['totals']['ai']['quality'],'missing')
    def test_stale_coverage_not_refreshed_into_known_zero(self):
        result=self.refresh(self.now+timedelta(minutes=6));self.assertIsNone(result['totals']['ai']['amount'])
        self.assertEqual(result['totals']['hosting']['quality'],'estimated')
    def test_event_append_allowed_but_observed_event_change_rejected(self):
        self.sources['categories']['ai']['events']=[{'id':'1','occurred_at':self.now.isoformat(),'amount':'0.01','quality':'actual'}]
        self.write();self.refresh()
        self.sources['categories']['ai']['events'].append({'id':'2','occurred_at':(self.now+timedelta(seconds=1)).isoformat(),'amount':'0.02','quality':'actual'})
        self.sources['categories']['ai']['coverage_end']=(self.now+timedelta(seconds=1)).isoformat();self.write()
        self.assertEqual(Decimal(self.refresh(self.now+timedelta(seconds=1))['totals']['ai']['amount']),Decimal('.03'))
        self.sources['categories']['ai']['events'][0]['amount']='0';self.write()
        with self.assertRaisesRegex(ValueError,'event'):self.refresh(self.now+timedelta(seconds=2))
    def test_invalid_identity_future_coverage_and_negative_rate(self):
        for field,value in [('currency','USD'),('experiment_id','other')]:
            with self.subTest(field=field):
                original=self.sources[field];self.sources[field]=value;self.write()
                with self.assertRaises(ValueError):self.refresh()
                self.sources[field]=original
        self.sources['categories']['ai']['coverage_end']=(self.now+timedelta(seconds=1)).isoformat();self.write()
        with self.assertRaises(ValueError):self.refresh()
        self.sources['categories']['ai']['coverage_end']=self.now.isoformat()
        self.sources['categories']['hosting']['daily_rate']='-1';self.write()
        with self.assertRaises(ValueError):self.refresh()

    def test_publish_failure_after_journal_commit_is_recoverable(self):
        with patch('hermes_trading.wallet_cost_accrual._temporary',side_effect=RuntimeError('publish failed')):
            with self.assertRaises(RuntimeError):self.refresh()
        self.assertFalse((self.path/'wallet_costs.json').exists())
        result=self.refresh();self.assertEqual(result,json.loads((self.path/'wallet_costs.json').read_text()))

    def test_unknown_rate_does_not_become_zero_at_inception(self):
        self.sources['categories']['hosting']['daily_rate']=None;self.sources['categories']['hosting']['quality']='missing'
        self.sources['categories']['ai']['coverage_end']=self.start.isoformat();self.write()
        result=self.refresh(self.start);self.assertIsNone(result['totals']['hosting']['amount'])

    def test_observed_recurring_plan_and_event_removal_rejected(self):
        self.refresh();self.sources['categories']['hosting']['daily_rate']='0';self.write()
        with self.assertRaisesRegex(ValueError,'plan'):self.refresh(self.now+timedelta(seconds=1))

    def test_nonfinite_binary_float_and_duplicate_events_rejected(self):
        for value in ('NaN','Infinity',True,0.2):
            with self.subTest(value=value):
                self.sources['categories']['hosting']['daily_rate']=value;self.write()
                with self.assertRaises(ValueError):self.refresh()
        self.sources['categories']['hosting']['daily_rate']='.2'
        event={'id':'same','occurred_at':self.now.isoformat(),'amount':'0','quality':'actual'}
        self.sources['categories']['ai']['events']=[event,event];self.write()
        with self.assertRaisesRegex(ValueError,'duplicate'):self.refresh()

    def test_new_event_inside_already_complete_coverage_requires_reconciliation(self):
        self.refresh()
        self.sources['categories']['ai']['events']=[{'id':'late','occurred_at':(self.now-timedelta(seconds=1)).isoformat(),'amount':'.01','quality':'actual'}]
        self.write()
        with self.assertRaisesRegex(ValueError,'coverage'):self.refresh(self.now+timedelta(seconds=1))

    def test_projection_freshness_cannot_outlive_underlying_usage_coverage(self):
        from hermes_trading.wallet_observer import _costs
        result=self.refresh(self.now+timedelta(minutes=4))
        costs,coverage=_costs(self.path,self.account,self.now+timedelta(minutes=8))
        self.assertIsNone(costs['ai'].amount)

    def test_malformed_quality_is_an_explicit_validation_error(self):
        self.sources['categories']['hosting']['quality']=[];self.write()
        with self.assertRaises(ValueError):self.refresh()

if __name__=='__main__':unittest.main()
