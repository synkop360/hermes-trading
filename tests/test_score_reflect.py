import tempfile
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading.adapters import market_analysis
from hermes_trading.adapters.news import parse_rss_headlines
from hermes_trading.loop import (
    TradingConfig,
    TradingModeError,
    parse_assets,
    resolve_asset_strategy,
    resolve_trading_config,
)
from hermes_trading.run import ensure_state_files
from hermes_trading.reflect import (
    MAX_CHANGES_PER_ASSET_PER_CYCLE,
    ReflectionCycleSchedule,
    _asset_needs_reflection,
    _build_asset_reflection_prompt,
    _fetch_market_analysis,
    _legacy_reflection_timestamp,
    _load_yaml,
    _proposal_change_items,
    build_due_reflection_health_alert,
    generate_deterministic_asset_proposal,
    load_regime_started_at,
    mark_regime_start,
    resolve_reflection_health_settings,
    run_fallback_reflection,
    run_hermes_reflection,
)
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
            # A winning trade that clears round-trip transaction costs, so this
            # exercises the "loosen" branch untouched by the cost-awareness guard
            # (see test_fallback_reflection_tightens_when_winners_miss_transaction_costs).
            (state / "trades.jsonl").write_text('{"realized_pnl_pct": 0.02, "equity": 102}\n')

            updated = run_fallback_reflection(state)

            self.assertEqual(updated["version"], "02")
            self.assertEqual(updated["entry"]["threshold"], 32)
            self.assertEqual(updated["stop_loss_pct"], 2.0)
            self.assertTrue((state / "history" / "v0001.yaml").exists())
            self.assertIn("entry.threshold", (state / "hypotheses.jsonl").read_text())

    def test_fallback_reflection_tightens_when_winners_miss_transaction_costs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            # Single losing trade, so there are zero winners to clear round-trip
            # transaction costs -- the guard should tighten even though the score
            # from this tiny sample isn't negative.
            (state / "trades.jsonl").write_text('{"realized_pnl_pct": -0.01, "equity": 99}\n')

            updated = run_fallback_reflection(state)

            self.assertEqual(updated["entry"]["threshold"], 28)
            self.assertIn("transaction costs", (state / "hypotheses.jsonl").read_text())

    def test_hermes_reflection_retries_with_openai_api_after_missing_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)

            def fake_run(command, **kwargs):
                raise RuntimeError("HTTP 404: Model not found")

            class FakeOpenAIResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "output_text": '{"variable":"entry.threshold","value":34,"reason":"test retry"}',
                        "usage": {"input_tokens": 1000, "output_tokens": 200},
                    }

            env = {
                "HERMES_REFLECTION_MODELS": "nous:tencent/hy3:free",
                "OPENAI_API_KEY": "set",
                "HERMES_OPENAI_FALLBACK_MODEL": "gpt-5.6-luna",
            }
            self._write_no_trade_decisions(state, "BTC/USDT")

            with (
                patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run),
                patch("hermes_trading.reflect.httpx.post", return_value=FakeOpenAIResponse()) as post,
            ):
                updated = run_hermes_reflection(state, env=env)

            # BTC/USDT is now eligible via the no-trade-drought path (0 matching
            # trades no longer blocks it -- see NoTradeHistoryBootstrapTests), so
            # the retry this test is named for actually executes: the OpenAI
            # fallback is called and proposes entry.threshold=34, but that is a
            # 4-point jump against a 2.0 max step, so bounds validation rejects
            # it and the strategy is left unchanged.
            self.assertEqual(updated["entry"]["threshold"], 30)
            self.assertTrue(post.called)

    def test_openai_fallback_records_missing_cost_when_pricing_is_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            output = {
                "output_text": '{"variable":"entry.threshold","value":34,"reason":"test"}',
                "usage": {"input_tokens": 1000, "output_tokens": 200},
            }

            class FakeOpenAIResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return output

            from hermes_trading.costs import CostLedger
            from hermes_trading.reflect import _run_openai_prompt

            with patch("hermes_trading.reflect.httpx.post", return_value=FakeOpenAIResponse()):
                _run_openai_prompt(
                    "prompt",
                    "gpt-test",
                    {"OPENAI_API_KEY": "set"},
                    cost_ledger=CostLedger(state),
                )

            self.assertIn('"category":"openai"', (state / "cost_events.jsonl").read_text())
            self.assertIn('"quality":"missing"', (state / "cost_events.jsonl").read_text())

    def test_local_reflection_model_uses_default_docker_model_port_without_openai_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            self._write_no_trade_decisions(state, "BTC/USDT")

            class FakeLocalResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"variable":"entry.threshold","value":28,"reason":"local model"}',
                                },
                            },
                        ],
                    }

            def fake_run(command, **kwargs):
                raise RuntimeError("default unavailable")

            with (
                patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run),
                patch("hermes_trading.reflect.httpx.post", return_value=FakeLocalResponse()) as post,
            ):
                updated = run_hermes_reflection(
                    state,
                    env={
                        "HERMES_LOCAL_MODEL": "ai/test-local",
                        "HERMES_REFLECTION_TIMEOUT_SECONDS": "120",
                        "HERMES_REFLECT_MIN_CLOSED_TRADES": "0",
                    },
                )

            self.assertEqual(updated["entry"]["threshold"], 30)
            self.assertIn("symbols", updated)
            self.assertEqual(updated["symbols"]["BTC/USDT"]["entry"]["threshold"], 32)
            self.assertEqual(post.call_args.args[0], "http://127.0.0.1:12434/engines/v1/chat/completions")
            self.assertEqual(post.call_args.kwargs["json"]["model"], "ai/test-local")
            self.assertEqual(post.call_args.kwargs["timeout"], 120)
            self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
            last_hypothesis = json.loads((state / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last_hypothesis["results"][0]["provider"], "local_model")

    def test_local_reflection_model_accepts_entry_threshold_alias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            self._write_no_trade_decisions(state, "BTC/USDT", entry_value=30.0)

            class FakeLocalResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"variable":"entry_threshold","value":28,"reason":"local alias"}',
                                },
                            },
                        ],
                    }

            def fake_run(command, **kwargs):
                raise RuntimeError("default unavailable")

            with (
                patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run),
                patch("hermes_trading.reflect.httpx.post", return_value=FakeLocalResponse()),
            ):
                updated = run_hermes_reflection(
                    state,
                    env={
                        "HERMES_LOCAL_MODEL": "ai/test-local",
                        "HERMES_REFLECT_MIN_CLOSED_TRADES": "0",
                    },
                )

            self.assertEqual(updated["symbols"]["BTC/USDT"]["entry"]["threshold"], 28)

    def test_hermes_reflection_uses_pinned_provider_and_model_when_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            self._write_no_trade_decisions(state, "BTC/USDT", entry_value=20.0)

            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)

                class Result:
                    stdout = '{"asset":"BTC/USDT","variable":"entry.threshold","value":22,"reason":"pinned"}\n'

                return Result()

            with patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run):
                run_hermes_reflection(
                    state,
                    env={
                        "HERMES_REFLECTION_PINNED_PROVIDER": "openrouter",
                        "HERMES_REFLECTION_PINNED_MODEL": "fixed:model",
                        "HERMES_REFLECT_MIN_CLOSED_TRADES": "0",
                    },
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-4:], ["--provider", "openrouter", "-m", "fixed:model"])

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

            with patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run):
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

            with patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={"HERMES_REFLECTION_MODELS": "nous:z-ai/glm-5.2"},
                )

            btc = updated["symbols"]["BTC/USDT"]
            # 0 total trades no longer blocks eligibility (see
            # NoTradeHistoryBootstrapTests), so this asset's no-trade drought
            # is now actually evaluated -- exactly what this test is named
            # for. The model's position_size_r proposal gets entirely
            # superseded by the accessibility guard once it sees every
            # observed RSI reading (~70.2-70.4) sits far above the 28
            # threshold: it forces entry.threshold up instead (capped at the
            # 2.0 max step, 28 -> 30), and position_size_r is left untouched.
            self.assertEqual(btc["position_size_r"], 0.03)
            self.assertEqual(btc["entry"]["threshold"], 30)

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

            with patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={"HERMES_REFLECTION_MODELS": "nous:z-ai/glm-5.2"},
                )

            eth = updated["symbols"]["ETH/USDT"]
            # Same unblocked-eligibility mechanism as above. The model's own
            # proposal (25 -> 30) is still far short of the observed RSI floor
            # (~60.2), so the accessibility guard discards it and substitutes
            # its own max-step-respecting target (25 -> 27) instead of leaving
            # the strategy unchanged.
            self.assertEqual(eth["entry"]["threshold"], 27)

    def test_no_trade_drought_uses_legacy_hypothesis_timestamp_when_reflection_state_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "83",
                        "entry": {
                            "indicator": "rsi_recovery",
                            "threshold": 25,
                            "direction": "long",
                        },
                        "symbols": {
                            "BTC/USDT": {
                                "entry": {"threshold": 25},
                                "position_size_r": 0.1,
                            }
                        },
                    },
                    sort_keys=False,
                )
            )
            (state / "goal.yaml").write_text(yaml.safe_dump({"assets": ["BTC/USDT"]}))
            trades = [
                {
                    "timestamp": f"2026-08-01T10:{minute:02d}:00+00:00",
                    "asset": "BTC/USDT",
                    "realized_pnl_pct": -0.01,
                    "equity": 99.0 - minute / 100,
                }
                for minute in range(20)
            ]
            (state / "trades.jsonl").write_text(
                "\n".join(json.dumps(row) for row in trades) + "\n",
                encoding="utf-8",
            )
            (state / "hypotheses.jsonl").write_text(
                json.dumps({"timestamp": "2026-08-01T12:00:00+00:00", "mode": "hermes_asset"}) + "\n",
                encoding="utf-8",
            )
            decisions = [
                {
                    "decided_at": f"2026-08-02T19:{minute:02d}:00+00:00",
                    "asset": "BTC/USDT",
                    "threshold": 25,
                    "trade_closed": False,
                }
                for minute in range(31)
            ]
            (state / "decisions.jsonl").write_text(
                "\n".join(json.dumps(row) for row in decisions) + "\n",
                encoding="utf-8",
            )

            def fake_run(command, **kwargs):
                raise RuntimeError("HTTP 429: too many requests")

            with patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run):
                updated = run_hermes_reflection(
                    state,
                    env={
                        "HERMES_REFLECTION_MODELS": "nous:z-ai/glm-5.2",
                        "HERMES_REFLECT_COOLDOWN_HOURS": "0",
                    },
                )

            self.assertEqual(updated["symbols"]["BTC/USDT"]["entry"]["threshold"], 27)
            self.assertIsInstance(
                updated["symbols"]["BTC/USDT"]["entry"]["threshold"],
                int,
            )
            self.assertEqual(updated["version"], "84")

    def test_legacy_reflection_timestamp_ignores_multi_asset_no_change_summaries(self):
        """A cycle where nothing changed still logs a "hermes_multi_asset_no_change"
        summary row with no "asset" field. Before this was fixed, that empty
        asset field wildcard-matched every asset in the loose ``if record_asset``
        check below, so an instance that had never once genuinely reflected on
        an asset (e.g. a freshly reset reflection_state.json) would see its own
        prior skip-cycle as "last reflection was N minutes ago", stay inside
        cooldown forever, skip again, and re-poison the next lookup -- a
        self-sustaining loop that could never become eligible again."""
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            rows = [
                {"timestamp": f"2026-08-19T0{i}:00:00+00:00", "mode": "hermes_multi_asset_no_change", "results": []}
                for i in range(6)
            ]
            (state / "hypotheses.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )

            self.assertIsNone(_legacy_reflection_timestamp(state, "BTC/USDT"))

    def test_legacy_reflection_timestamp_still_finds_a_genuine_asset_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            rows = [
                {"timestamp": "2026-08-19T00:00:00+00:00", "mode": "hermes_multi_asset_no_change", "results": []},
                {"timestamp": "2026-08-18T12:00:00+00:00", "mode": "hermes_asset", "asset": "BTC/USDT"},
            ]
            (state / "hypotheses.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )

            found = _legacy_reflection_timestamp(state, "BTC/USDT")

            self.assertIsNotNone(found)
            self.assertEqual(found.isoformat(), "2026-08-18T12:00:00+00:00")

    def test_reflection_prompt_uses_the_single_variable_schema_by_default(self):
        prompt = _build_asset_reflection_prompt(
            "BTC/USDT", {"entry": {"threshold": 30}}, {}, [], [], [], {}, {},
        )

        self.assertIn("Do not change more than one variable.", prompt)
        self.assertNotIn('"changes"', prompt)

    def test_reflection_prompt_switches_to_the_multi_variable_schema_when_allowed(self):
        prompt = _build_asset_reflection_prompt(
            "BTC/USDT", {"entry": {"threshold": 30}}, {"one_variable_only": False}, [], [], [], {}, {},
        )

        self.assertIn('"changes"', prompt)
        self.assertIn(str(MAX_CHANGES_PER_ASSET_PER_CYCLE), prompt)
        self.assertNotIn("Do not change more than one variable.", prompt)

    def test_hermes_reflection_applies_multiple_changes_when_one_variable_only_is_false(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_minimal_reflection_state(state)
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {
                        "asset": "BTC/USDT",
                        "target_return_30d": 0.05,
                        "max_drawdown": 0.08,
                        "min_sharpe": 1.0,
                        "reflection_every": 5,
                        "one_variable_only": False,
                    }
                )
            )
            # Eligibility via genuine trade/decision volume (>= min_new_trades,
            # >= min_new_decisions), not the no-trade-accessibility-drought
            # path -- that guard only ever returns a classic single-variable
            # override and would otherwise clobber the multi-change proposal
            # this test is exercising.
            (state / "trades.jsonl").write_text(
                "\n".join(
                    json.dumps({"asset": "BTC/USDT", "realized_pnl_pct": 0.01, "equity": 100 + index})
                    for index in range(5)
                )
                + "\n",
                encoding="utf-8",
            )
            (state / "decisions.jsonl").write_text(
                "\n".join(
                    json.dumps({"asset": "BTC/USDT", "action": "hold", "timestamp": f"2026-07-30T10:{minute:02d}:00+00:00"})
                    for minute in range(31)
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeLocalResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "asset": "BTC/USDT",
                                            "changes": [
                                                {"variable": "entry.threshold", "value": 29, "reason": "a"},
                                                {"variable": "stop_loss_pct", "value": 1.8, "reason": "b"},
                                            ],
                                        }
                                    ),
                                },
                            },
                        ],
                    }

            def fake_run(command, **kwargs):
                raise RuntimeError("default unavailable")

            with (
                patch("hermes_trading.model_runner.subprocess.run", side_effect=fake_run),
                patch("hermes_trading.reflect.httpx.post", return_value=FakeLocalResponse()),
            ):
                updated = run_hermes_reflection(
                    state,
                    env={
                        "HERMES_LOCAL_MODEL": "ai/test-local",
                        "HERMES_REFLECTION_TIMEOUT_SECONDS": "120",
                        "HERMES_REFLECT_MIN_CLOSED_TRADES": "0",
                    },
                )

            # Both changes carry the proposal's top-level "asset", so they
            # land in strategy.symbols["BTC/USDT"] (a per-symbol override),
            # not the root strategy -- the same scoping every other
            # per-asset proposal (e.g. the accessibility guards) already uses.
            btc_overrides = updated["symbols"]["BTC/USDT"]
            self.assertEqual(btc_overrides["entry"]["threshold"], 29)
            self.assertEqual(btc_overrides["stop_loss_pct"], 1.8)
            hypotheses = (state / "hypotheses.jsonl").read_text()
            self.assertIn('"variable":"entry.threshold"', hypotheses)
            self.assertIn('"variable":"stop_loss_pct"', hypotheses)

    def test_proposal_change_items_passes_through_a_classic_single_variable_proposal(self):
        proposal = {"asset": "BTC/USDT", "variable": "entry.threshold", "value": 29, "reason": "x"}

        self.assertEqual(_proposal_change_items(proposal), [proposal])

    def test_proposal_change_items_drops_entries_past_the_per_cycle_cap(self):
        proposal = {
            "asset": "BTC/USDT",
            "changes": [
                {"variable": f"exit.max_bars", "value": index, "reason": "x"}
                for index in range(MAX_CHANGES_PER_ASSET_PER_CYCLE + 5)
            ],
        }

        items = _proposal_change_items(proposal)

        self.assertEqual(len(items), MAX_CHANGES_PER_ASSET_PER_CYCLE)
        self.assertTrue(all(item["asset"] == "BTC/USDT" for item in items))


