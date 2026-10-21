import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from hermes_trading.reflect import generate_deterministic_asset_proposal, run_hermes_reflection


class ReflectionOutcomeTests(unittest.TestCase):
    def run_case(self, generator):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            strategy = {"version": "01", "entry": {"threshold": 15}, "symbols": {"BTC/USDT": {}}}
            (state / "strategy.yaml").write_text(yaml.safe_dump(strategy))
            (state / "goal.yaml").write_text(yaml.safe_dump({"assets": ["BTC/USDT"], "target_return_30d": .05}))
            (state / "trades.jsonl").write_text("\n".join(json.dumps({"asset": "BTC/USDT", "realized_pnl_pct": -.01}) for _ in range(5)) + "\n")
            metrics = {"score": -.5, "new_trades": 5, "new_no_trade_decisions": 0}
            with patch("hermes_trading.reflect._asset_needs_reflection", return_value=(True, ["5 new trades"], metrics)):
                result = run_hermes_reflection(state, env={}, proposal_generator=generator,
                                               market_analysis_fetcher=lambda *_: None)
            records = [json.loads(x) for x in (state / "hypotheses.jsonl").read_text().splitlines()]
            self.assertEqual(result, strategy)
            self.assertEqual(yaml.safe_load((state / "strategy.yaml").read_text()), strategy)
            self.assertFalse((state / "history").exists())
            return records

    def test_bound_no_change_is_not_a_model_failure(self):
        records = self.run_case(generate_deterministic_asset_proposal)
        self.assertEqual(records[0]["mode"], "hermes_asset_no_change")
        self.assertEqual(records[0]["provider"], "deterministic")
        self.assertIn("bound", records[0]["reason"])
        self.assertEqual(records[-1]["results"][0]["status"], "no_change")

    def test_real_provider_failure_remains_failure_with_provenance(self):
        records = self.run_case(lambda *_args, **_kw: (None, {"provider": "local_model", "model": "test", "errors": [{"error": "timeout"}]}))
        self.assertEqual(records[0]["mode"], "hermes_asset_failed")
        self.assertEqual(records[0]["provider"], "local_model")
        self.assertEqual(records[0]["errors"], [{"error": "timeout"}])

    def test_ambiguous_missing_proposal_is_not_declared_success(self):
        records = self.run_case(lambda *_args, **_kw: (None, {"provider": "test", "model": None, "errors": []}))
        self.assertEqual(records[0]["mode"], "hermes_asset_failed")


if __name__ == "__main__":
    unittest.main()
