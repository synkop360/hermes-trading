from datetime import timedelta
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

import test_global_reflection as fixtures
from hermes_trading.reflection_supervision import _hash
from hermes_trading.supervisor_usage_source import export_supervisor_usage


class UsageFeedTests(unittest.TestCase):
    write_trades=fixtures.GlobalReviewTests.write_trades
    def setUp(self):
        fixtures.GlobalReviewTests.setUp(self)
        self.remote_tmp=tempfile.TemporaryDirectory();self.remote=Path(self.remote_tmp.name)
        self.policy=json.loads((self.state/'reflection_owner.json').read_text())
        self.account={'schema_version':1,'experiment_id':'test','currency':'EUR','starting_balance':'100',
                      'activated_at':fixtures.START.isoformat()}
        for path in (self.state,self.remote):
            (path/'wallet_account.json').write_text(json.dumps(self.account))
            (path/'reflection_owner.json').write_text(json.dumps(self.policy))
            (path/'wallet_cost_sources.json').write_text(json.dumps({'categories':{'ai':{'mode':'supervised','policy_hash':_hash(self.policy)}}}))
        self.source=export_supervisor_usage(self.state,self.account,now=fixtures.NOW)
    def tearDown(self):self.remote_tmp.cleanup();self.tmp.cleanup()
    def request(self):
        from hermes_trading.supervisor_usage_feed import build_usage_request
        return build_usage_request(self.source,self.policy,self.account)
    def install(self,request=None,now=None):
        from hermes_trading.supervisor_usage_feed import install_supervisor_usage
        return install_supervisor_usage(self.remote,request or self.request(),env={'HERMES_TRADING_MODE':'training'},now=now or fixtures.NOW)

    def test_import_is_audited_atomic_and_identical_retry_idempotent(self):
        receipt=self.install()
        self.assertEqual(json.loads((self.remote/'supervisor_usage_snapshot.json').read_text()),self.source)
        self.assertEqual(self.install(now=fixtures.NOW+timedelta(seconds=1)),receipt)
        self.assertEqual(receipt['source_sha256'],_hash(self.source))

    def test_projection_failure_replays_committed_source_without_duplicate_receipt(self):
        with patch('hermes_trading.supervisor_usage_feed._temporary',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.install()
        self.assertFalse((self.remote/'supervisor_usage_snapshot.json').exists())
        receipt=self.install()
        import sqlite3
        db=sqlite3.connect(self.remote/'supervisor_usage_feed.sqlite')
        self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0],1);db.close()
        self.assertEqual(receipt['status'],'imported')

    def test_live_release_policy_account_and_hash_mismatches_rejected(self):
        from hermes_trading.supervisor_usage_feed import install_supervisor_usage
        with self.assertRaises(ValueError):install_supervisor_usage(self.remote,self.request(),env={'HERMES_TRADING_MODE':'live'},now=fixtures.NOW)
        for key in ('worker_release','policy_hash','account_hash','source_sha256'):
            request=self.request();request[key]={} if key=='worker_release' else '0'*64
            with self.subTest(key=key),self.assertRaises(ValueError):self.install(request)
        self.assertFalse((self.remote/'supervisor_usage_snapshot.json').exists())

    def test_regressed_or_conflicting_coverage_cannot_overwrite_newer(self):
        self.install();before=(self.remote/'supervisor_usage_snapshot.json').read_bytes()
        for offset in (-1,0):
            self.source['covered_through']=(fixtures.NOW+timedelta(seconds=offset)).isoformat()
            self.source['model_usage']['coverage_end']=self.source['covered_through']
            self.source['source_hashes']['ledger']='1'*64
            with self.subTest(offset=offset),self.assertRaises(ValueError):self.install()
            self.assertEqual((self.remote/'supervisor_usage_snapshot.json').read_bytes(),before)

    def test_new_backdated_event_cannot_rewrite_covered_empty_stream(self):
        self.install()
        self.source['covered_through']=(fixtures.NOW+timedelta(seconds=1)).isoformat()
        self.source['model_usage']['coverage_end']=self.source['covered_through']
        self.source['model_usage']['events']=[{'id':'review:00000000-0000-4000-8000-000000000001',
            'occurred_at':fixtures.NOW.isoformat(),'currency':'EUR','amount':None,'quality':'missing',
            'original_amount':None,'original_currency':'USD','source_event_hash':'a'*64}]
        with self.assertRaisesRegex(ValueError,'coverage|backdated'):self.install(now=fixtures.NOW+timedelta(seconds=2))

    def test_import_does_not_require_healthy_wallet_and_cannot_change_strategy(self):
        (self.remote/'heartbeat.json').write_text('{"status":"degraded"}')
        (self.remote/'strategy.yaml').write_text('original strategy')
        (self.remote/'experiment_halt.json').write_text('{}')
        self.install()
        self.assertEqual((self.remote/'strategy.yaml').read_text(),'original strategy')
        self.assertTrue((self.remote/'experiment_halt.json').exists())

    def event(self,at=None):
        return {'id':'review:00000000-0000-4000-8000-000000000001','occurred_at':(at or fixtures.NOW).isoformat(),
                'currency':'EUR','amount':None,'quality':'missing','original_amount':None,'original_currency':'USD','source_event_hash':'a'*64}

    def test_observed_receipt_cannot_change_disappear_or_duplicate(self):
        import copy
        self.source['model_usage']['events']=[self.event()];self.install()
        original=copy.deepcopy(self.source)
        for change in ('changed','removed','duplicate'):
            self.source=copy.deepcopy(original)
            self.source['covered_through']=(fixtures.NOW+timedelta(seconds=2)).isoformat()
            self.source['model_usage']['coverage_end']=self.source['covered_through']
            if change=='changed':self.source['model_usage']['events'][0]['original_currency']='EUR'
            elif change=='removed':self.source['model_usage']['events']=[]
            else:self.source['model_usage']['events'].append(self.event())
            with self.subTest(change=change),self.assertRaises(ValueError):self.install(now=fixtures.NOW+timedelta(seconds=2))

    def test_new_event_after_previous_coverage_is_accepted(self):
        self.install()
        self.source['covered_through']=(fixtures.NOW+timedelta(seconds=2)).isoformat()
        self.source['model_usage']['coverage_end']=self.source['covered_through']
        self.source['model_usage']['events']=[self.event(fixtures.NOW+timedelta(seconds=1))]
        receipt=self.install(now=fixtures.NOW+timedelta(seconds=2))
        self.assertEqual(receipt['source_sha256'],_hash(self.source))

    def test_exclusive_import_lease_survives_audit_commit_until_projection(self):
        import sqlite3
        from hermes_trading.supervisor_usage_feed import _temporary
        def temporary(*args):
            with self.assertRaises(sqlite3.OperationalError):self.install()
            return _temporary(*args)
        with patch('hermes_trading.supervisor_usage_feed._temporary',side_effect=temporary):receipt=self.install()
        self.assertEqual(receipt['status'],'imported')
