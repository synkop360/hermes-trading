from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from scripts.fetch_historical_data import (
    Candle,
    _parse_csv,
    cache_path,
    download,
    load_candles,
    resample,
    to_ccxt_like,
)

# Real samples from cryptodatadownload.com's Binance CSVs (fetched and
# verified live this session) -- daily/hourly use a capitalized header,
# minute-granularity files use a lowercase one; both carry the same banner
# line and the same column positions.
DAILY_SAMPLE = (
    "https://www.CryptoDataDownload.com\n"
    "Unix,Date,Symbol,Open,High,Low,Close,Volume BTC,Volume USDT,tradecount\n"
    "1787702400000,2026-08-26,BTCUSDT,78539.13,79251.6,77632.58,79023.75,14613.75887,1148236055.879,500000\n"
    "1787616000000,2026-08-25,BTCUSDT,77900.00,78600.00,77500.00,78539.13,12000.0,940000000.0,480000\n"
)
MINUTE_SAMPLE = (
    "https://www.CryptoDataDownload.com\n"
    "unix,date,symbol,open,high,low,close,Volume BTC,Volume USDT,tradecount\n"
    "1658911380000,2022-07-27 08:43:00,BTC/USDT,21374.96,21383.02,21371.20,21379.50,10.5,224000.0,50\n"
    "1658911320000,2022-07-27 08:42:00,BTC/USDT,21360.00,21378.00,21358.00,21374.96,8.2,175000.0,40\n"
)


class ParseCsvTests(unittest.TestCase):
    def test_parses_daily_style_capitalized_header_and_sorts_ascending(self) -> None:
        candles = _parse_csv(DAILY_SAMPLE)

        self.assertEqual(len(candles), 2)
        self.assertLess(candles[0].timestamp, candles[1].timestamp)
        self.assertEqual(candles[1].close, Decimal("79023.75"))
        self.assertEqual(candles[1].timestamp, datetime(2026, 8, 26, tzinfo=timezone.utc))

    def test_parses_minute_style_lowercase_header(self) -> None:
        candles = _parse_csv(MINUTE_SAMPLE)

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].timestamp, datetime(2022, 7, 27, 8, 42, tzinfo=timezone.utc))
        self.assertEqual(candles[0].open, Decimal("21360.00"))
        self.assertEqual(candles[0].volume, Decimal("8.2"))

    def test_empty_content_yields_no_candles(self) -> None:
        self.assertEqual(_parse_csv(""), [])
        self.assertEqual(_parse_csv("https://www.CryptoDataDownload.com\n"), [])


class DownloadTests(unittest.TestCase):
    def test_download_writes_cache_and_skips_second_call(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str) -> str:
            calls.append(url)
            return DAILY_SAMPLE

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            first = download("BTC/USDT", "day", cache_dir=cache_dir, fetch=fake_fetch)
            second = download("BTC/USDT", "day", cache_dir=cache_dir, fetch=fake_fetch)

            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)  # second call served from cache, no re-fetch
            self.assertIn("Binance_BTCUSDT_d.csv", calls[0])
            self.assertEqual(first.read_text(encoding="utf-8"), DAILY_SAMPLE)

    def test_download_force_refetches_even_when_cached(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str) -> str:
            calls.append(url)
            return DAILY_SAMPLE

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            download("BTC/USDT", "day", cache_dir=cache_dir, fetch=fake_fetch)
            download("BTC/USDT", "day", cache_dir=cache_dir, force=True, fetch=fake_fetch)

            self.assertEqual(len(calls), 2)

    def test_download_rejects_unsupported_granularity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                download("BTC/USDT", "weekly", cache_dir=Path(tmp), fetch=lambda url: "")

    def test_cache_path_strips_the_slash_from_the_pair(self) -> None:
        path = cache_path("BTC/USDT", "minute", cache_dir=Path("/tmp/x"))

        self.assertEqual(path.name, "BTCUSDT_minute.csv")


class LoadCandlesTests(unittest.TestCase):
    def test_raises_a_helpful_error_when_nothing_is_cached_yet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as caught:
                load_candles("BTC/USDT", "minute", cache_dir=Path(tmp))

        self.assertIn("fetch_historical_data.py", str(caught.exception))

    def test_since_and_until_scope_the_returned_candles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cache_path("BTC/USDT", "day", cache_dir=cache_dir).write_text(DAILY_SAMPLE, encoding="utf-8")

            only_latest = load_candles(
                "BTC/USDT", "day", cache_dir=cache_dir, since=datetime(2026, 8, 26, tzinfo=timezone.utc),
            )

        self.assertEqual(len(only_latest), 1)
        self.assertEqual(only_latest[0].close, Decimal("79023.75"))


class ResampleTests(unittest.TestCase):
    def test_aggregates_open_high_low_close_volume_across_the_bucket(self) -> None:
        candles = [
            Candle(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), Decimal(100), Decimal(105), Decimal(99), Decimal(101), Decimal(1)),
            Candle(datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), Decimal(101), Decimal(110), Decimal(100), Decimal(108), Decimal(2)),
        ]

        bucketed = resample(candles, minutes=2)

        self.assertEqual(len(bucketed), 1)
        bucket = bucketed[0]
        self.assertEqual(bucket.timestamp, datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(bucket.open, Decimal(100))    # first candle's open
        self.assertEqual(bucket.close, Decimal(108))   # last candle's close
        self.assertEqual(bucket.high, Decimal(110))    # max across the bucket
        self.assertEqual(bucket.low, Decimal(99))      # min across the bucket
        self.assertEqual(bucket.volume, Decimal(3))    # summed

    def test_candles_spanning_two_buckets_are_kept_separate(self) -> None:
        candles = [
            Candle(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc), Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(1)),
            Candle(datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc), Decimal(2), Decimal(2), Decimal(2), Decimal(2), Decimal(1)),
        ]

        bucketed = resample(candles, minutes=2)

        self.assertEqual(len(bucketed), 2)

    def test_rejects_non_positive_bucket_size(self) -> None:
        with self.assertRaises(ValueError):
            resample([], minutes=0)


class ToCcxtLikeTests(unittest.TestCase):
    def test_matches_ccxts_ohlcv_row_shape_with_exact_decimal_strings(self) -> None:
        candles = [Candle(datetime(2026, 1, 1, tzinfo=timezone.utc), Decimal("1.1"), Decimal("2.2"), Decimal("0.9"), Decimal("1.5"), Decimal("10"))]

        rows = to_ccxt_like(candles)

        self.assertEqual(len(rows), 1)
        ts, o, h, l, c, v = rows[0]
        self.assertEqual(ts, 1767225600000)
        self.assertEqual(Decimal(o), Decimal("1.1"))
        self.assertEqual(Decimal(v), Decimal("10"))


if __name__ == "__main__":
    unittest.main()
