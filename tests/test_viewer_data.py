import json
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from hermes_trading.strategy import _atr, _rsi
from hermes_trading.viewer_data import (
    Candle,
    asset_strategy_config,
    compute_indicator_series,
    load_closed_trades,
    load_open_position,
)


def _candle(hour: int, *, high: str, low: str, close: str) -> Candle:
    return Candle(
        timestamp=datetime(2026, 8, 1, hour, tzinfo=UTC),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def _rising_candles(count: int) -> tuple[Candle, ...]:
    return tuple(_candle(hour, high=str(80 + hour + 1), low=str(80 + hour - 1), close=str(80 + hour)) for hour in range(count))


class IndicatorSeriesTests(unittest.TestCase):
    def test_first_warmup_entries_are_none(self) -> None:
        series = compute_indicator_series(_rising_candles(20), {})

        self.assertTrue(all(value is None for value in series.rsi[:14]))
        self.assertTrue(all(value is None for value in series.atr[:14]))

    def test_matches_directly_computed_strategy_rsi_and_atr(self) -> None:
        candles = _rising_candles(20)

        series = compute_indicator_series(candles, {})

        closes = [candle.close for candle in candles]
        expected_rsi_at_14 = _rsi(closes[0:15])
        self.assertEqual(series.rsi[14], expected_rsi_at_14)
        expected_rsi_at_19 = _rsi(closes[5:20])
        self.assertEqual(series.rsi[19], expected_rsi_at_19)

        from types import SimpleNamespace

        ohlc = [{"high": c.high, "low": c.low, "close": c.close} for c in candles]
        expected_atr_at_14 = _atr(SimpleNamespace(price={"candles": ohlc[0:15]}))
        self.assertEqual(series.atr[14], expected_atr_at_14)

    def test_reads_entry_and_exit_thresholds_from_asset_strategy(self) -> None:
        asset_strategy = {
            "entry": {"indicator": "rsi", "threshold": "27"},
            "exit": {"indicator": "rsi", "threshold": "55"},
        }

        series = compute_indicator_series(_rising_candles(20), asset_strategy)

        self.assertEqual(series.entry_indicator, "rsi")
        self.assertEqual(series.entry_threshold, Decimal("27"))
        self.assertEqual(series.exit_indicator, "rsi")
        self.assertEqual(series.exit_threshold, Decimal("55"))

    def test_missing_entry_and_exit_config_yields_none_thresholds(self) -> None:
        series = compute_indicator_series(_rising_candles(20), {})

        self.assertIsNone(series.entry_indicator)
        self.assertIsNone(series.entry_threshold)
        self.assertIsNone(series.exit_indicator)
        self.assertIsNone(series.exit_threshold)


class AssetStrategyConfigTests(unittest.TestCase):
    def test_merges_symbol_override_over_global_defaults(self) -> None:
        strategy = {
            "entry": {"indicator": "rsi", "threshold": 30},
            "symbols": {"BTC/USDT": {"entry": {"threshold": 29}}},
        }

        resolved = asset_strategy_config(strategy, "BTC/USDT")

        self.assertEqual(resolved["entry"]["threshold"], 29)
        self.assertEqual(resolved["entry"]["indicator"], "rsi")


class LoadClosedTradesTests(unittest.TestCase):
    def test_filters_by_asset_and_window_and_sorts_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            (state_dir / "trades.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"asset": "BTC/USDT", "closed_at": "2026-08-01T12:00:00+00:00", "realized_pnl": "1"}),
                        json.dumps({"asset": "ETH/USDT", "closed_at": "2026-08-01T13:00:00+00:00", "realized_pnl": "1"}),
                        json.dumps({"asset": "BTC/USDT", "closed_at": "2026-08-01T06:00:00+00:00", "realized_pnl": "-1"}),
                        json.dumps({"asset": "BTC/USDT", "closed_at": "2026-08-01T14:00:00+00:00", "realized_pnl": "2"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            trades = load_closed_trades(state_dir, "BTC/USDT", datetime(2026, 8, 1, 10, tzinfo=UTC))

        self.assertEqual([trade["closed_at"] for trade in trades], [
            "2026-08-01T12:00:00+00:00",
            "2026-08-01T14:00:00+00:00",
        ])

    def test_returns_empty_list_when_trades_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            trades = load_closed_trades(Path(temp_dir), "BTC/USDT", datetime(2026, 8, 1, tzinfo=UTC))

        self.assertEqual(trades, [])


class LoadOpenPositionTests(unittest.TestCase):
    def test_reads_take_profit_price_for_the_matching_open_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            (state_dir / "positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "position_id": "pos-1",
                                "asset": "BTC/USDT",
                                "status": "open",
                                "quantity": "0.0001",
                                "entry_price": "64000",
                                "stop_price": "63000",
                                "take_profit_price": "66000",
                                "opened_at": "2026-08-01T12:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            position = load_open_position(state_dir, "BTC/USDT")

        self.assertIsNotNone(position)
        self.assertEqual(position.take_profit_price, Decimal("66000"))
        self.assertEqual(position.stop_price, Decimal("63000"))

    def test_returns_none_when_no_open_position_matches_the_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            (state_dir / "positions.json").write_text(json.dumps({"positions": []}), encoding="utf-8")

            self.assertIsNone(load_open_position(state_dir, "BTC/USDT"))

    def test_returns_none_when_take_profit_price_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            (state_dir / "positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "position_id": "pos-1",
                                "asset": "ETH/USDT",
                                "status": "open",
                                "quantity": "0.001",
                                "entry_price": "1900",
                                "stop_price": "1889",
                                "take_profit_price": None,
                                "opened_at": "2026-08-01T12:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            position = load_open_position(state_dir, "ETH/USDT")

        self.assertIsNotNone(position)
        self.assertIsNone(position.take_profit_price)

    def test_returns_none_when_positions_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(load_open_position(Path(temp_dir), "BTC/USDT"))


if __name__ == "__main__":
    unittest.main()
