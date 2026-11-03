"""Bounded public collection shared by the eight pilot accounts (LAT-31).

Every network client here is a test double: no request reaches Kraken. A
sample built from an injected collector is never eligible as public proof.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pilot_market
from hermes_trading.market_data import ClosedCandle, MarketSnapshot

AT = datetime(2026, 11, 3, 18, 30, tzinfo=timezone.utc)
NAMES = [f"{arm}_{wallet}" for arm in ("adaptive", "control") for wallet in (50, 100, 150, 200)]
TMP = Path(__file__).resolve().parents[1] / ".test-tmp-lat31"


def snapshot(bid="2500.1", available=True):
    if not available:
        return MarketSnapshot("ETH/EUR", "1h", "unavailable", "no_trade", "public_price_unavailable")
    candle = ClosedCandle(AT - timedelta(hours=2), AT - timedelta(hours=1), Decimal("2490"), Decimal("2510"),
                          Decimal("2480"), Decimal("2500"), Decimal("12.5"))
    price = {"source": "kraken", "received_at": AT.isoformat(), "bid": Decimal(bid),
             "ask": Decimal(bid) + 1, "last": Decimal(bid)}
    venue = {"source_url": "https://api.kraken.com/0/public/AssetPairs?pair=ETHEUR&country_code=FR",
             "received_at": AT.isoformat()}
    return MarketSnapshot("ETH/EUR", "1h", "available", "evaluate", "public_price_only", candle,
                          price=price, venue_rules=venue)


class Clock:
    def __init__(self):
        self.now = AT
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class Injected:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0
        self.closed = 0

    async def collect(self, assets):
        self.calls += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self):
        self.closed += 1


class PilotMarketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.root = TMP / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def pilot(self, collector, **options):
        options.setdefault("retry_delay_seconds", 1)
        return pilot_market.PilotMarketCollector(collector=collector, clock=self.clock, sleep=self.clock.sleep,
                                                 environ={}, **options)

    async def test_one_sample_is_shared_by_the_eight_accounts_with_provenance(self):
        self.assertEqual(list(pilot_market.ACCOUNTS), NAMES)
        sample = await self.pilot(Injected({"ETH/EUR": snapshot()})).collect_once()
        self.assertEqual(sample["accounts"], NAMES)
        self.assertEqual((sample["mode"], sample["authenticated"], sample["status"]), ("paper", False, "available"))
        # A double is never a public proof, even when complete.
        self.assertEqual(sample["source"], pilot_market.SOURCE_INJECTED)
        self.assertFalse(sample["public_proof_eligible"])
        [asset] = sample["assets"]
        self.assertEqual((asset["asset"], asset["pair"]), ("ETH/EUR", "ETHEUR"))
        self.assertEqual(asset["endpoints"]["ticker"], "https://api.kraken.com/0/public/Ticker?pair=ETHEUR")
        self.assertEqual(asset["endpoints"]["ohlc"], "https://api.kraken.com/0/public/OHLC?pair=ETHEUR&interval=60")
        self.assertEqual(asset["received_at"], AT.isoformat())
        self.assertEqual(asset["candle"]["close_timestamp"], (AT - timedelta(hours=1)).isoformat())
        self.assertEqual((asset["bid"], asset["ask"]), ("2500.1", "2501.1"))
        self.assertEqual(asset["venue"]["status"], "ok")
        self.assertEqual((sample["requested_at"], sample["completed_at"]), (AT.isoformat(), AT.isoformat()))
        # Fingerprints are canonical: recomputable and sensitive to the quote.
        body = {key: value for key, value in sample.items() if key != "fingerprint"}
        self.assertEqual(sample["fingerprint"], pilot_market.fingerprint(body))
        self.assertEqual(asset["fingerprint"], pilot_market.fingerprint(
            {key: value for key, value in asset.items() if key != "fingerprint"}))
        again = await self.pilot(Injected({"ETH/EUR": snapshot()})).collect_once()
        other = await self.pilot(Injected({"ETH/EUR": snapshot(bid="2500.2")})).collect_once()
        self.assertEqual(again["fingerprint"], sample["fingerprint"])
        self.assertNotEqual(other["assets"][0]["fingerprint"], asset["fingerprint"])
        json.dumps(sample, allow_nan=False)

    async def test_retry_is_finite_and_records_every_attempt(self):
        collector = Injected(OSError("network down"))
        sample = await self.pilot(collector, max_attempts=3).collect_once()
        self.assertEqual(collector.calls, 3)
        self.assertEqual([row["outcome"] for row in sample["attempts"]], ["error"] * 3)
        self.assertIn("OSError: network down", sample["attempts"][0]["error"])
        self.assertEqual(self.clock.sleeps, [1, 1])
        self.assertEqual((sample["status"], sample["assets"], sample["public_proof_eligible"]), ("unavailable", [], False))

    async def test_retry_stops_at_first_complete_sample_and_keeps_unavailable_as_partial(self):
        collector = Injected({"ETH/EUR": snapshot(available=False)}, {"ETH/EUR": snapshot()})
        sample = await self.pilot(collector, max_attempts=3).collect_once()
        self.assertEqual([row["outcome"] for row in sample["attempts"]], ["unavailable", "complete"])
        self.assertEqual(sample["status"], "available")
        exhausted = await self.pilot(Injected({"ETH/EUR": snapshot(available=False)}), max_attempts=2).collect_once()
        self.assertEqual(exhausted["status"], "partial")
        self.assertEqual(exhausted["assets"][0]["availability"], "unavailable")
        self.assertIsNone(exhausted["assets"][0]["candle"])
        self.assertEqual(exhausted["assets"][0]["venue"]["status"], "error")

    async def test_hanging_injected_collector_is_cut_by_the_attempt_deadline(self):
        class Hanging:
            async def collect(self, assets):
                await asyncio.sleep(60)
        sample = await self.pilot(Hanging(), max_attempts=1, attempt_timeout_seconds=0.01).collect_once()
        self.assertEqual(sample["attempts"][0]["outcome"], "timeout")
        self.assertEqual(sample["status"], "unavailable")

    async def test_default_path_reuses_public_collector_and_closes_its_clients(self):
        def client(**fetch):
            double = AsyncMock()
            double.__aenter__.return_value = double
            double.fetch_many.configure_mock(**fetch)
            return double
        payload = {"last": "2500", "bid": "2500", "ask": "2501", "received_at": AT.isoformat(),
                   "candles": [{"timestamp": (AT - timedelta(hours=15 - index)).isoformat(), "open": "2500",
                                "high": "2500", "low": "2500", "close": "2500", "volume": "1"} for index in range(15)]}
        price = client(return_value={"ETH/EUR": payload})
        venue = client(return_value={"ETH/EUR": {"error": "venue_metadata_unavailable"}})
        with patch("hermes_trading.paired_worker.PriceClient", return_value=price) as price_factory, \
                patch("hermes_trading.paired_worker.PublicVenueClient", return_value=venue), \
                patch("hermes_trading.paired_worker._now", return_value=AT):
            async with pilot_market.PilotMarketCollector(clock=self.clock, sleep=self.clock.sleep, environ={}) as pilot:
                sample = await pilot.collect_once()
        self.assertEqual(sample["source"], pilot_market.SOURCE_PUBLIC)
        self.assertEqual(price_factory.call_args.kwargs["exchange_id"], "kraken")
        price.__aexit__.assert_awaited_once()
        venue.__aexit__.assert_awaited_once()
        self.assertEqual(sample["assets"][0]["candle"]["close_timestamp"], AT.isoformat())

        async def hang(*args, **kwargs):
            await asyncio.sleep(60)
        stuck = client(side_effect=hang)
        with patch("hermes_trading.paired_worker.PriceClient", return_value=stuck), \
                patch("hermes_trading.paired_worker.PublicVenueClient", return_value=venue):
            sample = await pilot_market.PilotMarketCollector(
                clock=self.clock, sleep=self.clock.sleep, environ={}, max_attempts=2,
                attempt_timeout_seconds=0.05).collect_once()
        self.assertEqual(stuck.__aexit__.await_count, 2)
        self.assertEqual([row["outcome"] for row in sample["attempts"]], ["unavailable", "unavailable"])
        self.assertFalse(sample["public_proof_eligible"])

    async def test_credentials_and_unbounded_settings_are_refused(self):
        for name in pilot_market.CREDENTIAL_VARIABLES:
            with self.subTest(name=name), self.assertRaises(PermissionError):
                await pilot_market.PilotMarketCollector(collector=Injected({}), environ={name: "x"}).collect_once()
        for options in ({"max_attempts": 0}, {"max_attempts": 6}, {"max_attempts": True},
                        {"attempt_timeout_seconds": 0}, {"attempt_timeout_seconds": 21},
                        {"assets": ["ETH/USDT"]}, {"timeframe": "2h"}, {"assets": []}, {"retry_delay_seconds": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                pilot_market.PilotMarketCollector(collector=Injected({}), **options)
        for duration, interval in ((0, 1), (901, 60), (60, 0.5), (60, 61)):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                await pilot_market.run_collection(self.root, duration_seconds=duration, interval_seconds=interval,
                                                  collector=Injected({}), environ={})
        with self.assertRaises(ValueError):
            await pilot_market.run_collection(Path("/tmp/outside"), duration_seconds=60, collector=Injected({}), environ={})
        self.assertFalse(Path("/tmp/outside").exists())

    async def test_bounded_run_exports_samples_summary_and_closes_the_collector(self):
        collector = Injected({"ETH/EUR": snapshot()})
        summary = await pilot_market.run_collection(
            self.root, duration_seconds=900, interval_seconds=300, collector=collector,
            clock=self.clock, sleep=self.clock.sleep, environ={})
        self.assertEqual(collector.calls, 3)
        self.assertEqual(collector.closed, 1)
        self.assertEqual(self.clock.now - AT, timedelta(seconds=600))
        lines = (self.root / pilot_market.SAMPLES_FILE).read_bytes()
        samples = [json.loads(line) for line in lines.splitlines()]
        self.assertEqual(len(samples), 3)
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["sample_fingerprints"], [row["fingerprint"] for row in samples])
        self.assertEqual(summary["samples_sha256"], hashlib.sha256(lines).hexdigest())
        self.assertEqual((summary["source"], summary["public_proof_eligible_samples"]), (pilot_market.SOURCE_INJECTED, 0))
        self.assertEqual(summary["accounts"], NAMES)
        self.assertEqual(json.loads((self.root / pilot_market.SUMMARY_FILE).read_text()), summary)
        self.assertFalse(summary["cut_at_hard_deadline"])
        with self.assertRaises(ValueError):
            await pilot_market.run_collection(self.root, duration_seconds=60, collector=Injected({}),
                                              clock=self.clock, sleep=self.clock.sleep, environ={})

    async def test_hard_deadline_cuts_a_late_sample_without_writing_it(self):
        class Hanging:
            calls = 0
            async def collect(self, assets):
                Hanging.calls += 1
                await asyncio.sleep(60)
        summary = await pilot_market.run_collection(
            self.root, duration_seconds=1, interval_seconds=1, collector=Hanging(),
            attempt_timeout_seconds=5, max_attempts=1, environ={})
        self.assertEqual(Hanging.calls, 1)
        self.assertTrue(summary["cut_at_hard_deadline"])
        self.assertEqual((summary["samples"], summary["samples_sha256"]), (0, None))


if __name__ == "__main__":
    unittest.main()
