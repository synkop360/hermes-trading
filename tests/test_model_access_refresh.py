from datetime import timedelta
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_global_reflection as fixtures
from hermes_trading.reflection_supervision import _hash


class ModelAccessRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.state=Path(self.tmp.name)
        self.policy={'schema_version':1,'owner':'external_llm','experiment_id':'refresh-test','cadence_closed_trades':5,'one_variable_only':True,
                     'model':{'provider':'nous','name':'upstage/solar-mini4:free'}}
        self.account={'schema_version':1,'experiment_id':'refresh-test','currency':'EUR','starting_balance':'100','activated_at':fixtures.START.isoformat()}
        self.access={'schema_version':1,'provider':'nous','model':self.policy['model']['name'],'policy_hash':_hash(self.policy),
                     'payment':'verified_free','provenance_ref':'reviewed_initial_free_diagnostic','tools_allowed':False,'paid_calls_authorized':False,
                     'verified_at':fixtures.START.isoformat(),'valid_until':fixtures.NOW.isoformat()}
        for name,body in (('reflection_owner.json',self.policy),('wallet_account.json',self.account),('model_access.json',self.access)):
            (self.state/name).write_text(json.dumps(body))
        self.catalog={'data':[{'id':self.access['model'],'pricing':{'prompt':'0','completion':'0','request':'0'}}]}
        self.subscription={'context':'personal','current':None}
    def tearDown(self):self.tmp.cleanup()
    def probe(self):
        from hermes_trading.model_access_refresh import probe_free_access
        def exchange(method,path,**kwargs):
            self.assertEqual((method,path),('GET','/models'))
            kwargs['raw_capture'](json.dumps(self.catalog).encode())
            return self.catalog
        with patch('hermes_trading.model_access_refresh._credentials',return_value={'api_key':'fixture-secret','base_url':'https://inference-api.nousresearch.com/v1'}), \
                patch('hermes_trading.model_access_refresh._exchange',side_effect=exchange), \
                patch('hermes_trading.model_access_refresh._subscription',return_value=self.subscription):
            return probe_free_access(self.policy,self.access,now=fixtures.NOW)
    def request(self,access=None):
        from hermes_trading.model_access_refresh import build_access_request
        return build_access_request(access or self.probe(),self.policy,self.account)
    def install(self,request=None,now=None):
        from hermes_trading.model_access_refresh import install_free_access
        return install_free_access(self.state,request or self.request(),env={'HERMES_TRADING_MODE':'training'},now=now or fixtures.NOW)

    def test_probe_is_get_only_exact_free_pin_and_contains_no_credential(self):
        access=self.probe()
        self.assertEqual(access['valid_until'],(fixtures.NOW+timedelta(minutes=5)).isoformat())
        self.assertEqual(access['provenance_ref'],self.access['provenance_ref'])
        self.assertEqual(access['payment'],'verified_free')
        self.assertIs(access['paid_calls_authorized'],False)
        self.assertNotIn('fixture-secret',json.dumps(access))

    def test_paid_missing_ambiguous_and_nonfinite_catalog_are_rejected(self):
        import copy
        original=copy.deepcopy(self.catalog)
        for case in ('paid','missing','ambiguous','nonfinite'):
            self.catalog=copy.deepcopy(original)
            if case=='paid':self.catalog['data'][0]['pricing']['request']='.001'
            elif case=='missing':self.catalog['data'][0]['pricing'].pop('completion')
            elif case=='ambiguous':self.catalog['data']*=2
            else:self.catalog['data'][0]['pricing']['prompt']='NaN'
            with self.subTest(case=case),self.assertRaises(ValueError):self.probe()

    def test_paid_or_unverifiable_subscription_is_rejected(self):
        for subscription in ({'context':'personal','current':{'tierId':'plus'}},{'context':'team','current':None},{'context':'personal'},None):
            self.subscription=subscription
            with self.subTest(subscription=subscription),self.assertRaises(ValueError):self.probe()

    def test_import_and_identical_retry_preserve_receipt_and_provenance(self):
        request=self.request();receipt=self.install(request)
        self.assertEqual(json.loads((self.state/'model_access.json').read_text()),request['access'])
        self.assertEqual(self.install(request,fixtures.NOW+timedelta(seconds=1)),receipt)

    def test_projection_failure_recovers_committed_proof(self):
        request=self.request()
        with patch('hermes_trading.model_access_refresh._temporary',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.install(request)
        self.assertEqual(json.loads((self.state/'model_access.json').read_text()),self.access)
        self.assertEqual(self.install(request)['status'],'renewed')

    def test_changed_release_account_pin_provenance_or_paid_flag_is_rejected(self):
        import copy
        original=self.request()
        for field in ('release','account','pin','provenance','paid'):
            request=copy.deepcopy(original)
            if field=='release':request['worker_release']={}
            elif field=='account':request['account_hash']='0'*64
            elif field=='pin':request['access']['model']='other'
            elif field=='provenance':request['access']['provenance_ref']='unreviewed'
            else:request['access']['paid_calls_authorized']=True
            request['access_hash']=_hash(request['access'])
            with self.subTest(field=field),self.assertRaises(ValueError):self.install(request)

    def test_expired_or_excess_lifetime_proof_is_rejected(self):
        request=self.request()
        with self.assertRaises(ValueError):self.install(request,fixtures.NOW+timedelta(minutes=5))
        request['access']['valid_until']=(fixtures.NOW+timedelta(minutes=6)).isoformat();request['access_hash']=_hash(request['access'])
        with self.assertRaises(ValueError):self.install(request)

    def test_noncanonical_time_cannot_break_journal_ordering(self):
        request=self.request()
        request['access']['verified_at']=request['access']['verified_at'].replace('+00:00','Z')
        request['access_hash']=_hash(request['access'])
        with self.assertRaises(ValueError):self.install(request)

    def test_regressed_and_same_time_conflicting_proofs_cannot_overwrite(self):
        import copy
        request=self.request();self.install(request);before=(self.state/'model_access.json').read_bytes()
        for offset in (-1,0):
            changed=copy.deepcopy(request)
            changed['access']['verified_at']=(fixtures.NOW+timedelta(seconds=offset)).isoformat()
            changed['access']['valid_until']=(fixtures.NOW+timedelta(minutes=5,seconds=offset)).isoformat()
            changed['access']['renewal']['catalog_sha256']='b'*64;changed['access_hash']=_hash(changed['access'])
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.install(changed)
            self.assertEqual((self.state/'model_access.json').read_bytes(),before)

    def test_exclusive_refresh_lease_spans_commit_and_projection(self):
        import sqlite3
        from hermes_trading.model_access_refresh import _temporary
        request=self.request()
        def temporary(*args):
            with self.assertRaises(sqlite3.OperationalError):self.install(request)
            return _temporary(*args)
        with patch('hermes_trading.model_access_refresh._temporary',side_effect=temporary):self.install(request)

    def test_worker_requires_fresh_renewal_proof_under_explicit_policy(self):
        from hermes_trading.paired_worker import _model_ready
        self.policy['model_access_renewal_required']=True
        self.access['policy_hash']=_hash(self.policy)
        self.access['valid_until']=(fixtures.NOW+timedelta(hours=6)).isoformat()
        (self.state/'model_access.json').write_text(json.dumps(self.access))
        self.assertFalse(_model_ready(self.state,self.policy,fixtures.NOW))
        proof=self.probe();(self.state/'model_access.json').write_text(json.dumps(proof))
        self.assertTrue(_model_ready(self.state,self.policy,fixtures.NOW))
        self.assertFalse(_model_ready(self.state,self.policy,fixtures.NOW+timedelta(minutes=5)))
        proof['renewal']['pricing']['request']='.01'
        (self.state/'model_access.json').write_text(json.dumps(proof))
        self.assertFalse(_model_ready(self.state,self.policy,fixtures.NOW))

    def test_nonobject_access_is_unready_instead_of_an_exception(self):
        from hermes_trading.paired_worker import _model_ready
        for value in (None,[],True,'text'):
            (self.state/'model_access.json').write_text(json.dumps(value))
            with self.subTest(value=value):self.assertFalse(_model_ready(self.state,self.policy,fixtures.NOW))
