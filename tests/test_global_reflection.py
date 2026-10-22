from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from hermes_trading.global_reflection import run_global_review
from hermes_trading.model_runner import ModelResponseCaptureError, ReflectionModelRunner
from hermes_trading.providers import HermesCatalog, ModelCandidate, ProviderRouter
from hermes_trading.reflection_supervision import initialize_supervisor, ReviewLease

START = datetime(2026, 10, 9, tzinfo=timezone.utc)
NOW = START + timedelta(hours=1)


class Store:
    def __init__(self): self.attempts = []
    def provider_attempts(self): return self.attempts
    def record_provider_attempt(self, attempt): self.attempts.append(attempt)


class GlobalReviewTests(unittest.TestCase):
    def test_percentage_stop_proposal_under_atr_stop_is_rejected_without_staging(self):
        self.strategy['exit']={'stop_atr':'2'}
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        before=(self.state/'strategy.yaml').read_bytes()
        runner,command=self.runner({'outcome':'change','variable':'stop_loss_pct','value':2.2,
                                   'reason':'controlled ineffective stop proposal'})
        self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'rejected')
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)
        self.assertEqual(self.audit()['outcome'],'rejected')
        self.assertEqual(command.call_count,1)

    def test_global_proposal_shadowed_on_active_assets_is_rejected_without_version_change(self):
        self.strategy['symbols']['BTC/EUR']={'entry':{'threshold':33}}
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        (self.state/'goal.yaml').write_text('one_variable_only: true\nassets: [BTC/EUR]\n')
        before=(self.state/'strategy.yaml').read_bytes()
        runner,command=self.runner({'outcome':'change','variable':'entry.threshold','value':31,
                                   'reason':'controlled fixture'})
        self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'rejected')
        self.assertEqual((self.state/'strategy.yaml').read_bytes(),before)
        self.assertEqual(self.audit()['outcome'],'rejected')
        self.assertEqual(command.call_count,1)

    def _assert_uncertain_child_pending(self,reason):
        from hermes_trading.model_runner import ModelResponseCaptureError
        runner,command=self.runner({'outcome':'no_change','reason':'test'})
        command.side_effect=subprocess.CalledProcessError(75,[],output='',stderr=reason)
        with self.assertRaises(ModelResponseCaptureError):run_global_review(self.state,runner,now=NOW)
        self.assertEqual(self.audit()['outcome'],'pending')
        retry=run_global_review(self.state,runner,now=NOW+timedelta(hours=1))
        self.assertEqual(retry['reason'],'pending_review_requires_reconciliation')
        self.assertEqual(command.call_count,1)

    def test_malformed_http_child_response_is_not_redispatched(self):
        self._assert_uncertain_child_pending('malformed HTTP JSON')

    def test_failed_child_response_capture_is_not_redispatched(self):
        self._assert_uncertain_child_pending('raw response write failed')
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        policy = {"schema_version": 1, "owner": "external_llm", "experiment_id": "test",
                  "cadence_closed_trades": 5, "one_variable_only": True,
                  "model": {"provider": "test", "name": "fixed-v1"}}
        (self.state / "reflection_owner.json").write_text(json.dumps(policy))
        self.strategy = {"version": "01", "entry": {"threshold": 30}, "stop_loss_pct": 2,
                         "symbols": {"BTC/EUR": {}, "ETH/EUR": {}}}
        (self.state / "strategy.yaml").write_text(yaml.safe_dump(self.strategy))
        (self.state / "goal.yaml").write_text('one_variable_only: true\nreflection_every: 5\n')
        self.rows = [{"asset": "BTC/EUR" if i % 2 else "ETH/EUR", "position_id": str(i),
                      "closed_at": (START + timedelta(minutes=i)).isoformat(), "pnl": i}
                     for i in range(1, 6)]
        self.write_trades()
        initialize_supervisor(self.state, now=START)

    def tearDown(self): self.tmp.cleanup()

    def write_trades(self):
        (self.state / "trades.jsonl").write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def runner(self, output):
        command = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(output), ''))
        runner = ReflectionModelRunner(ProviderRouter(Store(), HermesCatalog(())), command_run=command,
                                      strict_pinned=True,
                                      pinned_candidate=ModelCandidate("test", "fixed-v1", True, "test"))
        return runner, command

    def audit(self):
        with ReviewLease(self.state) as lease:
            return json.loads(lease.db.execute('SELECT record FROM reviews').fetchone()[0])

    def test_primary_risk_halt_blocks_direct_review_before_model_call(self):
        import sqlite3
        db=sqlite3.connect(self.state/'paper_publications.sqlite');db.execute('CREATE TABLE paired_risk_state (id INTEGER PRIMARY KEY,state TEXT)')
        db.execute('INSERT INTO paired_risk_state VALUES (1,?)',(json.dumps({'global_halted':True}),));db.commit();db.close()
        runner,command=self.runner({'outcome':'no_change','reason':'test'})
        with self.assertRaisesRegex(ValueError,'risk halt'):run_global_review(self.state,runner,now=NOW)
        self.assertEqual(command.call_count,0)

    def test_request_preparation_exhausting_budget_records_no_dispatch(self):
        from hermes_trading.costs import CostLedger
        from hermes_trading.supervisor_budget import InvocationBudget
        from unittest.mock import patch
        clock=[100.0];budget=InvocationBudget(200,clock=lambda:clock[0])
        runner,command=self.runner({'outcome':'no_change','reason':'test'})
        runner.cost_ledger=CostLedger(self.state)
        original=ReviewLease.record_material
        def slow_request(lease,*args,**kwargs):
            result=original(lease,*args,**kwargs)
            if kwargs.get('request') is not None:clock[0]+=50
            return result
        with budget.activate(),patch.object(ReviewLease,'record_material',slow_request):
            result=run_global_review(self.state,runner,now=NOW)
        self.assertEqual(result['status'],'deferred');command.assert_not_called()
        deferred=self.audit()
        self.assertEqual(deferred['outcome'],'deferred')
        self.assertEqual(deferred['decision'],{'status':'deferred_before_dispatch','reason':'invocation_budget','model_dispatched':False})
        self.assertNotIn('response',deferred)
        self.assertFalse((self.state/'cost_events.jsonl').exists())
        with ReviewLease(self.state) as lease:
            self.assertIsNone(lease.pending_review())
            self.assertTrue(lease.due(self.rows,now=NOW)['eligible'])
        with InvocationBudget(clock[0]+120,clock=lambda:clock[0]).activate():
            recovered=run_global_review(self.state,runner,now=NOW)
        self.assertEqual(recovered['status'],'no_change');self.assertEqual(command.call_count,1)
        self.assertNotEqual(recovered['review_id'],deferred['review_id'])

    def test_no_change_is_one_global_call_with_durable_inputs_and_response(self):
        runner, command = self.runner({"outcome": "no_change", "reason": "insufficient evidence"})
        result = run_global_review(self.state, runner, now=NOW)
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(command.call_count, 1)
        audit = self.audit()
        self.assertEqual(audit["request"]["strategy"], self.strategy)
        self.assertEqual(audit["response"]["provider"], "test")
        self.assertIn('stop_loss_pct is ineffective',audit['request']['prompt'])
        self.assertIn("insufficient evidence", audit["response"]["output"])
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "not_due")
        self.assertEqual(command.call_count, 1)

    def test_single_change_is_staged_and_cannot_consume_window_before_publication(self):
        runner, command = self.runner({"outcome": "change", "asset": "BTC/EUR",
                                      "variable": "entry.threshold", "value": 31, "reason": "test"})
        result = run_global_review(self.state, runner, now=NOW)
        self.assertEqual(result["status"], "awaiting_publication")
        self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_text()), self.strategy)
        audit = self.audit()
        self.assertEqual(audit["outcome"], "pending")
        self.assertEqual(audit["decision"]["candidate"]["symbols"]["BTC/EUR"]["entry"]["threshold"], 31)
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "not_due")
        self.assertEqual(command.call_count, 1)

    def test_fractional_bar_count_is_rejected_instead_of_truncated(self):
        self.strategy['exit']={'max_bars':20}
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        runner,_=self.runner({'outcome':'change','variable':'exit.max_bars','value':20.5,'reason':'test'})
        self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'rejected')

    def test_decimal_text_equivalent_is_not_a_strategy_change(self):
        self.strategy['entry']['threshold']='30.0'
        (self.state/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        runner,_=self.runner({'outcome':'change','variable':'entry.threshold','value':30,'reason':'test'})
        self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'rejected')

    def test_exact_decimal_step_boundary_is_accepted(self):
        runner,_=self.runner({'outcome':'change','variable':'stop_loss_pct','value':2.2,'reason':'test'})
        self.assertEqual(run_global_review(self.state,runner,now=NOW)['status'],'awaiting_publication')

    def test_multiple_changes_out_of_bounds_and_inert_changes_are_rejected(self):
        for proposal in (
            {"outcome": "change", "changes": [{"variable": "entry.threshold", "value": 31},
                                                {"variable": "stop_loss_pct", "value": 3}]},
            {"outcome": "change", "variable": "entry.threshold", "value": 1000},
            {"outcome": "change", "variable": "position_size_r", "value": .1},
            {"outcome": "change", "variable": "entry.threshold", "value": 30},
            {"outcome": "change", "variable": "symbols.BTC/EUR.position_size_r", "value": .1, "reason": "test"},
            {"outcome": "change", "variable": " position_size_r ", "value": .1, "reason": "test"},
        ):
            proposal = {"reason": "test", **proposal}
            with self.subTest(proposal=proposal):
                self.tearDown()
                self.setUp()
                try:
                    runner, _ = self.runner(proposal)
                    self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "rejected")
                    self.assertEqual(yaml.safe_load((self.state / 'strategy.yaml').read_text()), self.strategy)
                finally:
                    self.tearDown()

    def test_failed_model_keeps_window_and_does_not_immediately_retry(self):
        runner, command = self.runner({})
        command.side_effect = RuntimeError("test unavailable")
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "failed")
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "not_due")
        self.assertEqual(command.call_count, 1)
        with ReviewLease(self.state) as lease:
            self.assertTrue(lease.due(self.rows, now=NOW + timedelta(minutes=31))["eligible"])

    def test_non_strict_and_mismatched_pin_are_blocked_before_call(self):
        runner, command = self.runner({"outcome": "no_change"})
        for attribute, value in (("strict_pinned", False), ("pinned_candidate", ModelCandidate("other", "fixed-v1", True, "test"))):
            previous = getattr(runner, attribute)
            setattr(runner, attribute, value)
            with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                run_global_review(self.state, runner, now=NOW)
            setattr(runner, attribute, previous)
        command.assert_not_called()

    def test_future_close_is_excluded_from_review_prompt(self):
        self.rows.append({"asset": "BTC/EUR", "position_id": "future",
                          "closed_at": "2099-01-01T00:00:00Z", "pnl": 999})
        self.write_trades()
        runner, _ = self.runner({"outcome": "no_change", "reason": "test"})
        run_global_review(self.state, runner, now=NOW)
        self.assertNotIn('2099', self.audit()["request"]["prompt"])

    def test_new_wallet_without_trade_journal_does_not_trigger_model_call(self):
        (self.state / "trades.jsonl").unlink()
        runner, command = self.runner({"outcome": "no_change", "reason": "test"})
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "not_due")
        command.assert_not_called()

    def test_wallet_report_is_preserved_in_model_inputs(self):
        report={'schema_version':1,'status':'observed','experiment_id':'test','observed_at':NOW.isoformat(),
                'observation':{'net_equity':None},'gate':{'status':'blocked'}}
        (self.state/'wallet_metrics.json').write_text(json.dumps(report))
        runner,_=self.runner({'outcome':'no_change','reason':'unknown costs'})
        run_global_review(self.state,runner,now=NOW)
        self.assertEqual(self.audit()['request']['wallet_account'],report)

    def test_response_capture_failure_leaves_pending_and_blocks_duplicate_call(self):
        runner, command = self.runner({"outcome": "no_change", "reason": "test"})
        original = ReviewLease.record_material
        def capture(lease, review_id, **kwargs):
            if kwargs.get("response") is not None:
                raise OSError("injected response capture failure")
            return original(lease, review_id, **kwargs)
        with patch.object(ReviewLease, "record_material", capture), self.assertRaises(ModelResponseCaptureError):
            run_global_review(self.state, runner, now=NOW)
        self.assertEqual(self.audit()["outcome"], "pending")
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "not_due")
        self.assertEqual(command.call_count, 1)

    def test_request_capture_failure_prevents_dispatch(self):
        runner, command = self.runner({"outcome": "no_change", "reason": "test"})
        with patch.object(ReviewLease, "record_material", side_effect=OSError("injected request failure")), self.assertRaises(OSError):
            run_global_review(self.state, runner, now=NOW)
        command.assert_not_called()
        self.assertEqual(self.audit()["outcome"], "pending")

    def test_response_and_cost_capture_failure_cannot_clear_pending_request(self):
        runner, command = self.runner({"outcome": "no_change", "reason": "test"})
        runner.cost_ledger = Mock()
        runner.cost_ledger.record.side_effect = OSError("injected cost failure")
        original = ReviewLease.record_material
        paths = []
        def run(command, **kwargs):
            paths.append(Path(command[command.index("--usage-file") + 1]))
            return subprocess.CompletedProcess(command, 0, '{"outcome":"no_change","reason":"test"}', '')
        command.side_effect = run
        def capture(lease, review_id, **kwargs):
            if kwargs.get("response") is not None:
                raise OSError("injected response failure")
            return original(lease, review_id, **kwargs)
        try:
            with patch.object(ReviewLease, "record_material", capture), self.assertRaises(ModelResponseCaptureError):
                run_global_review(self.state, runner, now=NOW)
            self.assertEqual(self.audit()["outcome"], "pending")
        finally:
            for path in paths: path.unlink(missing_ok=True)

    def test_response_capture_and_cleanup_failure_cannot_clear_pending_request(self):
        runner, command = self.runner({"outcome": "no_change", "reason": "test"})
        original = ReviewLease.record_material
        paths = []
        def run(command, **kwargs):
            paths.append(Path(command[command.index("--usage-file") + 1]))
            return subprocess.CompletedProcess(command, 0, '{"outcome":"no_change","reason":"test"}', '')
        command.side_effect = run
        def capture(lease, review_id, **kwargs):
            if kwargs.get("response") is not None:
                raise OSError("injected response failure")
            return original(lease, review_id, **kwargs)
        try:
            with patch.object(ReviewLease, "record_material", capture), \
                    patch("hermes_trading.model_runner.Path.unlink", side_effect=PermissionError("cleanup failed")), \
                    self.assertRaises(ModelResponseCaptureError):
                run_global_review(self.state, runner, now=NOW)
            self.assertEqual(self.audit()["outcome"], "pending")
        finally:
            for path in paths: path.unlink(missing_ok=True)

    def test_strategy_change_during_call_requires_reconciliation(self):
        runner, command = self.runner({"outcome": "no_change", "reason": "test"})
        def run(command, **kwargs):
            (self.state / "strategy.yaml").write_text(yaml.safe_dump(dict(self.strategy, version="99")))
            return subprocess.CompletedProcess(command, 0, '{"outcome":"no_change","reason":"test"}', '')
        command.side_effect = run
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "awaiting_reconciliation")
        self.assertEqual(self.audit()["outcome"], "pending")

    def test_prompt_contains_last_25_distinct_closed_trades_including_prior_reviews(self):
        self.rows.extend({"asset": "BTC/EUR", "position_id": "old-" + str(i),
                          "closed_at": (START - timedelta(minutes=i)).isoformat()}
                         for i in range(1, 31))
        self.write_trades()
        runner, _ = self.runner({"outcome": "no_change", "reason": "test"})
        run_global_review(self.state, runner, now=NOW)
        self.assertEqual(len(self.audit()["request"]["recent_trades"]), 25)

    def test_review_material_cannot_be_rewritten_or_mutated_by_caller(self):
        with ReviewLease(self.state) as lease:
            review = lease.prepare(self.rows, now=NOW)
            request = {"strategy": {"threshold": 30}}
            lease.record_material(review["review_id"], request=request, now=NOW)
            request["strategy"]["threshold"] = 999
            with self.assertRaises(ValueError):
                lease.record_material(review["review_id"], request={"strategy": {}}, now=NOW)
        self.assertEqual(self.audit()["request"]["strategy"]["threshold"], 30)

    def test_malformed_output_is_captured_before_parse_failure(self):
        runner, command = self.runner({})
        command.return_value = subprocess.CompletedProcess([], 0, 'malformed model response', 'diagnostic')
        self.assertEqual(run_global_review(self.state, runner, now=NOW)["status"], "failed")
        self.assertEqual(self.audit()["response"]["output"], 'malformed model response')
        self.assertEqual(self.audit()["response"]["stderr"], 'diagnostic')

    def test_timeout_preserves_partial_model_output(self):
        runner, command = self.runner({})
        command.side_effect = subprocess.TimeoutExpired([], 60, output=b'partial response', stderr=b'timeout')
        with self.assertRaises(ModelResponseCaptureError):run_global_review(self.state, runner, now=NOW)
        self.assertEqual(self.audit()["response"]["output"], 'partial response')
        self.assertEqual(self.audit()['outcome'],'pending')


if __name__ == '__main__': unittest.main()
