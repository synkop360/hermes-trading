"""Dedicated Railway secrets retain the exact existing free-model checks."""
import json
import os
from unittest.mock import Mock, patch
import unittest

from hermes_trading import proposal_transport as proposal, model_access_refresh as access


class CloudModelCredentialsTests(unittest.TestCase):
    def environment(self, **overrides):
        return dict(HERMES_NOUS_AUTH='environment', HERMES_NOUS_API_KEY='private-inference',
            HERMES_NOUS_PORTAL_TOKEN='private-session', HERMES_TRADING_MODE='paper',
            HERMES_TRADING_ALLOW_REAL_ORDERS='false', **overrides)

    def test_exact_endpoint_uses_dedicated_key_without_hermes_cli(self):
        with patch.dict(os.environ, self.environment(), clear=True):
            credentials = proposal._credentials()
        self.assertEqual(credentials, {'provider':'nous','base_url':proposal.BASE,'api_key':'private-inference'})
        self.assertNotIn('private-session', json.dumps(credentials))

    def test_missing_secret_does_not_fall_back_to_pc_auth(self):
        for name in ['HERMES_NOUS_API_KEY','HERMES_NOUS_PORTAL_TOKEN']:
            env=self.environment();env.pop(name)
            with self.subTest(name=name), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, 'dedicated') as raised: proposal._credentials()
                self.assertNotIn('private-', str(raised.exception))

    def test_credentials_require_explicit_cloud_mode_and_paper_flags(self):
        for name, value in [('HERMES_NOUS_AUTH',''), ('HERMES_NOUS_AUTH','auto'),
                            ('HERMES_TRADING_MODE','live'), ('HERMES_TRADING_ALLOW_REAL_ORDERS','true')]:
            env=self.environment();env[name]=value
            with self.subTest(name=name,value=value), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError): proposal._credentials()

    def test_invalid_secret_is_redacted_before_request(self):
        for secret in [' ', 'key\nInjected: bad', 'é', 'x'*8193]:
            env=self.environment();env['HERMES_NOUS_API_KEY']=secret
            with self.subTest(length=len(secret)), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, 'dedicated'): proposal._credentials()

    def test_subscription_is_read_only_fixed_origin_and_bounded(self):
        response=Mock();response.read.return_value=b'{"context":"personal","current":null}'
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response)
        opener.open.return_value.__exit__=Mock(return_value=False)
        with patch.dict(os.environ,self.environment(),clear=True), patch.object(access,'build_opener',return_value=opener):
            self.assertEqual(access._subscription(), {'context':'personal','current':None})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://portal.nousresearch.com/api/billing/subscription')
        self.assertEqual(request.method,'GET')
        self.assertEqual(request.get_header('Authorization'),'Bearer private-session')
        response.read.assert_called_once_with(1024*1024+1)
        self.assertEqual(opener.open.call_args.kwargs['timeout'],20)


if __name__ == '__main__': unittest.main()
