from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from hermes_trading.market_data import (
    MarketDataCoordinator,
    latest_closed_candle,
    timeframe_interval,
)
from hermes_trading.persistence import StateRepository


FIXED_NOW = datetime(2026, 7, 30, 12, 15, tzinfo=UTC)
LAST_CLOSED_OPEN = datetime(2026, 7, 30, 11, tzinfo=UTC)
CURRENT_OPEN = datetime(2026, 7, 30, 12, tzinfo=UTC)


def candle(opened_at: datetime, close: str = "100") -> dict[str, object]:
    return {
        "timestamp": int(opened_at.timestamp() * 1000),
        "open": "99",
        "high": "101",
        "low": "98",
        "close": close,
        "volume": "12.5",
    }


def price_payload(candles: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "asset": "BTC/USDT",
        "last": "100.25",
        "bid": "100.00",
        "ask": "100.50",
        "candles": candles,
        "closes": [float(item["close"]) for item in candles],
    }


class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repo = StateRepository.open(Path(self.temporary_directory.name) / "trading.db")
        self.repo.migrate()

    def tearDown(self) -> None:
        self.repo.close()
        self.temporary_directory.cleanup()

    def coordinator(self, price_fetch: AsyncMock, **kwargs: object) -> MarketDataCoordinator:
        return MarketDataCoordinator(
            repository=self.repo,
            price_fetch=price_fetch,
            news_fetch=AsyncMock(return_value={"schema_version": 1}),
            macro_fetch=AsyncMock(return_value={"schema_version": 1}),
            onchain_fetch=AsyncMock(return_value={"schema_version": 1}),
            now=lambda: FIXED_NOW,
            **kwargs,
        )

    def test_current_incomplete_candle_is_excluded(self) -> None:
        payload = price_payload(
            [
                candle(LAST_CLOSED_OPEN),
                candle(CURRENT_OPEN),
            ]
        )

        latest = latest_closed_candle(payload, "1h", now=FIXED_NOW)

        self.assertEqual(latest.close_timestamp, CURRENT_OPEN)
        self.assertEqual(latest.close, Decimal("100"))

    def test_timeframe_parser_accepts_ccxt_units_and_rejects_ambiguous_values(self) -> None:
        self.assertEqual(timeframe_interval("15m"), timedelta(minutes=15))
        self.assertEqual(timeframe_interval("2h"), timedelta(hours=2))
        self.assertEqual(timeframe_interval("1M"), timedelta(days=30))
        with self.assertRaises(ValueError):
            timeframe_interval("hourly")

    async def test_shared_adapters_run_once_for_four_assets(self) -> None:
        assets = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "SHIB/USDT"]
        current = candle(CURRENT_OPEN)
        price_fetch = AsyncMock(return_value=price_payload([candle(LAST_CLOSED_OPEN), current]))
        coordinator = self.coordinator(price_fetch, concurrency=2)

        snapshots = await coordinator.collect(assets)

        self.assertEqual(set(snapshots), set(assets))
        self.assertFalse(
            self.repo.is_candle_processed("BTC/USDT", "1h", CURRENT_OPEN.isoformat())
        )
        coordinator.news_fetch.assert_awaited_once()
        coordinator.macro_fetch.assert_awaited_once()
        coordinator.onchain_fetch.assert_awaited_once()

    async def test_stale_closed_candle_produces_no_trade(self) -> None:
        coordinator = self.coordinator(
            AsyncMock(return_value=price_payload([candle(FIXED_NOW - timedelta(hours=4))]))
        )

        snapshot = (await coordinator.collect(["BTC/USDT"]))["BTC/USDT"]

        self.assertEqual(snapshot.availability, "stale")
        self.assertEqual(snapshot.decision_action, "no_trade")

    async def test_processed_candle_is_not_marked_again_or_evaluated(self) -> None:
        close_timestamp = CURRENT_OPEN.isoformat()
        self.repo.mark_candle_processed("BTC/USDT", "1h", close_timestamp)
        coordinator = self.coordinator(
            AsyncMock(
                return_value=price_payload(
                    [candle(LAST_CLOSED_OPEN), candle(CURRENT_OPEN)]
                )
            )
        )

        snapshot = (await coordinator.collect(["BTC/USDT"]))["BTC/USDT"]

        self.assertEqual(snapshot.availability, "already_processed")
        self.assertEqual(snapshot.decision_action, "no_trade")
        self.assertTrue(self.repo.is_candle_processed("BTC/USDT", "1h", close_timestamp))

    async def test_price_requests_are_bounded_and_one_failure_does_not_cancel_others(self) -> None:
        active = 0
        maximum = 0

        async def price_fetch(asset: str) -> dict[str, object]:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            if asset == "ETH/USDT":
                raise RuntimeError("price unavailable")
            return price_payload([candle(LAST_CLOSED_OPEN), candle(CURRENT_OPEN)])

        snapshots = await self.coordinator(price_fetch, concurrency=2).collect(
            ["BTC/USDT", "ETH/USDT", "XRP/USDT"]
        )

        self.assertLessEqual(maximum, 2)
        self.assertEqual(snapshots["ETH/USDT"].availability, "invalid")
        self.assertEqual(snapshots["ETH/USDT"].decision_action, "no_trade")
        self.assertEqual(snapshots["BTC/USDT"].availability, "available")
        self.assertEqual(snapshots["XRP/USDT"].availability, "available")

    async def test_shared_failure_is_explicit_and_warning_occurs_once_at_threshold(self) -> None:
        warnings: list[str] = []
        coordinator = MarketDataCoordinator(
            repository=self.repo,
            price_fetch=AsyncMock(
                return_value=price_payload(
                    [candle(LAST_CLOSED_OPEN), candle(CURRENT_OPEN)]
                )
            ),
            news_fetch=AsyncMock(side_effect=RuntimeError("news unavailable")),
            macro_fetch=AsyncMock(return_value={"schema_version": 1}),
            onchain_fetch=AsyncMock(return_value={"schema_version": 1}),
            now=lambda: FIXED_NOW,
            failure_threshold=3,
            warning=warnings.append,
        )

        for _ in range(4):
            snapshots = await coordinator.collect(["BTC/USDT", "ETH/USDT"])

        self.assertEqual(snapshots["BTC/USDT"].availability, "shared_data_unavailable")
        self.assertIn("news", snapshots["BTC/USDT"].shared_failures)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(self.repo.market_data_failure_count("news"), 4)

        coordinator.news_fetch = AsyncMock(return_value={"schema_version": 1})
        await coordinator.collect(["BTC/USDT"])
        self.assertEqual(self.repo.market_data_failure_count("news"), 0)


if __name__ == "__main__":
    unittest.main()
