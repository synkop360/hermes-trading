from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.notifications import _build_periodic_summary_message
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.positions import PositionEngine
from hermes_trading.strategy import evaluate_strategy


def candle(close_at: datetime, *, open: str, low: str, close: str, high: str | None = None, history: tuple[dict[str, Decimal], ...] = ()) -> MarketSnapshot:
    return MarketSnapshot(
        asset="BTC/USDT",
        timeframe="1h",
        availability="available",
        decision_action="evaluate",
        reason="closed_candle_available",
        candle=ClosedCandle(
            open_timestamp=close_at - timedelta(hours=1),
            close_timestamp=close_at,
            open=Decimal(open),
            high=Decimal(high) if high is not None else max(Decimal(open), Decimal(close)),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1"),
        ),
        price={"closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),), "candles": history},
    )


def candle_for(asset: str, close_at: datetime, *, open: str, low: str, close: str, high: str | None = None) -> MarketSnapshot:
    return replace(candle(close_at, open=open, low=low, close=close, high=high), asset=asset)


def entry_strategy() -> dict[str, object]:
    return {
        "version": "test",
        "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"},
        "stop_loss_pct": "0.5",
    }


class PositionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state = StateRepository.open(Path(self.temporary_directory.name) / "trading.db")
        self.state.migrate()
        self.engine = PositionEngine(self.state)
        self.entry_candle = candle(
            datetime(2026, 7, 30, 20, tzinfo=UTC), open="100", low="79", close="80"
        )

    def tearDown(self) -> None:
        self.state.close()
        self.temporary_directory.cleanup()

    def test_entry_does_not_close_on_same_candle(self) -> None:
        result = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertTrue(result.processed)
        self.assertEqual(result.position.status.value, "open")
        self.assertEqual(result.closed_positions, ())

    def test_entry_is_blocked_when_take_profit_cannot_cover_modeled_costs(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"take_profit_pct": "0.5"}

        result = self.engine.process_candle(self.entry_candle, strategy)

        self.assertEqual(result.reason, "expected_payoff_below_costs")
        self.assertIsNone(result.position)

    def test_rsi_recovery_requires_a_cross_above_the_threshold_and_price_recovery(self) -> None:
        closes = tuple(Decimal(value) for value in range(100, 85, -1)) + (Decimal("91"),)
        snapshot = MarketSnapshot(
            asset="BTC/USDT", timeframe="1h", availability="available",
            decision_action="evaluate", reason="closed_candle_available",
            candle=ClosedCandle(
                self.entry_candle.candle.open_timestamp, self.entry_candle.candle.close_timestamp,
                Decimal("86"), Decimal("91"), Decimal("86"), Decimal("91"), Decimal("1"),
            ),
            price={"closes": closes},
        )

        signal = evaluate_strategy(
            snapshot,
            {"entry": {"indicator": "rsi_recovery", "threshold": "25", "direction": "long"}},
            None,
        )

        self.assertEqual(signal.action, "enter")
        self.assertEqual(signal.reason, "rsi_recovery")

    def test_rsi_recovery_with_ema_filter_fails_closed_without_full_history(self) -> None:
        strategy = {
            "entry": {
                "indicator": "rsi_recovery", "threshold": "25", "direction": "long",
                "require_ema_rising": True, "trend_ema_period": "200",
            }
        }
        signal = evaluate_strategy(self.entry_candle, strategy, None)

        self.assertEqual(signal.action, "no_trade")

    def test_stop_exit_uses_low_and_charges_fees(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        closed = result.closed_positions[0]
        self.assertEqual(closed.status.value, "closed")
        self.assertLess(closed.realized_pnl, Decimal("0"))
        self.assertGreater(closed.fees, Decimal("0"))

    def test_take_profit_closes_at_the_target_price(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"take_profit_pct": "2.1"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="80", low="80", high="82", close="81"),
            strategy,
        )

        self.assertEqual(result.reason, "take_profit")
        self.assertEqual(result.closed_positions[0].status.value, "closed")
        self.assertGreater(result.closed_positions[0].realized_pnl, Decimal("0"))

    def test_atr_stop_and_target_use_ohlc_history(self) -> None:
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {"stop_atr": "1.5", "take_profit_atr": "2"}
        entry = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open="80", low="79", close="80", history=history)
        opened = self.engine.process_candle(entry, strategy)

        self.assertEqual(opened.position.stop_price, Decimal("74.08"))
        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="80", low="79", high="90", close="89", history=history), strategy
        )

        self.assertEqual(result.reason, "take_profit_atr")
        self.assertGreater(result.closed_positions[0].realized_pnl, Decimal("0"))

    def test_take_profit_atr_is_frozen_at_entry_and_not_shrunk_by_later_atr_contraction(self) -> None:
        wide_history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {"take_profit_atr": "2"}
        entry = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open="80", low="79", close="80", history=wide_history)
        opened = self.engine.process_candle(entry, strategy)

        self.assertEqual(opened.position.take_profit_price, Decimal("88.08"))

        narrow_history = tuple({"high": Decimal("80.5"), "low": Decimal("79.5"), "close": Decimal("80")} for _ in range(15))
        # A fresh ATR recomputed from this narrower history would put take-profit
        # near entry price (~82.08), which candle.high=83 would clear -- but the
        # target frozen at entry (88.08) must not shrink, so the position holds.
        held = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="80", low="80", high="83", close="82", history=narrow_history),
            strategy,
        )

        self.assertEqual(held.reason, "position_open")
        self.assertEqual(held.closed_positions, ())

    def test_rsi_recovery_with_adx_filter_fails_closed_without_ohlc_history(self) -> None:
        strategy = {"entry": {"indicator": "rsi_recovery", "threshold": "25", "direction": "long", "max_adx": "25"}}

        signal = evaluate_strategy(self.entry_candle, strategy, None)

        self.assertEqual(signal.action, "no_trade")

    def test_max_bars_closes_an_open_position(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"max_bars": "2"}
        self.engine.process_candle(self.entry_candle, strategy)

        first = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="80", low="80", close="80"), strategy
        )
        second = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 22, tzinfo=UTC), open="80", low="80", close="80"), strategy
        )

        self.assertEqual(first.position.status.value, "open")
        self.assertEqual(second.reason, "max_bars")
        self.assertEqual(second.closed_positions[0].status.value, "closed")

    def test_exposure_cap_blocks_a_new_entry_at_capacity(self) -> None:
        strategy = entry_strategy()
        strategy["simulation"] = {"max_concurrent_positions": "1"}
        self.engine.process_candle(self.entry_candle, strategy)

        second_entry = candle_for("ETH/USDT", datetime(2026, 7, 30, 20, tzinfo=UTC), open="100", low="79", close="80")
        result = self.engine.process_candle(second_entry, strategy)

        self.assertEqual(result.reason, "exposure_limit")
        self.assertIsNone(result.position)
        self.assertEqual(len(self.state.active_positions()), 1)

    def test_exposure_cap_allows_entry_under_capacity(self) -> None:
        strategy = entry_strategy()
        strategy["simulation"] = {"max_concurrent_positions": "2"}
        self.engine.process_candle(self.entry_candle, strategy)

        second_entry = candle_for("ETH/USDT", datetime(2026, 7, 30, 20, tzinfo=UTC), open="100", low="79", close="80")
        result = self.engine.process_candle(second_entry, strategy)

        self.assertEqual(result.position.status.value, "open")
        self.assertEqual(len(self.state.active_positions()), 2)

    def test_exposure_cap_absent_does_not_limit_entries(self) -> None:
        strategy = entry_strategy()
        self.engine.process_candle(self.entry_candle, strategy)

        second_entry = candle_for("ETH/USDT", datetime(2026, 7, 30, 20, tzinfo=UTC), open="100", low="79", close="80")
        result = self.engine.process_candle(second_entry, strategy)

        self.assertEqual(result.position.status.value, "open")

    def test_exposure_cap_does_not_block_an_exit_of_an_already_open_position(self) -> None:
        strategy = entry_strategy()
        strategy["simulation"] = {"max_concurrent_positions": "1"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            strategy,
        )

        self.assertEqual(result.closed_positions[0].status.value, "closed")

    def test_same_candle_is_not_processed_twice(self) -> None:
        first = self.engine.process_candle(self.entry_candle, entry_strategy())
        second = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertEqual(second.reason, "duplicate_candle")

    def test_open_position_survives_restart(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        database_path = Path(self.temporary_directory.name) / "trading.db"
        self.state.close()
        self.state = StateRepository.open(database_path)
        self.engine = PositionEngine(self.state)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="90", close="95"),
            entry_strategy(),
        )

        self.assertTrue(result.processed)
        self.assertEqual(result.position.status.value, "open")

    def test_projection_records_fills_and_positions_once_per_committed_candle(self) -> None:
        artifact_directory = Path(self.temporary_directory.name) / "artifacts"
        engine = PositionEngine(self.state, ArtifactProjector(artifact_directory))
        engine.process_candle(self.entry_candle, entry_strategy())
        engine.process_candle(self.entry_candle, entry_strategy())
        engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        self.assertEqual(_jsonl_count(artifact_directory / "fills.jsonl"), 2)
        self.assertEqual(_jsonl_count(artifact_directory / "positions.jsonl"), 2)
        self.assertEqual(_jsonl_count(artifact_directory / "trades.jsonl"), 1)

    def test_stale_closed_candle_persists_one_no_trade_decision(self) -> None:
        stale = MarketSnapshot(**{**self.entry_candle.__dict__, "availability": "stale", "decision_action": "no_trade", "reason": "stale_closed_candle"})

        first = self.engine.process_candle(stale, entry_strategy())
        second = self.engine.process_candle(stale, entry_strategy())

        self.assertTrue(first.processed)
        self.assertEqual(first.decision.action, "no_trade")
        self.assertEqual(first.orders, ())
        self.assertFalse(second.processed)

    def test_older_candle_cannot_close_open_position(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        older = candle(datetime(2026, 7, 30, 19, tzinfo=UTC), open="80", low="60", close="70")

        result = self.engine.process_candle(older, entry_strategy())

        self.assertTrue(result.processed)
        self.assertEqual(result.reason, "out_of_order_candle")
        self.assertEqual(result.position.status.value, "open")
        self.assertEqual(result.orders, ())

    def test_simulation_rates_must_be_less_than_one(self) -> None:
        for setting in ("slippage_rate", "taker_fee_rate", "maker_fee_rate"):
            strategy = entry_strategy()
            strategy["simulation"] = {setting: "1"}
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.engine.process_candle(self.entry_candle, strategy)

    def test_stop_always_uses_taker_fee_even_with_maker_explicit_exit(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "70", "fee": "maker"}
        strategy["simulation"] = {"taker_fee_rate": "0.01", "maker_fee_rate": "0.5"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            strategy,
        )

        exit_order = result.orders[0]
        self.assertEqual(exit_order.fees, exit_order.filled_quantity * exit_order.average_fill_price * Decimal("0.01"))

    def test_entry_defaults_to_taker_fee_and_market_order(self) -> None:
        strategy = entry_strategy()
        strategy["simulation"] = {"taker_fee_rate": "0.01", "maker_fee_rate": "0.001"}

        result = self.engine.process_candle(self.entry_candle, strategy)

        entry_order = result.orders[0]
        self.assertEqual(entry_order.order_type, "market")
        self.assertEqual(entry_order.fees, entry_order.filled_quantity * entry_order.average_fill_price * Decimal("0.01"))

    def test_entry_uses_maker_fee_and_limit_order_when_configured(self) -> None:
        strategy = entry_strategy()
        strategy["entry"]["fee"] = "maker"
        strategy["simulation"] = {"taker_fee_rate": "0.01", "maker_fee_rate": "0.001"}

        result = self.engine.process_candle(self.entry_candle, strategy)

        entry_order = result.orders[0]
        self.assertEqual(entry_order.order_type, "limit")
        self.assertEqual(entry_order.fees, entry_order.filled_quantity * entry_order.average_fill_price * Decimal("0.001"))

    def test_maker_entry_can_clear_the_cost_gate_where_taker_could_not(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"take_profit_pct": "0.5"}
        # taker + taker exit-leg-assumed + 2*slippage = 0.2+0.2+0.2 = 0.6% > 0.5% target: blocked.
        # maker entry + taker exit-leg-assumed + 2*slippage = 0.05+0.2+0.2 = 0.45% <= 0.5%: allowed.
        strategy["simulation"] = {
            "taker_fee_rate": "0.002", "maker_fee_rate": "0.0005", "slippage_rate": "0.001",
            "minimum_expected_edge_pct": "0",
        }
        blocked = self.engine.process_candle(self.entry_candle, strategy)
        self.assertEqual(blocked.reason, "expected_payoff_below_costs")

        strategy["entry"]["fee"] = "maker"
        allowed = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="79", close="80"), strategy,
        )

        self.assertEqual(allowed.position.status.value, "open")

    def test_entry_uses_flat_quote_notional_when_no_equity_is_tracked(self) -> None:
        strategy = entry_strategy()
        strategy["simulation"] = {"quote_notional": "7"}

        result = self.engine.process_candle(self.entry_candle, strategy)

        entry_order = result.orders[0]
        self.assertAlmostEqual(
            float(entry_order.filled_quantity * entry_order.average_fill_price), 7.0, places=6,
        )

    def test_entry_compounds_off_equity_when_tracked(self) -> None:
        # stop_loss_pct=0.5 -> stop_fraction is exactly 0.5% of fill_price
        # regardless of price, so: risk_budget = 50 * 0.0025 * 0.5 = 0.0625,
        # Cost-inclusive sizing is capped at 5% of equity: 2.5.
        engine = PositionEngine(self.state, equity=Decimal("50"))
        strategy = entry_strategy()
        strategy["position_size_r"] = "0.5"

        result = engine.process_candle(self.entry_candle, strategy)

        entry_order = result.orders[0]
        self.assertAlmostEqual(
            float(entry_order.filled_quantity * entry_order.average_fill_price), 2.5, places=6,
        )

    def test_entry_notional_grows_with_equity_before_order_cap(self) -> None:
        strategy = entry_strategy()
        strategy["simulation"] = {"max_order_quote": "100"}
        strategy["position_size_r"] = "0.5"
        small = PositionEngine(self.state, equity=Decimal("50")).process_candle(self.entry_candle, strategy)

        with tempfile.TemporaryDirectory() as other_dir:
            other_state = StateRepository.open(Path(other_dir) / "trading.db")
            other_state.migrate()
            large = PositionEngine(other_state, equity=Decimal("500")).process_candle(self.entry_candle, strategy)
            other_state.close()

        small_notional = small.orders[0].filled_quantity * small.orders[0].average_fill_price
        large_notional = large.orders[0].filled_quantity * large.orders[0].average_fill_price
        self.assertAlmostEqual(float(large_notional / small_notional), 10.0, places=6)

    def test_entry_notional_never_exceeds_half_of_equity(self) -> None:
        engine = PositionEngine(self.state, equity=Decimal("50"))
        strategy = entry_strategy()
        strategy["position_size_r"] = "1.0"
        strategy["simulation"] = {"risk_per_trade_pct": "0.5"}  # deliberately huge

        result = engine.process_candle(self.entry_candle, strategy)

        entry_order = result.orders[0]
        notional = entry_order.filled_quantity * entry_order.average_fill_price
        self.assertLessEqual(notional, Decimal("25"))

    def test_trailing_atr_ratchets_stop_up_on_a_hold_cycle(self) -> None:
        # 15 identical (high=82, low=78, close=80) candles -> ATR(14) is exactly
        # 4 (true range = max(4, |82-80|, |78-80|) = 4 every bar).
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {"trailing_atr": "1.5"}
        entry = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open="80", low="79", close="80", history=history)
        opened = self.engine.process_candle(entry, strategy)
        self.assertEqual(opened.position.stop_price, Decimal("79.6796"))  # stop_loss_pct=0.5% initial stop
        self.assertIsNone(opened.position.take_profit_price)

        held = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="88", low="85", high="91", close="90", history=history),
            strategy,
        )

        self.assertEqual(held.reason, "position_open")
        self.assertEqual(held.closed_positions, ())
        self.assertEqual(held.position.stop_price, Decimal("84"))  # 90 - 4*1.5

    def test_trailing_atr_never_lowers_the_stop(self) -> None:
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {"trailing_atr": "1.5"}
        entry = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open="80", low="79", close="80", history=history)
        self.engine.process_candle(entry, strategy)
        self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="88", low="85", high="91", close="90", history=history),
            strategy,
        )

        # A pullback that stays above the ratcheted 84 stop must not drag it
        # back down toward this candle's own (lower) implied trailing level.
        held = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 22, tzinfo=UTC), open="86", low="84.5", high="87", close="85", history=history),
            strategy,
        )

        self.assertEqual(held.position.stop_price, Decimal("84"))

    def test_trailing_atr_ratchet_survives_a_fresh_repository_read(self) -> None:
        # Validates the persistence-layer fix this feature depends on: the
        # positions UPSERT previously dropped stop_price on conflict entirely.
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {"trailing_atr": "1.5"}
        entry = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open="80", low="79", close="80", history=history)
        self.engine.process_candle(entry, strategy)
        self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="88", low="85", high="91", close="90", history=history),
            strategy,
        )

        reread = self.state.open_position("BTC/USDT")

        self.assertEqual(reread.stop_price, Decimal("84"))

    def test_trailing_atr_without_configuration_does_not_move_the_stop(self) -> None:
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {"stop_atr": "1.5"}
        entry = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open="80", low="79", close="80", history=history)
        opened = self.engine.process_candle(entry, strategy)

        held = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="88", low="85", high="91", close="90", history=history),
            strategy,
        )

        self.assertEqual(held.position.stop_price, opened.position.stop_price)

    def _rsi_recovered_candle(self, *, low: str, close: str) -> MarketSnapshot:
        rising_closes = tuple(Decimal(value) for value in range(80, 95))
        return MarketSnapshot(
            asset="BTC/USDT", timeframe="1h", availability="available",
            decision_action="evaluate", reason="closed_candle_available",
            candle=ClosedCandle(
                self.entry_candle.candle.open_timestamp + timedelta(hours=1),
                self.entry_candle.candle.close_timestamp + timedelta(hours=1),
                Decimal(low), Decimal(close), Decimal(low), Decimal(close), Decimal("1"),
            ),
            price={"closes": rising_closes},
        )

    def test_explicit_exit_holds_when_favorable_move_does_not_clear_costs(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "50"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(self._rsi_recovered_candle(low="80", close="80.2"), strategy)

        self.assertEqual(result.reason, "position_open")
        self.assertEqual(result.closed_positions, ())
        self.assertEqual(result.position.status.value, "open")

    def test_explicit_exit_closes_once_favorable_move_clears_costs(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "50"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "explicit_exit")
        closed = result.closed_positions[0]
        self.assertEqual(closed.status.value, "closed")
        self.assertGreater(closed.realized_pnl, Decimal("0"))
        exit_order = result.orders[0]
        self.assertEqual(exit_order.order_type, "market")
        self.assertEqual(exit_order.fees, exit_order.filled_quantity * exit_order.average_fill_price * Decimal("0.008"))

    def test_explicit_exit_accepts_explicit_taker_pricing(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "50", "fee": "taker"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "explicit_exit")
        exit_order = result.orders[0]
        self.assertEqual(exit_order.order_type, "market")
        self.assertEqual(exit_order.fees, exit_order.filled_quantity * exit_order.average_fill_price * Decimal("0.008"))

    def test_trailing_overrides_explicit_exit_holds_instead_of_closing_when_enabled(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5",
            "trailing_overrides_explicit_exit": True,
        }
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "position_open")
        self.assertEqual(result.closed_positions, ())
        self.assertEqual(result.position.status.value, "open")

    def test_trailing_overrides_explicit_exit_is_a_noop_without_trailing_atr(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "50", "trailing_overrides_explicit_exit": True}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "explicit_exit")
        self.assertEqual(result.closed_positions[0].status.value, "closed")

    def test_trailing_overrides_explicit_exit_defaults_to_false(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "50", "trailing_atr": "1.5"}
        self.engine.process_candle(self.entry_candle, strategy)

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "explicit_exit")
        self.assertEqual(result.closed_positions[0].status.value, "closed")

    def _rsi_recovered_candle_with_atr_history(self, *, low: str, close: str, history) -> MarketSnapshot:
        base = self._rsi_recovered_candle(low=low, close=close)
        price = dict(base.price)
        price["candles"] = history
        return replace(base, price=price)

    def test_trailing_activation_atr_still_lets_explicit_exit_close_a_small_move(self) -> None:
        # ATR(14) = 4 exactly with this flat history (true range = max(4, |82-80|, |78-80|)).
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5",
            "trailing_overrides_explicit_exit": True, "trailing_activation_atr": "1.0",
        }
        self.engine.process_candle(self.entry_candle, strategy)

        # Entry fill ~80.08; close=82 is a ~1.9 move, well under 1.0 * ATR(4) -- activation
        # threshold not cleared, so explicit_exit should still fire normally.
        result = self.engine.process_candle(
            self._rsi_recovered_candle_with_atr_history(low="82", close="82", history=history), strategy,
        )

        self.assertEqual(result.reason, "explicit_exit")
        self.assertEqual(result.closed_positions[0].status.value, "closed")

    def test_trailing_activation_atr_defers_to_trailing_once_move_clears_it(self) -> None:
        history = tuple({"high": Decimal("82"), "low": Decimal("78"), "close": Decimal("80")} for _ in range(15))
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5",
            "trailing_overrides_explicit_exit": True, "trailing_activation_atr": "1.0",
        }
        self.engine.process_candle(self.entry_candle, strategy)

        # Entry fill ~80.08; close=90 is a ~9.9 move, well past 1.0 * ATR(4) -- activation
        # clears, so explicit_exit must NOT fire even though RSI/cost conditions are met.
        result = self.engine.process_candle(
            self._rsi_recovered_candle_with_atr_history(low="90", close="90", history=history), strategy,
        )

        self.assertEqual(result.reason, "position_open")
        self.assertEqual(result.closed_positions, ())

    def test_trailing_activation_atr_falls_back_to_explicit_exit_without_atr_history(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5",
            "trailing_overrides_explicit_exit": True, "trailing_activation_atr": "1.0",
        }
        self.engine.process_candle(self.entry_candle, strategy)

        # No OHLC history supplied -> _atr() returns None -> fails closed to normal
        # explicit_exit behavior rather than silently deferring forever.
        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "explicit_exit")
        self.assertEqual(result.closed_positions[0].status.value, "closed")

    def _rsi_recovered_candle_at(self, hours: int, *, low: str, close: str) -> MarketSnapshot:
        base = self._rsi_recovered_candle(low=low, close=close)
        return replace(
            base,
            candle=replace(
                base.candle,
                open_timestamp=self.entry_candle.candle.open_timestamp + timedelta(hours=hours),
                close_timestamp=self.entry_candle.candle.close_timestamp + timedelta(hours=hours),
            ),
        )

    def test_partial_close_pct_requires_trailing_atr(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {"indicator": "rsi", "threshold": "50", "partial_close_pct": "0.5"}
        self.engine.process_candle(self.entry_candle, strategy)

        with self.assertRaises(ValueError):
            self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

    def test_partial_close_pct_rejects_a_base_trailing_override(self) -> None:
        # A base-config trailing_overrides_explicit_exit: true is checked
        # first, unconditionally -- it would permanently suppress
        # explicit_exit/partial_exit before partial_close_pct's own
        # partial_exit_count gate ever runs, silently making it dead code.
        # This exact misconfiguration was deployed once and scored
        # statistically identical to a plain full-override strategy on a
        # real backtest before being caught -- must fail loudly instead.
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5",
            "trailing_overrides_explicit_exit": True, "partial_close_pct": "0.5",
        }
        self.engine.process_candle(self.entry_candle, strategy)

        with self.assertRaises(ValueError):
            self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

    def test_partial_close_pct_must_be_a_fraction(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5", "partial_close_pct": "1.5",
        }
        self.engine.process_candle(self.entry_candle, strategy)

        with self.assertRaises(ValueError):
            self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

    def test_partial_close_pct_banks_a_fraction_and_keeps_the_remainder_open(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5", "partial_close_pct": "0.5",
        }
        opened = self.engine.process_candle(self.entry_candle, strategy)
        original_quantity = opened.position.quantity
        original_position_id = opened.position.position_id

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        self.assertEqual(result.reason, "partial_exit")
        self.assertEqual(len(result.closed_positions), 1)
        closed_leg = result.closed_positions[0]
        self.assertEqual(closed_leg.status.value, "closed")
        self.assertNotEqual(closed_leg.position_id, original_position_id)
        self.assertEqual(closed_leg.quantity, original_quantity * Decimal("0.5"))
        self.assertGreater(closed_leg.realized_pnl, Decimal("0"))

        remainder = result.position
        self.assertEqual(remainder.status.value, "open")
        self.assertEqual(remainder.position_id, original_position_id)
        self.assertEqual(remainder.quantity, original_quantity - closed_leg.quantity)
        self.assertEqual(remainder.partial_exit_count, 1)

    def test_partial_close_pct_splits_entry_fees_between_the_two_legs(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5", "partial_close_pct": "0.5",
        }
        opened = self.engine.process_candle(self.entry_candle, strategy)
        entry_fee = opened.position.fees

        result = self.engine.process_candle(self._rsi_recovered_candle(low="82", close="82"), strategy)

        closed_leg = result.closed_positions[0]
        remainder = result.position
        # 50/50 split: the remainder keeps exactly its half of the entry fee
        # (no exit fee charged yet -- it's still open); the closed leg keeps
        # its half plus the exit fee it just paid, so it's strictly larger.
        self.assertEqual(remainder.fees, entry_fee * Decimal("0.5"))
        self.assertGreater(closed_leg.fees, entry_fee * Decimal("0.5"))

    def test_partial_close_pct_disables_explicit_exit_after_the_first_partial_close(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5", "partial_close_pct": "0.5",
        }
        self.engine.process_candle(self.entry_candle, strategy)
        self.engine.process_candle(self._rsi_recovered_candle_at(1, low="82", close="82"), strategy)

        # A second explicit_exit-qualifying candle must NOT close the
        # remainder -- partial_exit_count > 0 forces trailing/stop_loss/
        # max_bars governance only, exactly like an unconditional
        # trailing_overrides_explicit_exit, but scoped to this one position.
        result = self.engine.process_candle(self._rsi_recovered_candle_at(2, low="83", close="83"), strategy)

        self.assertEqual(result.reason, "position_open")
        self.assertEqual(result.closed_positions, ())

    def test_partial_close_pct_remainder_can_still_close_via_stop_loss(self) -> None:
        strategy = entry_strategy()
        strategy["exit"] = {
            "indicator": "rsi", "threshold": "50", "trailing_atr": "1.5", "partial_close_pct": "0.5",
        }
        self.engine.process_candle(self.entry_candle, strategy)
        after_partial = self.engine.process_candle(self._rsi_recovered_candle_at(1, low="82", close="82"), strategy)
        remainder = after_partial.position

        result = self.engine.process_candle(
            candle_for(
                "BTC/USDT", self.entry_candle.candle.close_timestamp + timedelta(hours=2),
                open="82", low=str(remainder.stop_price - Decimal("1")), close="82",
            ),
            strategy,
        )

        self.assertEqual(result.reason, "stop_loss")
        self.assertEqual(len(result.closed_positions), 1)
        self.assertEqual(result.closed_positions[0].quantity, remainder.quantity)

    def test_projection_resumes_idempotently_after_each_output_step(self) -> None:
        for fail_at in range(1, 9):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as directory:
                repository = StateRepository.open(Path(directory) / "trading.db")
                repository.migrate()
                artifact_directory = Path(directory) / "artifacts"
                PositionEngine(repository, ArtifactProjector(artifact_directory)).process_candle(self.entry_candle, entry_strategy())
                projector = _FailingProjector(artifact_directory, fail_at)
                close_result = PositionEngine(repository, projector).process_candle(
                    candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
                    entry_strategy(),
                )
                self.assertIsNotNone(close_result.projection_error)

                PositionEngine(repository, ArtifactProjector(projector.state_dir)).process_candle(
                    candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
                    entry_strategy(),
                )

                self.assertEqual(_jsonl_count(projector.state_dir / "decisions.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "orders.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "fills.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "positions.jsonl"), 2)
                self.assertEqual(_jsonl_count(projector.state_dir / "trades.jsonl"), 1)
                for snapshot_name in ArtifactProjector.SNAPSHOT_NAMES:
                    self.assertTrue((projector.state_dir / snapshot_name).exists())
                repository.close()

    def test_healthy_duplicate_does_not_reconcile(self) -> None:
        projector = _CountingProjector(Path(self.temporary_directory.name) / "counting")
        engine = PositionEngine(self.state, projector)
        engine.process_candle(self.entry_candle, entry_strategy())

        duplicate = engine.process_candle(self.entry_candle, entry_strategy())

        self.assertIsNone(duplicate.projection_error)
        self.assertEqual(projector.reconcile_calls, 0)

    def test_each_committed_outcome_has_its_own_projection_acknowledgement(self) -> None:
        directory = Path(self.temporary_directory.name) / "concurrent-outbox"
        failing = _PersistentFailProjector(directory)
        first = PositionEngine(self.state, failing).process_candle(self.entry_candle, entry_strategy())
        second_snapshot = MarketSnapshot(**{
            **self.entry_candle.__dict__,
            "asset": "ETH/USDT",
            "candle": ClosedCandle(
                self.entry_candle.candle.open_timestamp,
                self.entry_candle.candle.close_timestamp,
                self.entry_candle.candle.open,
                self.entry_candle.candle.high,
                self.entry_candle.candle.low,
                self.entry_candle.candle.close,
                self.entry_candle.candle.volume,
            ),
        })
        second = PositionEngine(self.state, failing).process_candle(second_snapshot, entry_strategy())

        self.assertIsNotNone(first.projection_error)
        self.assertIsNotNone(second.projection_error)
        pending = self.state.pending_projection_items()
        self.assertEqual(len(pending), 2)

        database_path = Path(self.temporary_directory.name) / "trading.db"
        def acknowledge(item_id: str) -> bool:
            repository = StateRepository.open(database_path)
            try:
                return repository.acknowledge_projection_item(item_id)
            finally:
                repository.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            acknowledgements = list(executor.map(acknowledge, [item.item_id for item in pending]))
        self.assertEqual(acknowledgements, [True, True])
        remaining = self.state.pending_projection_items()
        self.assertEqual(remaining, ())

    def test_pending_projection_is_retried_when_snapshot_has_no_candle(self) -> None:
        directory = Path(self.temporary_directory.name) / "no-candle-recovery"
        failing = _FailingProjector(directory, 1)
        first = PositionEngine(self.state, failing).process_candle(self.entry_candle, entry_strategy())
        self.assertIsNotNone(first.projection_error)

        unavailable = MarketSnapshot(**{
            **self.entry_candle.__dict__,
            "availability": "unavailable",
            "decision_action": "no_trade",
            "reason": "price_unavailable",
            "candle": None,
        })
        retry = PositionEngine(self.state, _PersistentFailProjector(directory)).process_candle(
            unavailable, entry_strategy()
        )

        self.assertIsNotNone(retry.projection_error)
        self.assertTrue(self.state.pending_projection_items())

    def test_missing_projector_never_acknowledges_canonical_projection(self) -> None:
        result = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertEqual(result.projection_error, "no artifact projector is configured")
        self.assertEqual(len(self.state.pending_projection_items()), 1)

    def test_restart_projects_one_pending_item_without_duplicate_events(self) -> None:
        directory = Path(self.temporary_directory.name) / "restart-outbox"
        failed = PositionEngine(self.state, _FailingProjector(directory, 2))
        failed.process_candle(self.entry_candle, entry_strategy())
        self.assertEqual(len(self.state.pending_projection_items()), 1)

        resumed = PositionEngine(self.state, ArtifactProjector(directory))
        retry = resumed.process_candle(self.entry_candle, entry_strategy())
        duplicate = resumed.process_candle(self.entry_candle, entry_strategy())

        self.assertIsNone(retry.projection_error)
        self.assertIsNone(duplicate.projection_error)
        self.assertFalse(self.state.pending_projection_items())
        self.assertEqual(_jsonl_count(directory / "decisions.jsonl"), 1)

    def test_recovery_projects_each_pending_position_transition_as_committed(self) -> None:
        """A later close must not rewrite an earlier pending open transition."""
        directory = Path(self.temporary_directory.name) / "immutable-outbox"
        failed = PositionEngine(self.state, _PersistentFailProjector(directory))
        entry = failed.process_candle(self.entry_candle, entry_strategy())
        exit_result = failed.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )
        self.assertIsNotNone(entry.projection_error)
        self.assertIsNotNone(exit_result.projection_error)
        self.assertEqual(len(self.state.pending_projection_items()), 2)

        resumed = PositionEngine(self.state, ArtifactProjector(directory))
        self.assertIsNone(resumed.recover_pending_projections())

        positions = [
            json.loads(line)
            for line in (directory / "positions.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        trades = [
            json.loads(line)
            for line in (directory / "trades.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([position["status"] for position in positions], ["open", "closed"])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["status"], "closed")
        self.assertFalse(self.state.pending_projection_items())

    def test_migration_converts_legacy_global_projection_marker_to_items(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        with self.state._connection:
            self.state._connection.execute(
                """INSERT INTO incidents (incident_id, status, reason, severity, revision, opened_at, resolved_at)
                VALUES ('projection:pending', 'open', 'legacy', 'warning', 1, '2026-07-30T20:00:00+00:00', NULL)"""
            )

        self.state.migrate()

        self.assertFalse(self.state._connection.execute(
            "SELECT 1 FROM incidents WHERE incident_id = 'projection:pending' AND status = 'open'"
        ).fetchone())
        self.assertTrue(self.state.pending_projection_items())

    def test_failed_projection_retries_only_while_pending_and_surfaces_error(self) -> None:
        directory = Path(self.temporary_directory.name) / "pending"
        failed = _FailingProjector(directory, 1)
        first = PositionEngine(self.state, failed).process_candle(self.entry_candle, entry_strategy())
        self.assertIsNotNone(first.projection_error)

        retrying = _PersistentFailProjector(directory)
        second = PositionEngine(self.state, retrying).process_candle(self.entry_candle, entry_strategy())
        third = PositionEngine(self.state, retrying).process_candle(self.entry_candle, entry_strategy())

        self.assertIsNotNone(second.projection_error)
        self.assertIsNotNone(third.projection_error)
        self.assertEqual(retrying.reconcile_calls, 2)

    def test_canonical_jsonl_events_feed_periodic_summary(self) -> None:
        artifact_directory = Path(self.temporary_directory.name) / "summary-artifacts"
        engine = PositionEngine(self.state, ArtifactProjector(artifact_directory))
        engine.process_candle(self.entry_candle, entry_strategy())
        engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        summary = _build_periodic_summary_message(
            artifact_directory,
            "1h",
            datetime(2026, 7, 30, 19, tzinfo=UTC),
            datetime(2026, 7, 30, 22, tzinfo=UTC),
        )

        self.assertIn("Decisions: total=2", summary)
        self.assertIn("Trades: total=1", summary)
        self.assertIn("Orders: total=2", summary)
        self.assertNotIn("total_pnl=0 |", summary)


def _jsonl_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class _FailingProjector(ArtifactProjector):
    def __init__(self, state_dir: Path, fail_at: int) -> None:
        super().__init__(state_dir)
        self.fail_at = fail_at
        self.operations = 0

    def _maybe_fail(self) -> None:
        self.operations += 1
        if self.operations == self.fail_at:
            raise OSError("injected projection failure")

    def append_event(self, name: str, payload: object) -> None:
        self._maybe_fail()
        super().append_event(name, payload)

    def write_snapshot(self, name: str, payload: object) -> None:
        self._maybe_fail()
        super().write_snapshot(name, payload)


class _CountingProjector(ArtifactProjector):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.reconcile_calls = 0

    def reconcile(self, repository: StateRepository) -> None:
        self.reconcile_calls += 1
        super().reconcile(repository)


class _PersistentFailProjector(_CountingProjector):
    def project_pending_item(self, repository: StateRepository, item: object) -> None:
        self.reconcile_calls += 1
        raise OSError("projection storage still unavailable")
