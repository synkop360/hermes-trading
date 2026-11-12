from __future__ import annotations

import argparse
import csv
import io
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

import httpx

# Downloads and caches historical OHLCV data from cryptodatadownload.com
# (Binance) for backtesting -- years of history vs. Kraken's ~7.5-day cap via
# ccxt's fetch_ohlcv, the limitation that constrained every real-data backtest
# this session (see docs/COMMANDS.md's "trailing-exit" and risk-management
# entries). Confirmed live and free for all 5 of this project's symbols, at
# daily/hourly/minute granularity, no login or API key required.
#
# NOT a substitute for Kraken data: this is Binance OHLCV, a different venue
# than what the strategy actually trades. Broad price action is highly
# correlated across major exchanges for these liquid pairs, but microstructure
# differs -- treat this as "backtest the strategy logic over a long history",
# not "predict exact Kraken fills". Offered under a Creative Commons
# BY-NC-SA license (non-commercial) -- fine for internal research, revisit if
# this project's data usage ever becomes commercial.

CACHE_DIR = Path(__file__).resolve().parents[1] / "market_data_cache"
BASE_URL = "https://www.cryptodatadownload.com/cdd/Binance_{pair}_{suffix}.csv"
GRANULARITY_SUFFIX = {"day": "d", "hour": "1h", "minute": "minute"}
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "XRP/USDT", "LINK/USDT", "SOL/USDT"]

Fetcher = Callable[[str], str]


def _pair(symbol: str) -> str:
    return symbol.replace("/", "")