class DeterministicAssetProposalTests(unittest.TestCase):
    """generate_deterministic_asset_proposal is the in-process reflection
    trigger's proposal source -- it must produce sensible proposals with no
    external process or API call (see loop.py's HERMES_REFLECTION_INPROCESS_EVERY_CYCLES)."""

    def setUp(self) -> None:
        from hermes_trading.reflect import resolve_reflection_policy

        self.policy = resolve_reflection_policy({})

    def test_raises_take_profit_atr_when_payoff_gate_dominates_no_trade_reason(self):
        proposal, model_info = generate_deterministic_asset_proposal(
            "BTC/USDT", "", {},
            effective_strategy={"entry": {"threshold": 30}, "exit": {"take_profit_atr": 5.0}},
            metrics={"dominant_no_trade_reason": "expected_payoff_below_costs"},
            trades=[], goal={"target_return_30d": 0.05}, policy=self.policy,
        )

        self.assertEqual(proposal["variable"], "exit.take_profit_atr")
        self.assertEqual(proposal["value"], 12.0)
        self.assertEqual(model_info["provider"], "deterministic")

    def test_tightens_entry_threshold_when_score_is_negative(self):
        trades = [{"realized_pnl_pct": -0.01} for _ in range(5)]

        proposal, _ = generate_deterministic_asset_proposal(
            "BTC/USDT", "", {}, effective_strategy={"entry": {"threshold": 30}}, metrics={},
            trades=trades, goal={"target_return_30d": 0.05}, policy=self.policy,
        )

        self.assertEqual(proposal, {
            "asset": "BTC/USDT", "variable": "entry.threshold", "value": 28.0,
            "reason": (
                "deterministic in-process reflection: realized return missed target and "
                "the score is negative, so entry was tightened to trade more selectively."
            ),
        })

    def test_loosens_entry_threshold_when_target_missed_but_winners_clear_costs(self):
        trades = [{"realized_pnl_pct": 0.015} for _ in range(3)]

        proposal, _ = generate_deterministic_asset_proposal(
            "BTC/USDT", "", {}, effective_strategy={"entry": {"threshold": 30}}, metrics={},
            trades=trades, goal={"target_return_30d": 0.05}, policy=self.policy,
        )

        self.assertEqual(proposal["variable"], "entry.threshold")
        self.assertEqual(proposal["value"], 32.0)

    def test_tightens_entry_threshold_when_target_is_met(self):
        trades = [{"realized_pnl_pct": 0.08} for _ in range(3)]

        proposal, _ = generate_deterministic_asset_proposal(
            "BTC/USDT", "", {}, effective_strategy={"entry": {"threshold": 30}}, metrics={},
            trades=trades, goal={"target_return_30d": 0.05}, policy=self.policy,
        )

        self.assertEqual(proposal["variable"], "entry.threshold")
        self.assertEqual(proposal["value"], 28.0)

    def test_returns_none_when_entry_threshold_is_not_configured(self):
        proposal, _ = generate_deterministic_asset_proposal(
            "BTC/USDT", "", {}, effective_strategy={}, metrics={},
            trades=[], goal={"target_return_30d": 0.05}, policy=self.policy,
        )

        self.assertIsNone(proposal)


