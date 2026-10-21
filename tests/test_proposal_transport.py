import base64,json,tempfile,unittest
from pathlib import Path

from hermes_trading.proposal_transport import run_proposal,_exchange
from unittest.mock import Mock,patch


class ProposalTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.usage=Path(self.tmp.name)/'usage.json';self.calls=[]
        self.catalog={'data':[{'id':'fixed:free','pricing':{'prompt':'0','completion':'0','input_cache_read':'0'}}]}
        self.response={'model':'fixed:free','choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'{"outcome":"no_change"}'}}],
                       'usage':{'prompt_tokens':20,'completion_tokens':10}}
    def tearDown(self):self.tmp.cleanup()
    def exchange(self,method,path,*,payload=None,token=None):
        self.calls.append((method,path,payload,token))
        return self.catalog if method=='GET' else self.response
    def invoke(self,**kwargs):
        return run_proposal('nous','fixed:free','prompt',self.usage,free_required=True,
                            exchange=self.exchange,credentials=lambda:{'provider':'nous','base_url':'https://inference-api.nousresearch.com/v1','api_key':'secret-test'},**kwargs)
    def test_one_tool_free_request_and_pricing_usage_proof(self):
        self.assertEqual(json.loads(self.invoke())['outcome'],'no_change')
        post=self.calls[1];self.assertEqual(post[0],'POST');self.assertEqual(post[2]['model'],'fixed:free')
        self.assertEqual(post[2]['tools'],[]);self.assertEqual(post[2]['tool_choice'],'none')
        self.assertEqual(post[2]['max_tokens'],1024);self.assertFalse(post[2]['stream'])
        usage=json.loads(self.usage.read_text());self.assertEqual(usage['input_tokens'],20)
        self.assertEqual(usage['pricing_verification']['payment'],'verified_zero_catalog_price')
    def test_missing_nonzero_or_duplicate_model_never_posts(self):
        for catalog in ({'data':[]},{'data':[{'id':'fixed:free','pricing':{'prompt':'.001','completion':'0'}}]},
                        {'data':self.catalog['data']*2}):
            with self.subTest(catalog=catalog):
                self.catalog=catalog;self.calls=[]
                with self.assertRaises(ValueError):self.invoke()
                self.assertEqual([c[0] for c in self.calls],['GET'])
    def test_preflight_does_not_infer(self):
        result=self.invoke(preflight=True);self.assertFalse(result['inference_performed'])
        self.assertEqual([c[0] for c in self.calls],['GET'])
    def test_response_tool_calls_mismatch_or_truncation_rejected_after_usage_save(self):
        for modification in ('tool','model','length'):
            with self.subTest(modification=modification):
                response=json.loads(json.dumps(self.response))
                if modification=='tool':response['choices'][0]['message']['tool_calls']=[{'name':'terminal'}]
                if modification=='model':response['model']='different'
                if modification=='length':response['choices'][0]['finish_reason']='length'
                original=self.response;self.response=response
                with self.assertRaises(ValueError):self.invoke()
                self.assertEqual(json.loads(self.usage.read_text())['output_tokens'],10)
                self.response=original

    def test_raw_http_body_is_captured_before_json_parse(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{malformed'
        opener=Mock();opener.open.return_value=response;captured=[]
        with patch('hermes_trading.proposal_transport.build_opener',return_value=opener):
            with self.assertRaises(json.JSONDecodeError):
                _exchange('POST','/chat/completions',payload={},token='secret',raw_capture=captured.append)
        self.assertEqual(captured,[b'{malformed'])

    def test_post_response_capture_failure_has_uncertain_outcome(self):
        from hermes_trading import proposal_transport
        original=proposal_transport._save_usage;calls=[]
        def failed(path,value):
            calls.append(True)
            if len(calls)==2:raise OSError('raw response capture failed')
            return original(path,value)
        with patch('hermes_trading.proposal_transport._save_usage',side_effect=failed):
            with self.assertRaisesRegex(RuntimeError,'uncertain'):self.invoke()
        self.assertEqual([c[0] for c in self.calls],['GET','POST'])

    def test_malformed_http_receipt_is_durable_and_uncertain(self):
        from hermes_trading import proposal_transport
        original=proposal_transport._exchange
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{malformed'
        opener=Mock();opener.open.return_value=response
        def exchange(method,path,**kwargs):
            return self.catalog if method=='GET' else original(method,path,**kwargs)
        with patch('hermes_trading.proposal_transport._exchange',side_effect=exchange),patch('hermes_trading.proposal_transport.build_opener',return_value=opener):
            with self.assertRaises(proposal_transport.ProposalOutcomeUncertain):
                run_proposal('nous','fixed:free','prompt',self.usage,free_required=True,
                             credentials=lambda:{'provider':'nous','base_url':proposal_transport.BASE,'api_key':'secret'})
        self.assertEqual(base64.b64decode(json.loads(self.usage.read_text())['raw_http_response_b64']),b'{malformed')

    def test_endpoint_authentication_mismatch_never_posts(self):
        with self.assertRaises(ValueError):
            run_proposal('nous','fixed:free','prompt',self.usage,free_required=True,exchange=self.exchange,
                         credentials=lambda:{'provider':'nous','base_url':'https://other.example/v1','api_key':'secret'})
        self.assertEqual([c[0] for c in self.calls],['GET'])

    def test_paid_request_requires_authorization_before_any_provider_call(self):
        with self.assertRaises(PermissionError):
            run_proposal('nous','paid','prompt',self.usage,free_required=False,exchange=self.exchange)
        self.assertEqual(self.calls,[])

    def test_redirect_is_refused(self):
        from hermes_trading.proposal_transport import NoRedirect
        with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.example')

if __name__=='__main__':unittest.main()
