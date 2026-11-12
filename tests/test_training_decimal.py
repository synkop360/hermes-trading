import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermes_trading.loop import _write_json, run_loop
from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.run import ensure_state_files


class TrainingDecimalJournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_serializes_decimal_price_losslessly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot_path = Path(temporary_directory) / "snapshot.json"
            await _write_json(
                snapshot_path,
                {"reference_price": Decimal("0.0001234567890123456789")},
            )
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(
            snapshot["reference_price"],
            "0.0001234567890123456789",
        )

    async def test_training_cycles_keep_decimal_prices_through_entry_and_stop_exit(self) -> None:
        exact_price = Decimal("100.1234567890123456789012345678")
        entry = _snapshot("2026-07-30T20:00:00+00:00", exact_price, Decimal("99"))
        stop = _snapshot("2026-07-30T21:00:00+00:00", Decimal("100"), Decimal("97"))
        requests = 0

        async def collect_closed_candles(_, assets):
            nonlocal requests
            requests += 1
            if requests == 2:
                self.assertEqual(_json_lines(state / "trades.jsonl"), [])
                return {"BTC/USDT": stop}
            return {"BTC/USDT": entry}

        async def stop_after_second_cycle(seconds: float) -> None:
            if requests >= 2:
                raise asyncio.CancelledError

        with tempfile.TemporaryDirectory() as temporary_directory:
            state = Path(temporary_directory)
            ensure_state_files(state)
            (state / "strategy.yaml").write_text(
                "version: decimal-test\n"
                "entry:\n"
                "  indicator: rsi\n"
                "  threshold: '30'\n"
                "  direction: long\n"
                "stop_loss_pct: 0.5\n",
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"HERMES_TRADING_MODE": "training"},
                    clear=False,
                ),
                patch("hermes_trading.loop.MarketDataCoordinator.collect", collect_closed_candles),
                patch(
                    "hermes_trading.loop.asyncio.sleep",
                    side_effect=stop_after_second_cycle,
                ),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await run_loop("BTC/USDT", state, interval_seconds=60)

            decisions = _json_lines(state / "decisions.jsonl")
            orders = _json_lines(state / "orders.jsonl")
            fills = _json_lines(state / "fills.jsonl")
            positions = _json_lines(state / "positions.jsonl")
            trade = _single_json_line(state / "trades.jsonl")
            heartbeat = json.loads((state / "heartbeat.json").read_text(encoding="utf-8"))

        entry_price = "100.2235802458013580245801358"
        stop_price = "99.72246234457235123445723512"
        exit_price = "99.62273988222777888322277788"
        self.assertEqual(decisions[0]["reference_price"], str(exact_price))
        self.assertEqual(decisions[1]["reference_price"], stop_price)
        self.assertEqual(orders[0]["average_fill_price"], entry_price)
        self.assertEqual(orders[1]["average_fill_price"], exit_price)
        self.assertEqual(fills[0]["price"], entry_price)
        self.assertEqual(fills[1]["price"], exit_price)
        self.assertEqual(positions[0]["entry_price"], entry_price)
        self.assertEqual(positions[1]["stop_price"], stop_price)
        self.assertEqual(trade["entry_price"], entry_price)
        self.assertEqual(heartbeat["status"], "ok")
        self.assertEqual(heartbeat["closed_trade_count"], 1)
        self.assertEqual(heartbeat["decision_count"], 1)
        self.assertEqual(heartbeat["order_count"], 1)


def _single_json_line(path: Path) -> dict[str, object]:
    lines = _json_lines(path)
    if len(lines) != 1:
        raise AssertionError(f"expected one JSON object in {path.name}, got {len(lines)}")
    return lines[0]


def _json_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _snapshot(timestamp: str, close: Decimal, low: Decimal) -> MarketSnapshot:
    close_timestamp = datetime.fromisoformat(timestamp)
    return MarketSnapshot(
        asset="BTC/USDT",
        timeframe="1h",
        availability="available",
        decision_action="evaluate",
        reason="closed_candle_available",
        candle=ClosedCandle(
            open_timestamp=close_timestamp - timedelta(hours=1),
            close_timestamp=close_timestamp,
            open=Decimal("100"),
            high=Decimal("101"),
            low=low,
            close=close,
            volume=Decimal("1"),
        ),
        price={"closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),)},
    )