class RegimeScopingTests(unittest.TestCase):
    """score/consecutive_losses must forget trades from a since-replaced
    strategy regime, so a legacy losing streak can't perpetually veto the
    current entry/exit logic -- see load_regime_started_at/mark_regime_start."""

    def setUp(self) -> None:
        from hermes_trading.reflect import resolve_reflection_policy

        self.policy = resolve_reflection_policy({})

    def test_load_regime_started_at_defaults_to_now_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            now = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)

            first = load_regime_started_at(state, {}, now=now)
            marker = state / "reflection-regime-started-at.txt"

            self.assertTrue(marker.exists())
            self.assertEqual(first, now)

            # A second call (with a later "now") must not move the marker --
            # it was already created by the first call.
            second = load_regime_started_at(state, {}, now=datetime(2026, 8, 19, 0, 0, 0, tzinfo=timezone.utc))
            self.assertEqual(second, first)

    def test_load_regime_started_at_respects_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)

            started_at = load_regime_started_at(
                state, {"HERMES_REFLECTION_REGIME_STARTED_AT": "2026-08-10T00:00:00+00:00"}
            )

            self.assertEqual(started_at.isoformat(), "2026-08-10T00:00:00+00:00")
            self.assertFalse((state / "reflection-regime-started-at.txt").exists())

    def test_mark_regime_start_resets_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "reflection-regime-started-at.txt").write_text("2020-01-01T00:00:00+00:00\n", encoding="utf-8")

            reset_at = mark_regime_start(state, now=datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc))

            self.assertEqual(
                (state / "reflection-regime-started-at.txt").read_text(encoding="utf-8").strip(),
                reset_at.isoformat(),
            )
            self.assertNotEqual(reset_at.year, 2020)

    def test_asset_needs_reflection_excludes_pre_regime_trades_from_score(self) -> None:
        old_losses = [
            {"asset": "BTC/USDT", "closed_at": "2026-01-01T00:00:00+00:00", "realized_pnl_pct": -0.02}
            for _ in range(25)
        ]
        goal = {"target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0}
        now = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)

        _, _, unscoped_metrics = _asset_needs_reflection(
            asset="BTC/USDT", trades=old_losses, decisions=[], last_reflection_at=None,
            goal=goal, policy=self.policy, now=now, regime_started_at=None,
        )
        _, _, scoped_metrics = _asset_needs_reflection(
            asset="BTC/USDT", trades=old_losses, decisions=[], last_reflection_at=None,
            goal=goal, policy=self.policy, now=now,
            regime_started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

        # Unscoped: the full 25-loss legacy history pins score at the floor.
        self.assertEqual(unscoped_metrics["consecutive_losses"], 25)
        self.assertLess(unscoped_metrics["score"], -0.5)
        self.assertEqual(unscoped_metrics["total_trades"], 25)

        # Regime-scoped: none of those trades closed after Aug 1, so scoring
        # sees a clean slate, while total_trades (eligibility) is unaffected.
        self.assertEqual(scoped_metrics["consecutive_losses"], 0)
        self.assertEqual(scoped_metrics["score"], -0.2)
        self.assertEqual(scoped_metrics["scored_trades"], 0)
        self.assertEqual(scoped_metrics["total_trades"], 25)

    def test_run_hermes_reflection_records_regime_scoped_metrics_not_legacy_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "01",
                        "entry": {"indicator": "rsi_recovery", "threshold": 30, "direction": "long"},
                        "exit": {"indicator": "rsi", "threshold": 55, "take_profit_atr": 5.0},
                        "stop_loss_pct": 0.5,
                    },
                    sort_keys=False,
                )
            )
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {"assets": ["BTC/USDT"], "target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0},
                    sort_keys=False,
                )
            )
            # 25 losing trades under a now-replaced strategy, long before both
            # last_reflection_at and the regime marker below -- must not
            # drive today's score/consecutive_losses.
            old_losses = [
                {"asset": "BTC/USDT", "closed_at": "2026-01-01T00:00:00+00:00", "realized_pnl_pct": -0.02}
                for _ in range(25)
            ]
            (state / "trades.jsonl").write_text(
                "\n".join(json.dumps(row) for row in old_losses) + "\n", encoding="utf-8"
            )
            (state / "reflection_state.json").write_text(
                json.dumps({"symbols": {"BTC/USDT": {"last_reflection_at": "2026-08-10T00:00:00+00:00"}}}),
                encoding="utf-8",
            )
            no_trade = [
                {
                    "timestamp": f"2026-08-18T01:{i:02d}:00+00:00", "asset": "BTC/USDT",
                    "action": "no_trade", "reason": "entry_condition_not_met", "entry_value": 32.0,
                }
                for i in range(4)
            ]
            (state / "decisions.jsonl").write_text(
                "\n".join(json.dumps(row) for row in no_trade) + "\n", encoding="utf-8"
            )

            run_hermes_reflection(
                state,
                env={
                    "HERMES_REFLECT_MIN_CLOSED_TRADES": "1",
                    "HERMES_REFLECT_MIN_NEW_DECISIONS": "3",
                    "HERMES_REFLECT_MIN_NEW_TRADES": "1",
                    "HERMES_REFLECT_COOLDOWN_HOURS": "0",
                    "HERMES_REFLECTION_REGIME_STARTED_AT": "2026-08-15T00:00:00+00:00",
                },
                proposal_generator=generate_deterministic_asset_proposal,
            )

            hypotheses = [
                json.loads(line)
                for line in (state / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        applied = next(row for row in hypotheses if row.get("asset") == "BTC/USDT" and row.get("changed"))
        # Without regime scoping this would carry the 25-loss legacy score
        # (well below -0.5) into the applied hypothesis; scoped, it reads as
        # a clean slate instead.
        self.assertEqual(applied["metrics"]["score"], -0.2)
        self.assertEqual(applied["metrics"]["consecutive_losses"], 0)
        self.assertEqual(applied["metrics"]["total_trades"], 25)


class NoTradeHistoryBootstrapTests(unittest.TestCase):
    """A brand-new asset (zero trades ever, e.g. LINK/SOL the day they're
    added) must not be permanently excluded from the no-trade-drought
    accessibility path just for lacking history it can only ever accumulate
    with that path's help -- see _asset_needs_reflection's comment."""

    def setUp(self) -> None:
        from hermes_trading.reflect import resolve_reflection_policy

        self.policy = resolve_reflection_policy({})
        self.goal = {"target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0}
        self.now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

    def test_zero_total_trades_no_longer_blocks_no_trade_drought_eligibility(self) -> None:
        no_trade_decisions = [
            {
                "timestamp": f"2026-08-21T00:{m:02d}:00+00:00", "asset": "LINK/USDT",
                "action": "no_trade", "reason": "entry_condition_not_met",
            }
            for m in range(35)
        ]

        should_reflect, reasons, metrics = _asset_needs_reflection(
            asset="LINK/USDT", trades=[], decisions=no_trade_decisions, last_reflection_at=None,
            goal=self.goal, policy=self.policy, now=self.now,
        )

        self.assertEqual(metrics["total_trades"], 0)
        self.assertTrue(metrics["eligible_sample"])
        self.assertTrue(should_reflect)
        self.assertTrue(any("no-trade decisions" in reason for reason in reasons))

    def test_a_handful_of_new_trades_below_min_new_trades_still_does_not_qualify(self) -> None:
        # Unchanged behavior: dropping the total-history gate does not lower
        # min_new_trades itself -- a couple of new trades is still neither
        # "enough trades to judge" nor "a true no-trade drought".
        trades = [
            {"asset": "ETH/USDT", "closed_at": "2026-08-21T10:00:00+00:00", "realized_pnl_pct": 0.01},
            {"asset": "ETH/USDT", "closed_at": "2026-08-21T11:00:00+00:00", "realized_pnl_pct": -0.01},
        ]

        _, _, metrics = _asset_needs_reflection(
            asset="ETH/USDT", trades=trades, decisions=[], last_reflection_at=None,
            goal=self.goal, policy=self.policy, now=self.now,
        )

        self.assertEqual(metrics["total_trades"], 2)
        self.assertFalse(metrics["eligible_sample"])

    def test_established_asset_with_enough_new_trades_is_still_eligible(self) -> None:
        # An asset well past min_closed_trades must still work exactly as
        # before -- this fix only removes a block, it must add no new one.
        trades = [
            {"asset": "BTC/USDT", "closed_at": f"2026-08-{d:02d}T00:00:00+00:00", "realized_pnl_pct": 0.01}
            for d in range(1, 26)
        ]

        _, _, metrics = _asset_needs_reflection(
            asset="BTC/USDT", trades=trades, decisions=[], last_reflection_at=None,
            goal=self.goal, policy=self.policy, now=self.now,
        )

        self.assertEqual(metrics["total_trades"], 25)
        self.assertTrue(metrics["eligible_sample"])


class ReflectionHealthAlertTests(unittest.TestCase):
    def _settings(self, **overrides):
        env = {
            "HERMES_REFLECTION_STALE_ALERT_ENABLED": "true",
            "HERMES_REFLECTION_STALE_ALERT_HOURS": "36",
            "HERMES_REFLECTION_STALE_ALERT_COOLDOWN_SECONDS": "21600",
        }
        env.update(overrides)
        return resolve_reflection_health_settings(env)

    def _write_last_hypothesis(self, state: Path, at: str) -> None:
        (state / "hypotheses.jsonl").write_text(
            json.dumps({"timestamp": at, "mode": "hermes_multi_asset_no_change"}) + "\n",
            encoding="utf-8",
        )

    def test_returns_none_when_reflection_is_recent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_last_hypothesis(state, "2026-08-01T00:00:00+00:00")

            message = build_due_reflection_health_alert(
                state, self._settings(), now=datetime.fromisoformat("2026-08-01T10:00:00+00:00"),
            )

        self.assertIsNone(message)

    def test_returns_none_when_no_hypotheses_exist_yet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            message = build_due_reflection_health_alert(
                Path(temp_dir), self._settings(), now=datetime.fromisoformat("2026-08-01T10:00:00+00:00"),
            )

        self.assertIsNone(message)

    def test_returns_none_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_last_hypothesis(state, "2026-08-01T00:00:00+00:00")

            message = build_due_reflection_health_alert(
                state,
                self._settings(HERMES_REFLECTION_STALE_ALERT_ENABLED="false"),
                now=datetime.fromisoformat("2026-08-03T00:00:00+00:00"),
            )

        self.assertIsNone(message)

    def test_alerts_once_past_stale_threshold_and_persists_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_last_hypothesis(state, "2026-08-01T00:00:00+00:00")

            message = build_due_reflection_health_alert(
                state, self._settings(), now=datetime.fromisoformat("2026-08-02T13:00:00+00:00"),
            )
            saved = json.loads((state / "reflection_health.json").read_text(encoding="utf-8"))

        self.assertIn("Reflection has not run in 37.0h", message)
        self.assertEqual(saved["status"], "stale")

    def test_does_not_repeat_alert_before_cooldown_then_repeats_after(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_last_hypothesis(state, "2026-08-01T00:00:00+00:00")
            settings = self._settings()

            first = build_due_reflection_health_alert(
                state, settings, now=datetime.fromisoformat("2026-08-02T13:00:00+00:00"),
            )
            second = build_due_reflection_health_alert(
                state, settings, now=datetime.fromisoformat("2026-08-02T15:00:00+00:00"),
            )
            third = build_due_reflection_health_alert(
                state, settings, now=datetime.fromisoformat("2026-08-02T19:00:01+00:00"),
            )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)

    def test_sends_resolve_message_once_reflection_resumes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._write_last_hypothesis(state, "2026-08-01T00:00:00+00:00")
            settings = self._settings()

            build_due_reflection_health_alert(
                state, settings, now=datetime.fromisoformat("2026-08-02T13:00:00+00:00"),
            )
            self._write_last_hypothesis(state, "2026-08-02T14:00:00+00:00")

            resolve_message = build_due_reflection_health_alert(
                state, settings, now=datetime.fromisoformat("2026-08-02T14:05:00+00:00"),
            )
            saved = json.loads((state / "reflection_health.json").read_text(encoding="utf-8"))

        self.assertEqual(resolve_message, "Hermes Trading | Reflection resumed: hypotheses.jsonl is updating again.")
        self.assertEqual(saved["status"], "ok")


class SystemicPayoffGateSharingTests(unittest.TestCase):
    def test_sibling_asset_gets_the_same_accessibility_fix_without_its_own_eligibility(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "01",
                        "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
                        "exit": {"indicator": "rsi", "threshold": 55, "take_profit_atr": 5.0},
                        "stop_loss_pct": 0.5,
                    },
                    sort_keys=False,
                )
            )
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {
                        "assets": ["BTC/USDT", "ETH/USDT", "XRP/USDT"],
                        "target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0,
                    },
                    sort_keys=False,
                )
            )
            # BTC/USDT: independently eligible -- one historical trade (before
            # its last reflection), zero new trades, enough no-trade decisions
            # dominated by the payoff-gate reason.
            (state / "reflection_state.json").write_text(
                json.dumps({"symbols": {"BTC/USDT": {"last_reflection_at": "2026-08-01T00:00:00+00:00"}}}),
                encoding="utf-8",
            )
            (state / "trades.jsonl").write_text(
                json.dumps({"asset": "BTC/USDT", "closed_at": "2026-07-30T00:00:00+00:00", "realized_pnl_pct": 0.01})
                + "\n",
                encoding="utf-8",
            )
            btc_no_trade = [
                {
                    "timestamp": f"2026-08-01T01:0{i}:00+00:00", "asset": "BTC/USDT",
                    "action": "no_trade", "reason": "expected_payoff_below_costs",
                }
                for i in range(4)
            ]
            # ETH/USDT: same lockout diagnosis, but no trade history at all,
            # so it can never independently pass has_trade_history on its own.
            eth_no_trade = [
                {
                    "timestamp": f"2026-08-01T01:0{i}:00+00:00", "asset": "ETH/USDT",
                    "action": "no_trade", "reason": "expected_payoff_below_costs",
                }
                for i in range(4)
            ]
            # XRP/USDT: locked out for an unrelated reason -- must not be swept in.
            xrp_no_trade = [
                {
                    "timestamp": f"2026-08-01T01:0{i}:00+00:00", "asset": "XRP/USDT",
                    "action": "no_trade", "reason": "entry_condition_not_met",
                }
                for i in range(4)
            ]
            (state / "decisions.jsonl").write_text(
                "\n".join(json.dumps(row) for row in btc_no_trade + eth_no_trade + xrp_no_trade) + "\n",
                encoding="utf-8",
            )

            updated = run_hermes_reflection(
                state,
                env={
                    "HERMES_REFLECT_MIN_CLOSED_TRADES": "1",
                    "HERMES_REFLECT_MIN_NEW_DECISIONS": "3",
                    "HERMES_REFLECT_MIN_NEW_TRADES": "1",
                    "HERMES_REFLECT_COOLDOWN_HOURS": "0",
                },
                proposal_generator=generate_deterministic_asset_proposal,
            )

        self.assertEqual(updated["symbols"]["BTC/USDT"]["exit"]["take_profit_atr"], 12.0)
        self.assertEqual(updated["symbols"]["ETH/USDT"]["exit"]["take_profit_atr"], 12.0)
        self.assertNotIn("exit", updated["symbols"]["XRP/USDT"])


