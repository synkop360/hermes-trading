import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import yaml

from hermes_trading.paper_publication import build_publication_request, publish_paper_request, publish_pending_review
from hermes_trading.global_reflection import run_global_review
from hermes_trading.reflection_supervision import ReviewLease
import test_global_reflection as fixtures

NOW = fixtures.NOW


class PaperPublicationTests(unittest.TestCase):
    setUp = fixtures.GlobalReviewTests.setUp
    tearDown = fixtures.GlobalReviewTests.tearDown
    write_trades = fixtures.GlobalReviewTests.write_trades
    runner = fixtures.GlobalReviewTests.runner
    audit = fixtures.GlobalReviewTests.audit
    def stage(self):
        runner, _ = self.runner({"outcome": "change", "asset": "BTC/EUR",
                                 "variable": "entry.threshold", "value": 31, "reason": "test"})
        run_global_review(self.state, runner, now=NOW)
        with ReviewLease(self.state) as lease:
            review = lease.pending_review()
            return build_publication_request(review, lease.policy)

    def publish(self, request):
        return publish_paper_request(self.state, request, env={"HERMES_TRADING_MODE": "training"}, now=NOW)

    def test_old_pending_percentage_stop_change_under_atr_cannot_be_published(self):
        self.strategy['exit']={'stop_atr':'2'}
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        before=(self.state/'strategy.yaml').read_bytes()
        candidate=yaml.safe_load(before)
        candidate['stop_loss_pct']='2.2'
        runner,_=self.runner({'outcome':'change','variable':'stop_loss_pct','value':2.2,
                              'reason':'old pending ineffective stop fixture'})
        with patch('hermes_trading.global_reflection.apply_paper_strategy_proposal',
                   return_value=(candidate,'stop_loss_pct',None)):
            self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'awaiting_publication')
        with ReviewLease(self.state) as lease:
            request=build_publication_request(lease.pending_review(),lease.policy)
        with self.assertRaisesRegex(ValueError,'effective'):
            self.publish(request)
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)
        self.assertFalse((self.state/'history/v0001.yaml').exists())

    def test_old_pending_inert_global_candidate_cannot_be_published(self):
        self.strategy['symbols']['BTC/EUR']={'entry':{'threshold':33}}
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        (self.state/'goal.yaml').write_text('one_variable_only: true\nassets: [BTC/EUR]\n')
        before=(self.state/'strategy.yaml').read_bytes()
        candidate=yaml.safe_load(before)
        candidate['entry']['threshold']=31
        runner,_=self.runner({'outcome':'change','variable':'entry.threshold','value':31,
                              'reason':'old pending proposal fixture'})
        # Reproduce a pending review created by the earlier permissive validator.
        with patch('hermes_trading.global_reflection.apply_paper_strategy_proposal',
                   return_value=(candidate,'entry.threshold',None)):
            self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'awaiting_publication')
        with ReviewLease(self.state) as lease:
            request=build_publication_request(lease.pending_review(),lease.policy)
        with self.assertRaisesRegex(ValueError,'effective'):
            self.publish(request)
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)
        self.assertFalse((self.state/'history/v0001.yaml').exists())

    def test_fractional_rsi_proposal_publishes_and_drives_real_engine(self):
        from datetime import timedelta
        from decimal import Decimal
        from hermes_trading.market_data import ClosedCandle, MarketSnapshot
        from hermes_trading.reflect import _resolve_asset_strategy
        from hermes_trading.strategy import evaluate_strategy
        self.strategy['entry']['indicator']='rsi'
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        runner,_=self.runner({'outcome':'change','asset':'BTC/EUR','variable':'entry.threshold',
                              'value':31.5,'reason':'compatibility fixture'})
        self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'awaiting_publication')
        with ReviewLease(self.state) as lease:
            request=build_publication_request(lease.pending_review(),lease.policy)
        receipt=self.publish(request)
        strategy=_resolve_asset_strategy(yaml.safe_load((self.state/'strategy.yaml').read_bytes()),'BTC/EUR')
        # Four gains of 1 and ten losses of .876 give RSI approximately 31.35.
        closes=[Decimal('100')]
        for change in [Decimal('1')]*4+[Decimal('-.876')]*10:closes.append(closes[-1]+change)
        candle=ClosedCandle(NOW-timedelta(hours=1),NOW,closes[-1],closes[-1],closes[-1],closes[-1],None)
        snapshot=MarketSnapshot('BTC/EUR','1h','available','evaluate','test',candle,{'closes':closes})
        self.assertEqual(evaluate_strategy(snapshot,strategy,None).action,'enter')
        strategy['entry']['threshold']=31
        self.assertEqual(evaluate_strategy(snapshot,strategy,None).action,'no_trade')
        self.assertEqual(self.publish(request),receipt)

    def test_unfinished_paired_batch_blocks_publication_until_completed(self):
        request=self.stage();before=(self.state/'strategy.yaml').read_bytes()
        db=sqlite3.connect(self.state/'paper_publications.sqlite')
        db.execute('CREATE TABLE paired_batches (batch_id TEXT PRIMARY KEY,status TEXT)')
        db.execute("INSERT INTO paired_batches VALUES ('test','pending')");db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'paired batch'):self.publish(request)
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)
        db=sqlite3.connect(self.state/'paper_publications.sqlite');db.execute("UPDATE paired_batches SET status='complete'");db.commit();db.close()
        self.assertEqual(self.publish(request)['status'],'published')

    def test_primary_risk_halt_blocks_publication_without_strategy_change(self):
        request=self.stage();before=(self.state/'strategy.yaml').read_bytes()
        db=sqlite3.connect(self.state/'paper_publications.sqlite');db.execute('CREATE TABLE paired_risk_state (id INTEGER PRIMARY KEY,state TEXT)')
        db.execute('INSERT INTO paired_risk_state VALUES (1,?)',(json.dumps({'global_halted':True}),));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'risk halt'):self.publish(request)
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)

    def test_durable_storage_stop_blocks_pending_publication_retry(self):
        self.stage();before=(self.state/'strategy.yaml').read_bytes()
        (self.state/'experiment_halt.json').write_text(json.dumps({'reason':'storage_stop'}))
        with self.assertRaisesRegex(ValueError,'storage|halt'):
            publish_pending_review(self.state,lambda request:self.publish(request),now=NOW)
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)

    def test_durable_storage_stop_blocks_new_review_and_snapshot(self):
        from hermes_trading.supervisor_sync import make_snapshot
        runner,command=self.runner({'outcome':'no_change','reason':'test'})
        (self.state/'experiment_halt.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'storage|halt'):run_global_review(self.state,runner,now=NOW)
        with self.assertRaisesRegex(ValueError,'storage|halt'):make_snapshot(self.state,now=NOW)
        self.assertEqual(command.call_count,0)

    def test_publication_archives_exact_bytes_and_retries_idempotently(self):
        before = (self.state / 'strategy.yaml').read_bytes()
        request = self.stage()
        receipt = self.publish(request)
        self.assertEqual((self.state / 'history/v0001.yaml').read_bytes(), before)
        self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_bytes())["version"], '02')
        self.assertEqual(self.publish(request), receipt)
        self.assertEqual(receipt["candidate_sha256"], hashlib.sha256((self.state / 'strategy.yaml').read_bytes()).hexdigest())

    def test_stale_inputs_and_live_mode_are_rejected(self):
        request = self.stage()
        with self.assertRaises(ValueError):
            publish_paper_request(self.state, request, env={"HERMES_TRADING_MODE": "live"}, now=NOW)
        for filename in ('strategy.yaml', 'goal.yaml', 'reflection_owner.json'):
            before = (self.state / filename).read_bytes()
            (self.state / filename).write_bytes(before + b'\n')
            # Owner whitespace is semantically identical, so change its identity.
            if filename.endswith('.json'):
                owner = json.loads(before)
                owner['experiment_id'] = 'other'
                (self.state / filename).write_text(json.dumps(owner))
            with self.subTest(filename=filename), self.assertRaises(ValueError): self.publish(request)
            (self.state / filename).write_bytes(before)

    def test_conflicting_archive_and_tampered_candidate_are_rejected(self):
        request = self.stage()
        (self.state / 'history').mkdir()
        (self.state / 'history/v0001.yaml').write_text('different old strategy')
        with self.assertRaises(ValueError): self.publish(request)
        self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_bytes()), self.strategy)
        request['candidate']['stop_loss_pct'] = 1
        with self.assertRaises(ValueError): self.publish(request)

    def test_writer_release_mismatch_is_rejected_before_strategy_mutation(self):
        request = self.stage()
        request['writer_release']['loop.py'] = '0' * 64
        with self.assertRaises(ValueError): self.publish(request)
        self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_bytes()), self.strategy)

    def test_crash_after_replace_can_recover_receipt_without_replacing_again(self):
        request = self.stage()
        with patch('hermes_trading.paper_publication._commit_receipt', side_effect=OSError('receipt failure')):
            with self.assertRaises(OSError): self.publish(request)
        self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_bytes())["version"], '02')
        with patch('hermes_trading.paper_publication._replace_strategy') as replace:
            receipt = self.publish(request)
            replace.assert_not_called()
        self.assertEqual(receipt['status'], 'published')

    def test_verified_receipt_completes_review_but_transport_failure_does_not(self):
        self.stage()
        with self.assertRaises(OSError):
            publish_pending_review(self.state, lambda request: (_ for _ in ()).throw(OSError('transport failed')), now=NOW)
        self.assertEqual(self.audit()['outcome'], 'pending')
        result = publish_pending_review(self.state, self.publish, now=NOW)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.audit()['outcome'], 'changed')
        self.assertIn('publication', self.audit())
        with ReviewLease(self.state) as lease:
            self.assertEqual(lease.due(self.rows, now=NOW)['new_trade_ids'], [])

    def test_false_receipt_cannot_consume_trade_window(self):
        self.stage()
        with self.assertRaises(ValueError):
            publish_pending_review(self.state, lambda request: {'status': 'published'}, now=NOW)
        self.assertEqual(self.audit()['outcome'], 'pending')

    def test_completed_retry_restores_missing_prior_archive(self):
        original = (self.state / 'strategy.yaml').read_bytes()
        request = self.stage()
        receipt = self.publish(request)
        (self.state / 'history/v0001.yaml').unlink()
        self.assertEqual(self.publish(request), receipt)
        self.assertEqual((self.state / 'history/v0001.yaml').read_bytes(), original)

    def test_pin_and_decision_base_binding_are_checked_before_mutation(self):
        request = self.stage()
        for field in ('provider', 'based_on_sha256'):
            altered = json.loads(json.dumps(request))
            if field == 'provider': altered['review']['response']['provider'] = 'deterministic'
            else: altered['review']['decision'][field] = '0' * 64
            with self.subTest(field=field), self.assertRaises(ValueError): self.publish(altered)
        self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_bytes()), self.strategy)

    def test_consistent_multi_variable_tampering_fails_reconstruction(self):
        request = self.stage()
        request['candidate']['stop_loss_pct'] = 1
        request['review']['decision']['candidate']['stop_loss_pct'] = 1
        with self.assertRaisesRegex(ValueError, 'declared variable'): self.publish(request)

    def test_missing_receipt_timestamp_cannot_complete_review(self):
        self.stage()
        def transport(request):
            receipt = self.publish(request)
            del receipt['published_at']
            return receipt
        with self.assertRaises(ValueError): publish_pending_review(self.state, transport, now=NOW)
        self.assertEqual(self.audit()['outcome'], 'pending')

    def test_completion_write_failure_retries_without_replacing_strategy(self):
        self.stage()
        original = ReviewLease.complete
        def fail_complete(lease, *args, **kwargs):
            lease.db.execute("CREATE TEMP TRIGGER fail_complete BEFORE UPDATE ON supervisor "
                             "BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END")
            return original(lease, *args, **kwargs)
        with patch.object(ReviewLease, 'complete', fail_complete), self.assertRaises(sqlite3.IntegrityError):
            publish_pending_review(self.state, self.publish, now=NOW)
        self.assertEqual(self.audit()['outcome'], 'pending')
        with patch('hermes_trading.paper_publication._replace_strategy') as replace:
            receipt = publish_pending_review(self.state, self.publish, now=NOW)
            replace.assert_not_called()
        self.assertEqual(receipt['status'], 'published')
        with ReviewLease(self.state) as lease:
            self.assertEqual(lease.due(self.rows, now=NOW)['new_trade_ids'], [])

    def test_lost_response_retry_uses_frozen_request(self):
        self.stage()
        receipts = []
        def transport(request):
            receipts.append(self.publish(request))
            raise OSError('reply lost after successful publication')
        with self.assertRaises(OSError): publish_pending_review(self.state, transport, now=NOW)
        with patch('hermes_trading.paper_publication.build_publication_request', side_effect=AssertionError('rebuilt request')):
            receipt = publish_pending_review(self.state, lambda request: receipts[0], now=NOW)
        self.assertEqual(receipt, receipts[0])

    def test_competing_publisher_is_blocked_after_intent_commits(self):
        request = self.stage()
        from hermes_trading.paper_publication import _replace_strategy
        def replace(*args, **kwargs):
            with self.assertRaises(sqlite3.OperationalError): self.publish(request)
            return _replace_strategy(*args, **kwargs)
        with patch('hermes_trading.paper_publication._replace_strategy', replace):
            self.assertEqual(self.publish(request)['status'], 'published')

    def test_same_review_identity_cannot_publish_a_different_request(self):
        request = self.stage()
        self.publish(request)
        altered = json.loads(json.dumps(request))
        altered['candidate']['symbols']['BTC/EUR']['entry']['threshold'] = 32
        altered['review']['decision']['candidate']['symbols']['BTC/EUR']['entry']['threshold'] = 32
        with self.assertRaisesRegex(ValueError, 'identity cannot be rewritten'): self.publish(altered)

    def test_helper_cli_returns_verified_receipt_in_separate_process(self):
        request = self.stage()
        result = subprocess.run([sys.executable, '-m', 'hermes_trading.paper_publication',
                                 '--state-dir', str(self.state)], input=json.dumps(request),
                                capture_output=True, text=True, timeout=20,
                                cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, 'HERMES_TRADING_MODE': 'training'})
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'published')
        self.assertEqual(receipt['review_id'], request['review_id'])
        self.assertTrue((self.state / 'history/v0001.yaml').exists())

    def test_configured_external_owner_blocks_legacy_multi_asset_path(self):
        from hermes_trading.reflect import run_hermes_reflection
        with patch('hermes_trading.reflect._load_yaml') as load:
            with self.assertRaises(RuntimeError): run_hermes_reflection(self.state)
            load.assert_not_called()


if __name__ == '__main__': unittest.main()
