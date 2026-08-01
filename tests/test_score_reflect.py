import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading.adapters.news import parse_rss_headlines
from hermes_trading.loop import (
    TradingConfig,
    TradingModeError,
    parse_assets,
    resolve_asset_strategy,
    resolve_trading_config,
)
from hermes_trading.run import ensure_state_files
from hermes_trading.reflect import select_reflection_models, run_fallback_reflection, run_hermes_reflection
from hermes_trading.score import score


class ScoreTests(unittest.TestCase):
    def test_score_rewards_return_quality_and_drawdown_control(self):
        trades = [
            {"realized_pnl_pct": 0.02, "equity": 102.0},
            {"realized_pnl_pct": 0.015, "equity": 103.5},
            {"realized_pnl_pct": 0.018, "equity": 105.3},
        ]
        goal = {"target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0}

        value = score(trades, goal)

        self.assertGreater(value, 0)
        self.assertLessEqual(value, 1)

    def test_score_penalizes_excess_drawdown(self):
        trades = [
            {"realized_pnl_pct": 0.03, "equity": 103.0},
            {"realized_pnl_pct": -0.14, "equity": 88.6},
        ]
        goal = {"target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0}

        self.assertLess(score(trades, goal), 0)

    def test_score_prefers_net_realized_pnl_over_legacy_percentage(self):
        goal = {"target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0}
        trades = [
            {
                "realized_pnl_pct": 0.10,
                "realized_pnl": "-20",
                "entry_cost": "100",
                "equity": 80.0,
            }
        ]

        self.assertLess(score(trades, goal), 0)


class ReflectionTests(unittest.TestCase):
    def _write_minimal_reflection_state(self, state: Path) -> None:
        (state / "history").mkdir()
        (state / "strategy.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": "01",
                    "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
                    "stop_loss_pct": 2.0,
                    "position_size_r": 0.5,
                },
                sort_keys=False,
            )
        )
        (state / "goal.yaml").write_text(
            yaml.safe_dump(
                {
                    "asset": "BTC/USDT",
                    "target_return_30d": 0.05,
                    "max_drawdown": 0.08,
                    "min_sharpe": 1.0,
                    "reflection_every": 5,
                    "one_variable_only": True,
                }
            )
        )
        (state / "trades.jsonl").write_text('{"realized_pnl_pct": -0.01, "equity": 99}\n')

    def _write_no_trade_decisions(self, state: Path, asset: str, entry_value: float = 32.0, count: int = 31) -> None:
        rows = [
            {
                "timestamp": f"2026-07-30T10:{minute:02d}:00+00:00",
                "asset": asset,
                "entry_value": entry_value,
                "threshold": 30,
                "trade_closed": False,
            }
            for minute in range(count)
        ]
        (state / "decisions.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

    def test_fallback_reflection_changes_exactly_one_variable_and_archives_prior(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)

            updated = run_fallback_reflection(state)

            self.assertEqual(updated["version"], "02")
            self.assertEqual(updated["entry"]["threshold"], 32)
            self.assertEqual(updated["stop_loss_pct"], 2.0)
            self.assertTrue((state / "history" / "v0001.yaml").exists())
            self.assertIn("entry.threshold", (state / "hypotheses.jsonl").read_text())

    def test_select_reflection_models_prefers_configured_list_then_openai_key(self):
        models = select_reflection_models(
            {
                "HERMES_REFLECTION_MODELS": "openrouter:moonshotai/kimi-k2:free, openai:gpt-5.6-luna",
                "OPENAI_API_KEY": "set",
            }
        )

        self.assertEqual(
            models[:2],
            [
                ("openrouter", "moonshotai/kimi-k2:free"),
                ("openai", "gpt-5.6-luna"),
            ],
        )

    def test_hermes_reflection_retries_with_openai_api_after_missing_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                if command[1:5] == ["--provider", "nous", "-m", "tencent/hy3:free"]:
                    raise RuntimeError("HTTP 404: Model 'tencent/hy3:free' not found")

                class Result:
                    stdout = '{"variable":"entry.threshold","value":34,"reason":"test retry"}\n'

                return Result()

            class FakeOpenAIResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "output_text": '{"variable":"entry.threshold","value":34,"reason":"test retry"}'
                    }

            env = {
                "HERMES_REFLECTION_MODELS": "nous:tencent/hy3:free",
                "OPENAI_API_KEY": "set",
                "HERMES_OPENAI_FALLBACK_MODEL": "gpt-5.6-luna",
            }
            self._write_no_trade_decisions(state, "BTC/USDT")

            with (
                patch("hermes_trading.reflect.subprocess.run", side_effect=fake_run),
                patch("hermes_trading.reflect.httpx.post", return_value=FakeOpenAIResponse()) as post,
            ):
                updated = run_hermes_reflection(state, env=env)

            self.assertEqual(updated["entry"]["threshold"], 30)
            self.assertEqual(calls, [])
            self.assertFalse(post.called)

    def test_hermes_reflection_can_update_one_symbol_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            self._write_no_trade_decisions(state, "BTC/USDT", entry_value=20.0)

            def fake_run(command, **kwargs):
                class Result:
                    stdout = (
                        '{"asset":"BTC/USDT","variable":"entry.threshold",'
                        '"value":22,"reason":"symbol-specific volatility"}\n'
                    )

                return Result()

            with patch("hermes_trading.reflect.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={"HERMES_REFLECTION_MODELS": "nous:z-ai/glm-5.2"},
                )

            self.assertEqual(updated["entry"]["threshold"], 30)
            self.assertNotIn("symbols", updated)

    def test_no_trade_drought_forces_entry_threshold_when_model_changes_sizing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "67",
                        "entry": {"indicator": "rsi", "threshold": 26, "direction": "long"},
                        "stop_loss_pct": 0.5,
                        "position_size_r": 1.0,
                        "symbols": {
                            "BTC/USDT": {
                                "entry": {"threshold": 28},
                                "position_size_r": 0.03,
                            }
                        },
                    },
                    sort_keys=False,
                )
            )
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {
                        "assets": ["BTC/USDT"],
                        "target_return_30d": 0.05,
                        "max_drawdown": 0.08,
                        "min_sharpe": 1.0,
                    },
                    sort_keys=False,
                )
            )
            decisions = [
                {
                    "timestamp": f"2026-07-30T19:{minute:02d}:00+00:00",
                    "asset": "BTC/USDT",
                    "entry_value": 70.2 + (minute % 3) / 10,
                    "threshold": 28,
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
                class Result:
                    stdout = (
                        '{"asset":"BTC/USDT","variable":"position_size_r",'
                        '"value":0.02,"reason":"reduce risk"}\n'
                    )

                return Result()

            with patch("hermes_trading.reflect.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={"HERMES_REFLECTION_MODELS": "nous:z-ai/glm-5.2"},
                )

            btc = updated["symbols"]["BTC/USDT"]
            self.assertEqual(btc["position_size_r"], 0.03)
            self.assertEqual(btc["entry"]["threshold"], 28)

    def test_no_trade_drought_raises_entry_threshold_when_model_increase_is_still_unreachable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "67",
                        "entry": {"indicator": "rsi", "threshold": 26, "direction": "long"},
                        "symbols": {
                            "ETH/USDT": {
                                "entry": {"threshold": 25},
                                "position_size_r": 0.9,
                            }
                        },
                    },
                    sort_keys=False,
                )
            )
            (state / "goal.yaml").write_text(yaml.safe_dump({"assets": ["ETH/USDT"]}))
            decisions = [
                {
                    "timestamp": f"2026-07-30T19:{minute:02d}:00+00:00",
                    "asset": "ETH/USDT",
                    "entry_value": 60.2 + (minute % 4) / 10,
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
                class Result:
                    stdout = (
                        '{"asset":"ETH/USDT","variable":"entry.threshold",'
                        '"value":30,"reason":"modest increase"}\n'
                    )

                return Result()

            with patch("hermes_trading.reflect.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={"HERMES_REFLECTION_MODELS": "nous:z-ai/glm-5.2"},
                )

            eth = updated["symbols"]["ETH/USDT"]
            self.assertEqual(eth["entry"]["threshold"], 25)


class BootstrapTests(unittest.TestCase):
    def test_ensure_state_files_seeds_empty_volume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)

            ensure_state_files(state)

            self.assertEqual(yaml.safe_load((state / "goal.yaml").read_text())["asset"], "BTC/USDT")
            self.assertEqual(yaml.safe_load((state / "strategy.yaml").read_text())["version"], "01")
            self.assertTrue((state / "history").is_dir())
            self.assertTrue((state / "trades.jsonl").exists())


class NewsAdapterTests(unittest.TestCase):
    def test_parse_rss_headlines_reads_titles(self):
        xml = """
        <rss><channel>
          <item><title>Bitcoin steadies after rally</title></item>
          <item><title>Crypto funds see inflows</title></item>
        </channel></rss>
        """

        self.assertEqual(
            parse_rss_headlines(xml),
            ["Bitcoin steadies after rally", "Crypto funds see inflows"],
        )


class TradingModeTests(unittest.TestCase):
    def test_training_mode_uses_simulated_execution(self):
        config = resolve_trading_config({"HERMES_TRADING_MODE": "training"})

        self.assertEqual(
            config,
            TradingConfig(mode="training", execution="simulated", accepts_real_orders=False),
        )

    def test_live_mode_requires_explicit_risk_acceptance(self):
        with self.assertRaises(TradingModeError):
            resolve_trading_config({"HERMES_TRADING_MODE": "live"})

    def test_live_mode_requires_confirmation_phrase_even_after_risk_flag(self):
        with self.assertRaises(TradingModeError):
            resolve_trading_config(
                {
                    "HERMES_TRADING_MODE": "live",
                    "HERMES_TRADING_I_ACCEPT_RISK": "true",
                }
            )


class MultiAssetDecisionTests(unittest.TestCase):
    def test_parse_assets_prefers_assets_list_over_single_asset(self):
        self.assertEqual(
            parse_assets({"HERMES_TRADING_ASSETS": "BTC/USDT, ETH/USDT XRP/USDT", "HERMES_TRADING_ASSET": "SOL/USDT"}),
            ["BTC/USDT", "ETH/USDT", "XRP/USDT"],
        )

    def test_parse_assets_falls_back_to_single_asset(self):
        self.assertEqual(parse_assets({"HERMES_TRADING_ASSET": "ETH/USDT"}), ["ETH/USDT"])

    def test_resolve_asset_strategy_deep_merges_symbol_override(self):
        strategy = {
            "version": "55",
            "entry": {"indicator": "rsi", "threshold": 26, "direction": "long"},
            "stop_loss_pct": 0.5,
            "position_size_r": 1.0,
            "symbols": {
                "SHIB/USDT": {
                    "entry": {"threshold": 22},
                    "stop_loss_pct": 0.8,
                }
            },
        }

        resolved = resolve_asset_strategy(strategy, "SHIB/USDT")

        self.assertEqual(resolved["version"], "55")
        self.assertEqual(resolved["entry"]["indicator"], "rsi")
        self.assertEqual(resolved["entry"]["direction"], "long")
        self.assertEqual(resolved["entry"]["threshold"], 22)
        self.assertEqual(resolved["stop_loss_pct"], 0.8)
        self.assertEqual(resolved["position_size_r"], 1.0)
        self.assertNotIn("symbols", resolved)

if __name__ == "__main__":
    unittest.main()