class ReflectionCycleScheduleTests(unittest.TestCase):
    def test_records_cycle_only_on_configured_interval(self):
        schedule = ReflectionCycleSchedule(3)

        self.assertFalse(schedule.record_cycle())
        self.assertFalse(schedule.record_cycle())
        self.assertTrue(schedule.record_cycle())
        self.assertFalse(schedule.record_cycle())

    def test_disabled_when_every_cycles_is_zero(self):
        schedule = ReflectionCycleSchedule(0)

        for _ in range(10):
            self.assertFalse(schedule.record_cycle())


class InProcessReflectionTriggerTests(unittest.TestCase):
    def test_run_hermes_reflection_with_deterministic_generator_needs_no_external_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "history").mkdir()
            (state / "strategy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "version": "01",
                        "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
                        "stop_loss_pct": 0.5,
                        "position_size_r": 1.0,
                    },
                    sort_keys=False,
                )
            )
            (state / "goal.yaml").write_text(
                yaml.safe_dump(
                    {"assets": ["BTC/USDT"], "target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0},
                    sort_keys=False,
                )
            )
            (state / "trades.jsonl").write_text(
                "\n".join(
                    json.dumps({"asset": "BTC/USDT", "realized_pnl_pct": -0.01})
                    for _ in range(5)
                )
                + "\n",
                encoding="utf-8",
            )
            (state / "decisions.jsonl").write_text("", encoding="utf-8")

            updated = run_hermes_reflection(
                state,
                env={
                    "HERMES_REFLECT_MIN_CLOSED_TRADES": "1",
                    "HERMES_REFLECT_MIN_NEW_TRADES": "1",
                    "HERMES_REFLECT_MIN_NEW_DECISIONS": "0",
                    "HERMES_REFLECT_COOLDOWN_HOURS": "0",
                },
                proposal_generator=generate_deterministic_asset_proposal,
            )

            hypotheses = (state / "hypotheses.jsonl").read_text(encoding="utf-8")

        self.assertEqual(updated["symbols"]["BTC/USDT"]["entry"]["threshold"], 28)
        self.assertEqual(updated["version"], "02")
        self.assertIn('"provider":"deterministic"', hypotheses)


class BootstrapTests(unittest.TestCase):
    def test_ensure_state_files_seeds_empty_volume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)

            ensure_state_files(state)

            self.assertEqual(yaml.safe_load((state / "goal.yaml").read_text())["asset"], "BTC/USDT")
            self.assertEqual(yaml.safe_load((state / "strategy.yaml").read_text())["version"], "01")
            self.assertTrue((state / "history").is_dir())
            self.assertTrue((state / "trades.jsonl").exists())