def cache_path(symbol: str, granularity: str, *, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / f"{_pair(symbol)}_{granularity}.csv"


def _default_fetcher(url: str) -> str:
    response = httpx.get(url, timeout=120.0)
    response.raise_for_status()
    return response.text


def download(
    symbol: str,
    granularity: str,
    *,
    cache_dir: Path = CACHE_DIR,
    force: bool = False,
    fetch: Fetcher = _default_fetcher,
) -> Path:
    """Downloads one symbol/granularity CSV, caching it locally. Skips the
    network call when a cached copy already exists unless force=True -- these
    files can be tens to hundreds of MB (minute-granularity BTC is ~180MB), so
    re-downloading on every run would be slow and wasteful for data that only
    grows by one day at a time."""
    if granularity not in GRANULARITY_SUFFIX:
        raise ValueError(f"unsupported granularity {granularity!r}; expected one of {sorted(GRANULARITY_SUFFIX)}")
    target = cache_path(symbol, granularity, cache_dir=cache_dir)
    if target.exists() and not force:
        return target
    suffix = GRANULARITY_SUFFIX[granularity]
    url = BASE_URL.format(pair=_pair(symbol), suffix=suffix)
    text = fetch(url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


@dataclass(frozen=True)
class Candle:
    timestamp: datetime  # UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def _parse_csv(text: str) -> list[Candle]:
    """Parses cryptodatadownload's Binance CSV format. Column layout is
    consistent by position across daily/hourly/minute files (unix ms, date,
    symbol, open, high, low, close, volume-in-base-asset, volume-in-quote,
    tradecount) even though the header casing differs between granularities
    ("Unix,Date,Symbol,..." for day/hour vs "unix,date,symbol,..." for
    minute) -- so this reads by position, not by header name, and skips the
    "https://www.CryptoDataDownload.com" banner line some exports carry
    before the real header."""
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith("http"):
        lines = lines[1:]
    if not lines:
        return []
    reader = csv.reader(io.StringIO("\n".join(lines[1:])))  # lines[0] is the header row
    candles = []
    for row in reader:
        if len(row) < 7 or not row[0].strip():
            continue
        timestamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
        candles.append(Candle(
            timestamp=timestamp,
            open=Decimal(row[3]), high=Decimal(row[4]), low=Decimal(row[5]), close=Decimal(row[6]),
            volume=Decimal(row[7]) if len(row) > 7 and row[7].strip() else Decimal(0),
        ))
    candles.sort(key=lambda c: c.timestamp)  # files are newest-first; callers need ascending
    return candles


def load_candles(
    symbol: str,
    granularity: str,
    *,
    cache_dir: Path = CACHE_DIR,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Candle]:
    path = cache_path(symbol, granularity, cache_dir=cache_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached data for {symbol} at {granularity!r} granularity -- run "
            f"`uv run python scripts/fetch_historical_data.py --symbols {symbol} --granularity {granularity}` first"
        )
    candles = _parse_csv(path.read_text(encoding="utf-8"))
    if since is not None:
        candles = [c for c in candles if c.timestamp >= since]
    if until is not None:
        candles = [c for c in candles if c.timestamp <= until]
    return candles


def resample(candles: list[Candle], minutes: int) -> list[Candle]:
    """Aggregates finer candles (typically 1-minute) into coarser bins, e.g.
    minutes=15 to match this project's actual trading timeframe. Only valid
    when going from finer to coarser -- there is no way to manufacture detail
    that was never downloaded."""
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    bucket_size = timedelta(minutes=minutes)
    buckets: OrderedDict[datetime, list[Candle]] = OrderedDict()
    for candle in candles:
        epoch = candle.timestamp.timestamp()
        bucket_start = candle.timestamp - timedelta(seconds=epoch % bucket_size.total_seconds())
        buckets.setdefault(bucket_start, []).append(candle)
    resampled = []
    for bucket_start, members in buckets.items():
        members.sort(key=lambda c: c.timestamp)
        resampled.append(Candle(
            timestamp=bucket_start,
            open=members[0].open,
            high=max(c.high for c in members),
            low=min(c.low for c in members),
            close=members[-1].close,
            volume=sum((c.volume for c in members), Decimal(0)),
        ))
    return resampled


def to_ccxt_like(candles: list[Candle]) -> list[list]:
    """Converts to ccxt's fetch_ohlcv row shape ([ts_ms, open, high, low,
    close, volume]) so scripts written against ccxt's OHLCV output (e.g. the
    real-engine backtest harnesses used earlier this project) can swap in
    this data with minimal changes. Unlike ccxt, OHLCV values here are exact
    Decimal-derived strings, not binary floats -- Decimal(row[i]) round-trips
    cleanly, no precision loss from an intermediate float."""
    return [
        [int(c.timestamp.timestamp() * 1000), str(c.open), str(c.high), str(c.low), str(c.close), str(c.volume)]
        for c in candles
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Downloads and caches free historical OHLCV data from cryptodatadownload.com "
            "(Binance) for backtesting -- years of history per symbol vs. Kraken's ~7.5-day "
            "cap via ccxt. Not Kraken data: a different venue, use for strategy-logic "
            "backtests, not exact-fill validation."
        )
    )
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated list, e.g. BTC/USDT,ETH/USDT")
    parser.add_argument("--granularity", default="minute", choices=sorted(GRANULARITY_SUFFIX))
    parser.add_argument("--force", action="store_true", help="Re-download even if a cached copy exists")
    parser.add_argument(
        "--resample-minutes", type=int, default=None,
        help="After downloading, also write a resampled cache at this bucket size (e.g. 15 for this project's actual timeframe)",
    )
    args = parser.parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    for symbol in symbols:
        path = download(symbol, args.granularity, force=args.force)
        size_mb = path.stat().st_size / 1_000_000
        print(f"{symbol:10s} {args.granularity:7s} -> {path.name} ({size_mb:.1f} MB)")
        if args.resample_minutes:
            candles = load_candles(symbol, args.granularity)
            resampled = resample(candles, args.resample_minutes)
            out_path = cache_path(symbol, f"{args.resample_minutes}min")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["unix", "date", "symbol", "open", "high", "low", "close", "volume"])
                for c in resampled:
                    writer.writerow([
                        int(c.timestamp.timestamp() * 1000), c.timestamp.isoformat(), _pair(symbol),
                        c.open, c.high, c.low, c.close, c.volume,
                    ])
            print(f"{'':10s} {args.resample_minutes}min   -> {out_path.name} ({len(resampled)} candles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
