import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from scripts.paper_cloud_auth import connect


class CloudAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name);self.stream=io.StringIO()
        self.credentials=dict(portal_base_url='https://portal.nousresearch.com',
            inference_base_url='https://inference-api.nousresearch.com/v1',client_id='hermes-cli',
            scope='inference:invoke',access_token='private-access',refresh_token='private-refresh',tls={'insecure':False})
        def login(**kwargs):
            print('private-access')
            kwargs['on_verification']('https://portal.nousresearch.com/device?user_code=ABCD','ABCD')
            return dict(self.credentials)
        self.auth=SimpleNamespace(_nous_device_code_login=Mock(side_effect=login),
            _persist_provider_state_to_store=Mock(),NOUS_INFERENCE_INVOKE_SCOPE='inference:invoke')

    def test_fresh_session_uses_fixed_endpoints_and_never_logs_tokens(self):
        self.assertEqual(connect(self.project,auth=self.auth,stream=self.stream),'connected')
        options=self.auth._nous_device_code_login.call_args.kwargs
        self.assertEqual(options['scope'],'inference:invoke');self.assertIs(options['open_browser'],False)
        persisted=self.auth._persist_provider_state_to_store.call_args
        self.assertEqual(persisted.args[0],'nous')
        self.assertEqual(persisted.args[2],self.project/'state/cloud-auth/auth.json')
        self.assertIn('ABCD',self.stream.getvalue())
        self.assertNotIn('private-access',self.stream.getvalue());self.assertNotIn('private-refresh',self.stream.getvalue())

    def test_existing_cloud_session_does_not_create_another_login(self):
        target=self.project/'state/cloud-auth/auth.json';target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'providers':{'nous':{'refresh_token':'retained'}}}))
        self.assertEqual(connect(self.project,auth=self.auth,stream=self.stream),'already_connected')
        self.auth._nous_device_code_login.assert_not_called()

    def test_missing_refresh_foreign_endpoint_or_billing_scope_is_rejected(self):
        for key,value in [('refresh_token',''),('inference_base_url','https://foreign.test/v1'),('scope','inference:invoke billing:manage')]:
            original=dict(self.credentials);self.credentials[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):connect(self.project,auth=self.auth,stream=self.stream)
            self.credentials=original
        self.auth._persist_provider_state_to_store.assert_not_called()


if __name__=='__main__':unittest.main()
