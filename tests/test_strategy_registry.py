import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading.reflect import run_hermes_reflection
from hermes_trading.strategy_registry import (
    PromotionApproval,
    PromotionBlocked,
    StrategyRegistry,
    UnsafeRollback,
)


NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)


def strategy(version: str, *, threshold: int = 25, stop_loss_pct: float = 0.6, position_size_r: float = 0.1):
    return {
        "version": version,
        "entry": {"indicator": "rsi_recovery", "threshold": threshold, "direction": "long"},
        "stop_loss_pct": stop_loss_pct,
        "position_size_r": position_size_r,
        "symbols": {"BTC/USDT": {"entry": {"threshold": threshold}}},
    }


class StrategyRegistryTests(unittest.TestCase):
    def test_live_reflection_writes_candidate_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(yaml.safe_dump(strategy("69"), sort_keys=False))
            registry = StrategyRegistry(state)

            registry.propose_challenger(
                strategy("70", threshold=27),
                based_on_version="69",
                provider="nous",
                model="tencent/hy3:free",
                proposed_at=NOW,
            )

            self.assertEqual(registry.load_champion()["version"], "69")
            self.assertEqual(registry.load_challenger()["strategy"]["version"], "70")

    def test_promotion_requires_fresh_approved_qualification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(yaml.safe_dump(strategy("69"), sort_keys=False))
            registry = StrategyRegistry(state)
            candidate = registry.propose_challenger(
                strategy("70"),
                based_on_version="69",
                provider="nous",
                model="tencent/hy3:free",
                proposed_at=NOW,
            )

            stale = PromotionApproval(
                candidate_hash=candidate.candidate_hash,
                approved_at=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=1),
                qualification_run_id="qualification-old",
            )

            with self.assertRaises(PromotionBlocked):
                registry.promote_challenger(stale, now=NOW)

    def test_valid_promotion_archives_and_replaces_champion_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(yaml.safe_dump(strategy("69"), sort_keys=False))
            registry = StrategyRegistry(state)
            candidate = registry.propose_challenger(
                strategy("70"),
                based_on_version="69",
                provider="nous",
                model="tencent/hy3:free",
                proposed_at=NOW,
            )
            approval = PromotionApproval(
                candidate_hash=candidate.candidate_hash,
                approved_at=NOW,
                expires_at=NOW + timedelta(hours=1),
                qualification_run_id="qualification-fresh",
            )

            promoted = registry.promote_challenger(approval, now=NOW)

            self.assertEqual(promoted["version"], "70")
            self.assertEqual(registry.load_champion()["version"], "70")
            self.assertTrue((state / "history" / "v0069.yaml").exists())
            self.assertFalse((state / "candidate_strategy.yaml").exists())

    def test_defensive_rollback_cannot_raise_any_risk_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(yaml.safe_dump(strategy("69"), sort_keys=False))
            registry = StrategyRegistry(state)

            with self.assertRaises(UnsafeRollback):
                registry.defensive_rollback(
                    reason="test",
                    target=strategy("68", stop_loss_pct=0.8),
                    now=NOW,
                )

    def test_hermes_reflection_routes_live_change_to_challenger_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(yaml.safe_dump(strategy("69"), sort_keys=False))
            (state / "goal.yaml").write_text(yaml.safe_dump({"assets": ["BTC/USDT"]}, sort_keys=False))
            decisions = [
                {
                    "timestamp": f"2026-07-30T19:{minute:02d}:00+00:00",
                    "asset": "BTC/USDT",
                    "entry_value": 20.0,
                    "threshold": 25,
                    "trade_closed": False,
                }
                for minute in range(31)
            ]
            (state / "decisions.jsonl").write_text(
                "\n".join(json.dumps(row) for row in decisions) + "\n",
                encoding="utf-8",
            )
            (state / "trades.jsonl").write_text("", encoding="utf-8")

            def fake_run(command, **kwargs):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    '{"asset":"BTC/USDT","variable":"entry.threshold","value":27,"reason":"test"}\n',
                    "",
                )

            with patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={
                        "HERMES_TRADING_MODE": "live",
                        "HERMES_REFLECT_MIN_CLOSED_TRADES": "0",
                        "HERMES_REFLECT_MIN_NEW_TRADES": "0",
                    },
                )

            champion = yaml.safe_load((state / "strategy.yaml").read_text())
            challenger = yaml.safe_load((state / "candidate_strategy.yaml").read_text())
            self.assertEqual(champion["version"], "69")
            self.assertEqual(updated["version"], "70")
            self.assertEqual(challenger["strategy"]["version"], "70")


if __name__ == "__main__":
    unittest.main()
