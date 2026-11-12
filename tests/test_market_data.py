from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from hermes_trading.adapters import price as price_adapter
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


def price_payload(
    candles: list[dict[str, object]],
    *,
    asset: str = "BTC/USDT",
    timeframe: str = "1h",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "asset": asset,
        "timeframe": timeframe,
        "last": "100.25",
        "bid": "100.00",
        "ask": "100.50",
        "candles": candles,
        "closes": [float(item["close"]) for item in candles],
    }


class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_price_client_retries_one_transient_asset_failure(self) -> None:
        client = price_adapter.PriceClient(exchange_factory=lambda config: FakeExchange())
        payload = {"asset": "BTC/USDT"}
        client.fetch = AsyncMock(side_effect=[RuntimeError("temporary"), payload])

        result = await client.fetch_many(["BTC/USDT"], concurrency=1)
        await client.close()

        self.assertEqual(result["BTC/USDT"], payload)
        self.assertEqual(client.fetch.await_count, 2)
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "trading.db"
        self.repo = StateRepository.open(self.db_path)
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

        async def fetch_for_asset(asset: str, timeframe: str) -> dict[str, object]:
            return price_payload(
                [candle(LAST_CLOSED_OPEN), current],
                asset=asset,
                timeframe=timeframe,
            )

        price_fetch = AsyncMock(side_effect=fetch_for_asset)
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

        async def price_fetch(asset: str, timeframe: str) -> dict[str, object]:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            if asset == "ETH/USDT":
                raise RuntimeError("price unavailable")
            return price_payload(
                [candle(LAST_CLOSED_OPEN), candle(CURRENT_OPEN)],
                asset=asset,
                timeframe=timeframe,
            )

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

        async def price_fetch(asset: str, timeframe: str) -> dict[str, object]:
            return price_payload(
                [candle(LAST_CLOSED_OPEN), candle(CURRENT_OPEN)],
                asset=asset,
                timeframe=timeframe,
            )

        coordinator = MarketDataCoordinator(
            repository=self.repo,
            price_fetch=AsyncMock(side_effect=price_fetch),
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

    async def test_requested_timeframe_is_fetched_and_payload_timeframe_must_match(self) -> None:
        requested: list[tuple[str, str]] = []

        async def price_fetch(asset: str, timeframe: str) -> dict[str, object]:
            requested.append((asset, timeframe))
            return price_payload(
                [candle(datetime(2026, 7, 30, 12, tzinfo=UTC))],
                asset=asset,
                timeframe=timeframe,
            )

        coordinator = self.coordinator(price_fetch, timeframe="15m")
        snapshot = (await coordinator.collect(["BTC/USDT"]))["BTC/USDT"]
        self.assertEqual(requested, [("BTC/USDT", "15m")])
        self.assertEqual(snapshot.availability, "available")

        wrong_timeframe = self.coordinator(
            AsyncMock(
                return_value=price_payload(
                    [candle(datetime(2026, 7, 30, 12, tzinfo=UTC))],
                    timeframe="1h",
                )
            ),
            timeframe="15m",
        )
        mismatch = (await wrong_timeframe.collect(["BTC/USDT"]))["BTC/USDT"]
        self.assertEqual(mismatch.availability, "invalid")
        self.assertEqual(mismatch.decision_action, "no_trade")

    async def test_invalid_financial_values_and_timestamp_overflow_are_isolated(self) -> None:
        invalid_payloads: dict[str, dict[str, object]] = {}
        for asset in (
            "NAN/USDT",
            "INFINITY/USDT",
            "MISSING/USDT",
            "OHLC/USDT",
            "VOLUME/USDT",
            "TIME/USDT",
        ):
            invalid_payloads[asset] = price_payload(
                [candle(LAST_CLOSED_OPEN)],
                asset=asset,
            )
        invalid_payloads["NAN/USDT"]["last"] = "sNaN"
        invalid_payloads["INFINITY/USDT"]["candles"][0]["close"] = "Infinity"
        invalid_payloads["MISSING/USDT"].pop("ask")
        invalid_payloads["OHLC/USDT"]["candles"][0]["high"] = "97"
        invalid_payloads["VOLUME/USDT"]["candles"][0]["volume"] = "-1"
        invalid_payloads["TIME/USDT"]["candles"][0]["timestamp"] = Decimal("1e30")

        async def price_fetch(asset: str, timeframe: str) -> dict[str, object]:
            if asset == "BTC/USDT":
                return price_payload([candle(LAST_CLOSED_OPEN)], asset=asset)
            return invalid_payloads[asset]

        assets = ["BTC/USDT", *invalid_payloads]
        snapshots = await self.coordinator(price_fetch).collect(assets)

        self.assertEqual(snapshots["BTC/USDT"].availability, "available")
        for asset in invalid_payloads:
            with self.subTest(asset=asset):
                self.assertEqual(snapshots[asset].availability, "invalid")
                self.assertEqual(snapshots[asset].decision_action, "no_trade")
                self.assertEqual(
                    self.repo.market_data_failure_count(f"price:{asset}"),
                    1,
                )

    async def test_price_payload_schema_asset_and_timeframe_are_validated(self) -> None:
        cases = {
            "schema": {"schema_version": 2},
            "asset": {"asset": "ETH/USDT"},
            "timeframe": {"timeframe": "15m"},
        }
        for name, mutation in cases.items():
            payload = price_payload([candle(LAST_CLOSED_OPEN)])
            payload.update(mutation)
            snapshot = (
                await self.coordinator(AsyncMock(return_value=payload)).collect(
                    ["BTC/USDT"]
                )
            )["BTC/USDT"]
            with self.subTest(case=name):
                self.assertEqual(snapshot.availability, "invalid")
                self.assertEqual(snapshot.decision_action, "no_trade")
        self.assertEqual(
            self.repo.market_data_failure_count("price:BTC/USDT"),
            len(cases),
        )

    async def test_malformed_shared_schema_is_an_explicit_failure(self) -> None:
        for failure_count, malformed in enumerate(
            ({}, {"schema_version": 2}),
            start=1,
        ):
            coordinator = MarketDataCoordinator(
                repository=self.repo,
                price_fetch=AsyncMock(
                    return_value=price_payload([candle(LAST_CLOSED_OPEN)])
                ),
                news_fetch=AsyncMock(return_value=malformed),
                macro_fetch=AsyncMock(return_value={"schema_version": 1}),
                onchain_fetch=AsyncMock(return_value={"schema_version": 1}),
                now=lambda: FIXED_NOW,
            )
            snapshot = (await coordinator.collect(["BTC/USDT"]))["BTC/USDT"]
            with self.subTest(payload=malformed):
                self.assertEqual(snapshot.availability, "shared_data_unavailable")
                self.assertEqual(snapshot.decision_action, "no_trade")
                self.assertIn("news", snapshot.shared_failures)
                self.assertEqual(
                    self.repo.market_data_failure_count("news"),
                    failure_count,
                )

    async def test_default_price_client_reuses_one_exchange_and_closes_after_asset_error(
        self,
    ) -> None:
        exchange = FakeExchange(failing_asset="ETH/USDT")
        creates = 0

        def exchange_factory(config: dict[str, object]) -> FakeExchange:
            nonlocal creates
            creates += 1
            return exchange

        coordinator = MarketDataCoordinator(
            repository=self.repo,
            price_client_factory=lambda: price_adapter.PriceClient(
                exchange_factory=exchange_factory
            ),
            news_fetch=AsyncMock(return_value={"schema_version": 1}),
            macro_fetch=AsyncMock(return_value={"schema_version": 1}),
            onchain_fetch=AsyncMock(return_value={"schema_version": 1}),
            now=lambda: FIXED_NOW,
            concurrency=2,
        )

        snapshots = await coordinator.collect(
            ["BTC/USDT", "ETH/USDT", "XRP/USDT", "SHIB/USDT"]
        )

        self.assertEqual(creates, 1)
        self.assertEqual(exchange.close_count, 1)
        self.assertEqual(snapshots["ETH/USDT"].availability, "invalid")
        self.assertEqual(snapshots["BTC/USDT"].availability, "available")
        self.assertTrue(all(timeframe == "1h" for _, timeframe in exchange.ohlcv_calls))

    async def test_cancellation_cancels_children_and_closes_price_client(self) -> None:
        exchange = FakeExchange(block=True)
        cancelled_shared = 0

        async def blocked_shared() -> dict[str, object]:
            nonlocal cancelled_shared
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled_shared += 1
                raise

        coordinator = MarketDataCoordinator(
            repository=self.repo,
            price_client_factory=lambda: price_adapter.PriceClient(
                exchange_factory=lambda config: exchange
            ),
            news_fetch=AsyncMock(side_effect=blocked_shared),
            macro_fetch=AsyncMock(side_effect=blocked_shared),
            onchain_fetch=AsyncMock(side_effect=blocked_shared),
            now=lambda: FIXED_NOW,
        )
        collection = asyncio.create_task(coordinator.collect(["BTC/USDT", "ETH/USDT"]))
        await asyncio.wait_for(exchange.started.wait(), timeout=1)

        collection.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await collection

        self.assertEqual(exchange.close_count, 1)
        self.assertGreater(exchange.cancelled_calls, 0)
        self.assertEqual(cancelled_shared, 3)

    async def test_price_adapter_emits_decimal_values_except_legacy_closes(self) -> None:
        exchange = FakeExchange()
        async with price_adapter.PriceClient(
            exchange_factory=lambda config: exchange
        ) as client:
            payload = await client.fetch("BTC/USDT", "15m")

        self.assertEqual(payload["timeframe"], "15m")
        for field in ("last", "bid", "ask"):
            self.assertIsInstance(payload[field], Decimal)
            self.assertTrue(payload[field].is_finite())
        for field in ("open", "high", "low", "close", "volume"):
            self.assertIsInstance(payload["candles"][0][field], Decimal)
            self.assertTrue(payload["candles"][0][field].is_finite())
        self.assertTrue(all(isinstance(value, float) for value in payload["closes"]))

    async def test_legacy_single_asset_fetch_uses_default_timeframe_and_closes(self) -> None:
        exchange = FakeExchange()
        with (
            patch.dict("os.environ", {"EXCHANGE_ID": "kraken"}),
            patch.object(price_adapter.ccxt, "kraken", return_value=exchange),
        ):
            payload = await price_adapter.fetch("BTC/USDT")

        self.assertEqual(payload["timeframe"], "1h")
        self.assertEqual(exchange.close_count, 1)

    async def test_price_client_retries_after_exchange_close_failure(self) -> None:
        exchange = CloseLifecycleExchange(failures=1)
        client = price_adapter.PriceClient(
            exchange_factory=lambda config: exchange
        )

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            await client.close()
        await client.close()
        await client.close()

        self.assertEqual(exchange.close_count, 2)

    async def test_concurrent_price_client_close_callers_share_one_close(self) -> None:
        exchange = CloseLifecycleExchange(block=True)
        client = price_adapter.PriceClient(
            exchange_factory=lambda config: exchange
        )

        first = asyncio.create_task(client.close())
        second = asyncio.create_task(client.close())
        await asyncio.wait_for(exchange.close_started.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertEqual(exchange.close_count, 1)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        exchange.allow_close.set()
        await asyncio.gather(first, second)
        await client.close()

        self.assertEqual(exchange.close_count, 1)

    async def test_cancelled_close_continues_and_later_caller_awaits_it(self) -> None:
        exchange = CloseLifecycleExchange(block=True)
        client = price_adapter.PriceClient(
            exchange_factory=lambda config: exchange
        )

        interrupted = asyncio.create_task(client.close())
        await asyncio.wait_for(exchange.close_started.wait(), timeout=1)
        interrupted.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await interrupted
        self.assertEqual(exchange.cancelled_close_count, 0)

        waiter = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        self.assertEqual(exchange.close_count, 1)
        self.assertFalse(waiter.done())
        exchange.allow_close.set()
        await waiter
        await client.close()

        self.assertEqual(exchange.close_count, 1)

    async def test_incident_increment_is_atomic_across_connections(self) -> None:
        barrier = threading.Barrier(4)

        def record_failure() -> int:
            repository = StateRepository.open(self.db_path)
            try:
                barrier.wait(timeout=2)
                return repository.record_market_data_failure(
                    "news", "concurrent failure"
                )
            finally:
                repository.close()

        with ThreadPoolExecutor(max_workers=4) as executor:
            revisions = list(executor.map(lambda _: record_failure(), range(4)))

        self.assertEqual(sorted(revisions), [1, 2, 3, 4])
        self.assertEqual(revisions.count(3), 1)
        self.assertEqual(self.repo.market_data_failure_count("news"), 4)


class FakeExchange:
    def __init__(
        self,
        *,
        failing_asset: str | None = None,
        block: bool = False,
    ) -> None:
        self.failing_asset = failing_asset
        self.block = block
        self.close_count = 0
        self.cancelled_calls = 0
        self.started = asyncio.Event()
        self.ohlcv_calls: list[tuple[str, str]] = []

    async def fetch_ticker(self, asset: str) -> dict[str, object]:
        await self._maybe_wait()
        if asset == self.failing_asset:
            raise RuntimeError("ticker unavailable")
        return {
            "last": "100.25",
            "bid": "100.00",
            "ask": "100.50",
            "timestamp": int(FIXED_NOW.timestamp() * 1000),
        }

    async def fetch_ohlcv(
        self, asset: str, *, timeframe: str, limit: int
    ) -> list[list[object]]:
        self.ohlcv_calls.append((asset, timeframe))
        await self._maybe_wait()
        return [
            [
                int(LAST_CLOSED_OPEN.timestamp() * 1000),
                "99",
                "101",
                "98",
                "100",
                "12.5",
            ]
        ]

    async def close(self) -> None:
        self.close_count += 1

    async def _maybe_wait(self) -> None:
        self.started.set()
        if not self.block:
            return
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            raise


class CloseLifecycleExchange:
    def __init__(
        self,
        *,
        failures: int = 0,
        block: bool = False,
    ) -> None:
        self.failures = failures
        self.block = block
        self.close_count = 0
        self.cancelled_close_count = 0
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        self.close_count += 1
        self.close_started.set()
        if self.block:
            try:
                await self.allow_close.wait()
            except asyncio.CancelledError:
                self.cancelled_close_count += 1
                raise
        if self.close_count <= self.failures:
            raise RuntimeError("close failed")


if __name__ == "__main__":
    unittest.main()