class WatcherSyncTests(unittest.TestCase):
    def test_watcher_syncs_reflection_state(self):
        from scripts import hermes_watch

        self.assertIn("reflection_state.json", hermes_watch.STATE_FILES)


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


class MarketAnalysisAdapterTests(unittest.TestCase):
    """market_analysis.fetch is the Docker-Model-Runner-backed local model call
    reflect.py's reflection prompt treats as advisory-only (see
    _fetch_market_analysis) -- these tests only cover the adapter's own
    request/parse contract, not its use inside reflect.py."""

    class _FakeResponse:
        def __init__(self, content: str):
            self._content = content

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": self._content}}]}

    def test_fetch_returns_parsed_analysis_on_success(self):
        content = json.dumps({"bias": "bullish", "confidence": 0.7, "summary": "Momentum looks constructive."})

        with patch(
            "hermes_trading.adapters.market_analysis.httpx.post",
            return_value=self._FakeResponse(content),
        ) as post:
            result = market_analysis.fetch("BTC/USDT", {"score": -0.2}, {})

        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "asset": "BTC/USDT",
                "model": market_analysis.DEFAULT_MODEL,
                "bias": "bullish",
                "confidence": 0.7,
                "summary": "Momentum looks constructive.",
            },
        )
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:12434/engines/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], market_analysis.DEFAULT_MODEL)

    def test_fetch_uses_configured_model_and_port(self):
        content = json.dumps({"bias": "neutral", "confidence": 0.4, "summary": "Range-bound."})

        with patch(
            "hermes_trading.adapters.market_analysis.httpx.post",
            return_value=self._FakeResponse(content),
        ) as post:
            market_analysis.fetch(
                "ETH/USDT", {},
                {"HERMES_MARKET_ANALYSIS_MODEL": "hf.co/custom:Q4", "HERMES_LOCAL_MODEL_PORT": "9999"},
            )

        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:9999/engines/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "hf.co/custom:Q4")

    def test_fetch_raises_on_invalid_bias(self):
        content = json.dumps({"bias": "very bullish", "confidence": 0.7, "summary": "x"})

        with patch("hermes_trading.adapters.market_analysis.httpx.post", return_value=self._FakeResponse(content)):
            with self.assertRaises(ValueError):
                market_analysis.fetch("BTC/USDT", {}, {})

    def test_fetch_clamps_confidence_to_valid_range(self):
        content = json.dumps({"bias": "bearish", "confidence": 4.5, "summary": "x"})

        with patch("hermes_trading.adapters.market_analysis.httpx.post", return_value=self._FakeResponse(content)):
            result = market_analysis.fetch("BTC/USDT", {}, {})

        self.assertEqual(result["confidence"], 1.0)

    def test_fetch_raises_on_malformed_json(self):
        with patch(
            "hermes_trading.adapters.market_analysis.httpx.post",
            return_value=self._FakeResponse("not json"),
        ):
            with self.assertRaises(json.JSONDecodeError):
                market_analysis.fetch("BTC/USDT", {}, {})


