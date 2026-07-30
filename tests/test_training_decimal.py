import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hermes_trading.loop import _write_json, run_loop
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

    async def test_entry_cycle_serializes_decimal_prices_losslessly(self) -> None:
        exact_price = Decimal("100.1234567890123456789012345678")
        price_payload = {
            "schema_version": 1,
            "asset": "BTC/USDT",
            "timeframe": "1h",
            "source": "test",
            "last": exact_price,
            "bid": Decimal("100.1234567890123456789012345677"),
            "ask": Decimal("100.1234567890123456789012345679"),
            "closes": [100.0] * 15 + [80.0],
            "candles": [],
            "timestamp": Decimal("1785412800000"),
        }

        async def stop_after_cycle(seconds: float) -> None:
            raise asyncio.CancelledError

        with tempfile.TemporaryDirectory() as temporary_directory:
            state = Path(temporary_directory)
            ensure_state_files(state)
            with (
                patch.dict(
                    os.environ,
                    {"HERMES_TRADING_MODE": "training"},
                    clear=False,
                ),
                patch(
                    "hermes_trading.loop.price.fetch",
                    AsyncMock(return_value=price_payload),
                ),
                patch(
                    "hermes_trading.loop.news.fetch",
                    AsyncMock(return_value={"schema_version": 1}),
                ),
                patch(
                    "hermes_trading.loop.macro.fetch",
                    AsyncMock(return_value={"schema_version": 1}),
                ),
                patch(
                    "hermes_trading.loop.onchain.fetch",
                    AsyncMock(return_value={"schema_version": 1}),
                ),
                patch(
                    "hermes_trading.loop.asyncio.sleep",
                    side_effect=stop_after_cycle,
                ),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await run_loop("BTC/USDT", state, interval_seconds=60)

            decision = _single_json_line(state / "decisions.jsonl")
            trade = _single_json_line(state / "trades.jsonl")
            order = _single_json_line(state / "orders.jsonl")
            heartbeat = json.loads((state / "heartbeat.json").read_text(encoding="utf-8"))

        expected_price = "100.1234567890123456789012345678"
        self.assertEqual(decision["entry_price"], expected_price)
        self.assertEqual(decision["signal"]["reference_price"], expected_price)
        self.assertEqual(trade["entry_price"], expected_price)
        self.assertEqual(order["reference_price"], expected_price)
        self.assertEqual(heartbeat["status"], "ok")
        self.assertEqual(heartbeat["closed_trade_count"], 1)


def _single_json_line(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise AssertionError(f"expected one JSON object in {path.name}, got {len(lines)}")
    return json.loads(lines[0])