class MarketAnalysisReflectionWiringTests(unittest.TestCase):
    """The reflection loop's use of market_analysis.fetch -- opt-in,
    advisory-only, and must never break a cycle regardless of what the
    fetcher does."""

    def _minimal_state(self, state: Path) -> None:
        (state / "history").mkdir()
        (state / "strategy.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": "01",
                    "entry": {"indicator": "rsi_recovery", "threshold": 30, "direction": "long"},
                    "exit": {"indicator": "rsi", "threshold": 55, "take_profit_atr": 5.0},
                    "stop_loss_pct": 0.5,
                },
                sort_keys=False,
            )
        )
        (state / "goal.yaml").write_text(
            yaml.safe_dump(
                {"assets": ["BTC/USDT"], "target_return_30d": 0.05, "max_drawdown": 0.08, "min_sharpe": 1.0},
                sort_keys=False,
            )
        )
        (state / "trades.jsonl").write_text("")
        no_trade = [
            {
                "timestamp": f"2026-08-18T01:{i:02d}:00+00:00", "asset": "BTC/USDT",
                "action": "no_trade", "reason": "entry_condition_not_met", "entry_value": 32.0,
            }
            for i in range(31)
        ]
        (state / "decisions.jsonl").write_text("\n".join(json.dumps(row) for row in no_trade) + "\n")

    def _env(self, **overrides):
        env = {
            "HERMES_REFLECT_MIN_CLOSED_TRADES": "0",
            "HERMES_REFLECT_MIN_NEW_DECISIONS": "3",
            "HERMES_REFLECT_COOLDOWN_HOURS": "0",
        }
        env.update(overrides)
        return env

    def test_run_hermes_reflection_never_hits_the_network_when_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)

            with patch("hermes_trading.adapters.market_analysis.httpx.post") as post:
                run_hermes_reflection(
                    state, env=self._env(), proposal_generator=generate_deterministic_asset_proposal,
                )

        post.assert_not_called()

    def test_default_fetcher_is_disabled_unless_env_flag_is_set(self):
        with patch("hermes_trading.reflect.market_analysis.fetch") as fake_fetch:
            result = _fetch_market_analysis("BTC/USDT", {}, {})

        self.assertIsNone(result)
        fake_fetch.assert_not_called()

    def test_default_fetcher_calls_adapter_when_enabled(self):
        with patch(
            "hermes_trading.reflect.market_analysis.fetch",
            return_value={"schema_version": 1, "asset": "BTC/USDT", "bias": "neutral", "confidence": 0.5, "summary": "x"},
        ) as fake_fetch:
            result = _fetch_market_analysis("BTC/USDT", {"score": -0.2}, {"HERMES_MARKET_ANALYSIS_ENABLED": "true"})

        fake_fetch.assert_called_once_with("BTC/USDT", {"score": -0.2}, {"HERMES_MARKET_ANALYSIS_ENABLED": "true"})
        self.assertEqual(result["bias"], "neutral")

    def test_default_fetcher_captures_failures_instead_of_raising(self):
        with patch("hermes_trading.reflect.market_analysis.fetch", side_effect=RuntimeError("model unreachable")):
            result = _fetch_market_analysis("BTC/USDT", {}, {"HERMES_MARKET_ANALYSIS_ENABLED": "true"})

        self.assertEqual(result, {"error": "model unreachable"})

    def test_opinion_is_recorded_in_the_applied_hypothesis_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)

            def fake_fetcher(asset, snapshot, env):
                return {"bias": "bullish", "confidence": 0.8, "summary": "Looks constructive."}

            run_hermes_reflection(
                state, env=self._env(HERMES_MARKET_ANALYSIS_ENABLED="true"),
                proposal_generator=generate_deterministic_asset_proposal,
                market_analysis_fetcher=fake_fetcher,
            )

            hypotheses = [
                json.loads(line)
                for line in (state / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        applied = next(row for row in hypotheses if row.get("asset") == "BTC/USDT" and row.get("changed"))
        self.assertEqual(applied["market_analysis"], {"bias": "bullish", "confidence": 0.8, "summary": "Looks constructive."})

    def test_market_analysis_call_is_logged_to_its_own_file_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)

            def fake_fetcher(asset, snapshot, env):
                return {"bias": "bearish", "confidence": 0.6, "summary": "Momentum fading."}

            run_hermes_reflection(
                state, env=self._env(HERMES_MARKET_ANALYSIS_ENABLED="true"),
                proposal_generator=generate_deterministic_asset_proposal,
                market_analysis_fetcher=fake_fetcher,
            )

            log_lines = [
                json.loads(line)
                for line in (state / "market_analysis.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(len(log_lines), 1)
        self.assertEqual(log_lines[0]["asset"], "BTC/USDT")
        self.assertEqual(log_lines[0]["status"], "ok")
        self.assertEqual(log_lines[0]["bias"], "bearish")
        self.assertIn("timestamp", log_lines[0])

    def test_market_analysis_error_is_logged_with_error_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)

            def broken_fetcher(asset, snapshot, env):
                raise RuntimeError("model unreachable")

            run_hermes_reflection(
                state, env=self._env(HERMES_MARKET_ANALYSIS_ENABLED="true"),
                proposal_generator=generate_deterministic_asset_proposal,
                market_analysis_fetcher=broken_fetcher,
            )

            log_lines = [
                json.loads(line)
                for line in (state / "market_analysis.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(log_lines[0]["status"], "error")
        self.assertEqual(log_lines[0]["error"], "model unreachable")

    def test_market_analysis_call_is_logged_even_when_no_proposal_is_applied(self):
        # This is the exact gap that made a real investigation harder than it
        # should have been: hypotheses.jsonl only carries market_analysis on
        # applied ("changed") cycles, so a call that happened but produced no
        # change (model_failed here) was invisible there. market_analysis.jsonl
        # must record it regardless of the outcome.
        #
        # Eligibility comes from real new trades (not the no-trade-decisions
        # path) specifically so _no_trade_accessibility_proposal's very first
        # branch (new_trades > 0: return proposal unchanged) applies -- that
        # guard has its own fallback proposal logic for the no-trade-drought
        # path that would otherwise still produce a change here regardless of
        # what the proposal_generator returns.
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)
            trades = [
                {
                    "asset": "BTC/USDT", "closed_at": f"2026-08-18T02:{i:02d}:00+00:00",
                    "realized_pnl_pct": 0.01,
                }
                for i in range(5)
            ]
            (state / "trades.jsonl").write_text("\n".join(json.dumps(row) for row in trades) + "\n")

            def fake_fetcher(asset, snapshot, env):
                return {"bias": "neutral", "confidence": 0.3, "summary": "No clear signal."}

            def failing_proposal_generator(asset, prompt, env, **_ignored):
                return None, {"provider": None, "model": None, "errors": [{"error": "no proposal"}]}

            run_hermes_reflection(
                state, env=self._env(HERMES_MARKET_ANALYSIS_ENABLED="true"),
                proposal_generator=failing_proposal_generator,
                market_analysis_fetcher=fake_fetcher,
            )

            hypotheses = [
                json.loads(line)
                for line in (state / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            log_lines = [
                json.loads(line)
                for line in (state / "market_analysis.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertFalse(any(row.get("changed") for row in hypotheses))
        self.assertTrue(any(row.get("mode") == "hermes_asset_failed" for row in hypotheses))
        self.assertEqual(len(log_lines), 1)
        self.assertEqual(log_lines[0]["bias"], "neutral")

    def test_market_analysis_log_file_is_not_created_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)

            run_hermes_reflection(
                state, env=self._env(), proposal_generator=generate_deterministic_asset_proposal,
            )

            self.assertFalse((state / "market_analysis.jsonl").exists())

    def test_a_raising_injected_fetcher_does_not_break_the_reflection_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            self._minimal_state(state)

            def broken_fetcher(asset, snapshot, env):
                raise RuntimeError("boom")

            updated = run_hermes_reflection(
                state, env=self._env(HERMES_MARKET_ANALYSIS_ENABLED="true"),
                proposal_generator=generate_deterministic_asset_proposal,
                market_analysis_fetcher=broken_fetcher,
            )

        # The cycle must still complete and apply a proposal despite the
        # market-analysis fetcher raising.
        self.assertIn("symbols", updated)


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


class ReflectLoadYamlTests(unittest.TestCase):
    def test_load_yaml_strips_a_leading_utf8_bom(self) -> None:
        # Same landmine as hermes_trading.loop._load_yaml: read_text() without
        # an explicit encoding falls back to the platform locale (cp1252 on
        # Windows), which would decode a UTF-8 BOM as mojibake instead of
        # stripping it, corrupting the first YAML key -- and reflect.py reads
        # strategy.yaml/goal.yaml directly from the local watch_consultant.py
        # cron on this same Windows machine.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy.yaml"
            path.write_bytes(b"\xef\xbb\xbfentry:\n  indicator: rsi\n")

            loaded = _load_yaml(path)

        self.assertIn("entry", loaded)
        self.assertEqual(loaded["entry"]["indicator"], "rsi")


if __name__ == "__main__":
    unittest.main()
