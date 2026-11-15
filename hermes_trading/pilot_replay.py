"""Strictly chronological replay of archived public Kraken candles (paper pilot).

Contract
--------
1. **Archive.** ``fetch_archive`` makes one unauthenticated public read
   (ccxt ``fetch_ohlcv``/``fetch_ticker`` and ``AssetPairs``) and writes
   ``replay_archive.json`` under a ``.test-tmp*`` root. Only candles already
   closed when the request *started* are kept (Kraken's last OHLC row is the
   candle in progress). The archive records the endpoints, the request and
   completion times, one SHA-256 per asset series and a fingerprint of the
   whole body. Prices are kept as decimal strings; nothing is interpolated.
2. **Validation.** ``load_archive`` refuses an archive whose fingerprint or
   series digest differs, whose timestamps are not integers aligned on the
   timeframe, strictly increasing and contiguous (no duplicate, no gap, no
   reordering), whose candles are not all closed at ``requested_at``, whose
   OHLC values are invalid or binary floats, whose assets are not aligned on
   the same timestamps, or whose venue record is not a valid public record.
3. **Clock.** The replay owns a simulated clock. Cycle ``k`` happens at the
   close of candle ``warmup + k`` plus ``lag_seconds``; the session's
   ``sleep(seconds)`` only advances that clock. ``collect`` derives every
   visible candle from the clock alone: a candle is visible iff its close is
   ``<=`` the current instant, and only the latest ``history_limit`` of them
   are passed on. A visible window that reaches past the instant raises
   :class:`ReplayAnticipationError`; a cycle without a new closed candle, or a
   clock past the archive, stops the replay.
4. **Execution quote.** The engine's quote is the close of the current candle
   (``bid = ask = last = close``), stamped at the cycle instant. The venue
   facts (lot/minimums, which digest only the public pair) are re-stamped at
   the cycle instant; their original receipt time and digest stay in the
   provenance. These two choices are declared in every replay record.

Only an archive fetched by this module from the public endpoints carries the
``kraken_public_unauthenticated`` source. An injected fetch (test double) is
marked ``injected_fetch`` and is never eligible as public proof.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from hermes_trading.market_data import MarketSnapshot, _closed_candle, timeframe_interval
from hermes_trading.pilot_market import (
    KRAKEN_INTERVAL_MINUTES,
    KRAKEN_PAIRS,
    PUBLIC_CLIENT,
    SOURCE_PUBLIC,
    _plain,
    fingerprint,
    public_endpoints,
    refuse_credentials,
)

UTC = timezone.utc
ARCHIVE_FILE = "replay_archive.json"
ARCHIVE_KIND = "pilot_public_replay_archive"
SOURCE_INJECTED_FETCH = "injected_fetch"
SOURCE_REPLAY = "kraken_public_replay"
SOURCE_REPLAY_NOT_PUBLIC = "replay_not_public"
EXECUTION_PRICE_BASIS = "current_candle_close"
VENUE_TIMESTAMP_BASIS = "restamped_at_cycle_instant"
MAX_FETCH_SECONDS = 20
MAX_HISTORY_LIMIT = 720
MAX_LAG_SECONDS = 60
_PRICE_FIELDS = ("open", "high", "low", "close", "volume")


class ReplayArchiveError(ValueError):
    """The archived public history is invalid or was altered."""


class ReplayAnticipationError(RuntimeError):
    """A candle closing after the replay instant would have been visible."""


class ReplayExhausted(RuntimeError):
    """No new closed candle is available for the replay instant."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ReplayArchiveError("archive time must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReplayArchiveError("archive time must include a timezone")
    return parsed.astimezone(UTC)


def _ms(value: datetime) -> int:
    return int((value - datetime(1970, 1, 1, tzinfo=UTC)) / timedelta(milliseconds=1))


def _from_ms(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)


def _decimal_text(value: Any, field: str) -> str:
    if isinstance(value, (bool, float)):
        raise ReplayArchiveError(f"candle {field} must be a decimal string, not {type(value).__name__}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ReplayArchiveError(f"candle {field} is not a decimal") from error
    if not number.is_finite():
        raise ReplayArchiveError(f"candle {field} is not finite")
    return str(value) if isinstance(value, str) else str(number)


def series_sha256(candles: list[dict]) -> str:
    return fingerprint(candles)


def _archive_candle(raw: Any) -> dict:
    """Canonical archived candle: integer millisecond open, decimal strings."""
    if not isinstance(raw, dict):
        raise ReplayArchiveError("candle is not a mapping")
    timestamp = raw.get("timestamp")
    if isinstance(timestamp, Decimal) and timestamp == timestamp.to_integral_value():
        timestamp = int(timestamp)
    if type(timestamp) is not int:
        raise ReplayArchiveError("candle timestamp must be integer milliseconds")
    row = {"timestamp": timestamp}
    for field in _PRICE_FIELDS:
        if field == "volume" and raw.get(field) is None:
            row[field] = None
            continue
        if field not in raw:
            raise ReplayArchiveError(f"candle {field} is missing")
        row[field] = _decimal_text(raw[field], field)
    return row


def validate_series(candles: Any, timeframe: str, *, closed_by: datetime) -> list[dict]:
    """Strictly increasing, contiguous, aligned, closed and valid OHLC candles."""
    if not isinstance(candles, list) or not candles:
        raise ReplayArchiveError("archived series is empty")
    interval = timeframe_interval(timeframe)
    step = int(interval / timedelta(milliseconds=1))
    rows = [_archive_candle(raw) for raw in candles]
    previous = None
    for index, row in enumerate(rows):
        if row["timestamp"] % step:
            raise ReplayArchiveError(f"candle {index} is not aligned on {timeframe}")
        if previous is not None:
            if row["timestamp"] <= previous:
                raise ReplayArchiveError(f"candle {index} is duplicated or out of order")
            if row["timestamp"] != previous + step:
                raise ReplayArchiveError(f"gap before candle {index}")
        previous = row["timestamp"]
        try:
            candle = _closed_candle(row, interval)
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise ReplayArchiveError(f"invalid candle {index}: {error}") from error
        if candle.low <= 0:
            raise ReplayArchiveError(f"candle {index} has a non-positive price")
        if candle.close_timestamp > closed_by:
            raise ReplayArchiveError(f"candle {index} was not closed when the archive was requested")
    return rows


def _check_venue(record: Any, asset: str) -> dict:
    from hermes_trading.venue_constraints import VenueConstraintsError, parse_venue_constraints

    if not isinstance(record, dict) or "error" in record:
        raise ReplayArchiveError(f"no public venue record for {asset}")
    try:
        parse_venue_constraints(record, asset=asset, at=record.get("received_at"))
    except (VenueConstraintsError, TypeError, ValueError, KeyError) as error:
        raise ReplayArchiveError(f"invalid venue record for {asset}: {error}") from error
    return record


def _archive_body(*, source, timeframe, assets, requested_at, completed_at, candles, venue_rules) -> dict:
    body = {
        "schema_version": 1,
        "kind": ARCHIVE_KIND,
        "mode": "paper",
        "authenticated": False,
        "source": source,
        "exchange": "kraken",
        "client": PUBLIC_CLIENT,
        "timeframe": timeframe,
        "assets": list(assets),
        "requested_at": _iso(requested_at),
        "completed_at": _iso(completed_at),
        "endpoints": {asset: public_endpoints(asset, timeframe) for asset in assets},
        "pairs": {asset: KRAKEN_PAIRS[asset] for asset in assets},
        "candles": candles,
        "series_sha256": {asset: series_sha256(candles[asset]) for asset in assets},
        "first_open": {asset: _iso(_from_ms(candles[asset][0]["timestamp"])) for asset in assets},
        "last_close": {asset: _iso(_from_ms(candles[asset][-1]["timestamp"]) + timeframe_interval(timeframe))
                       for asset in assets},
        "venue_rules": venue_rules,
    }
    body = _plain(body)
    body["fingerprint"] = fingerprint(body)
    return body


def validate_archive(archive: Any, *, assets=None, timeframe=None, now: datetime | None = None) -> dict:
    """Return the archive if every rule of the replay contract holds."""
    if not isinstance(archive, dict):
        raise ReplayArchiveError("archive is not a JSON object")
    if archive.get("schema_version") != 1 or archive.get("kind") != ARCHIVE_KIND:
        raise ReplayArchiveError("unsupported archive schema or kind")
    if archive.get("mode") != "paper" or archive.get("authenticated") is not False:
        raise ReplayArchiveError("archive must be a paper, unauthenticated read")
    body = {key: value for key, value in archive.items() if key != "fingerprint"}
    if archive.get("fingerprint") != fingerprint(body):
        raise ReplayArchiveError("archive fingerprint mismatch")
    if archive.get("source") not in (SOURCE_PUBLIC, SOURCE_INJECTED_FETCH):
        raise ReplayArchiveError("unknown archive source")
    frame = archive.get("timeframe")
    if frame not in KRAKEN_INTERVAL_MINUTES or (timeframe is not None and frame != timeframe):
        raise ReplayArchiveError("archive timeframe differs from the pilot")
    names = archive.get("assets")
    if (not isinstance(names, list) or not names or len(set(names)) != len(names)
            or any(name not in KRAKEN_PAIRS for name in names)
            or (assets is not None and list(assets) != names)):
        raise ReplayArchiveError("archive assets differ from the pilot")
    requested = _time(archive.get("requested_at"))
    completed = _time(archive.get("completed_at"))
    current = now or datetime.now(UTC)
    if not requested <= completed <= current:
        raise ReplayArchiveError("archive times are inverted or future-dated")
    candles = archive.get("candles")
    if not isinstance(candles, dict) or set(candles) != set(names):
        raise ReplayArchiveError("archive candles do not match its assets")
    for name in names:
        rows = validate_series(candles[name], frame, closed_by=requested)
        if rows != candles[name]:
            raise ReplayArchiveError(f"{name} series is not in canonical archived form")
        if archive.get("series_sha256", {}).get(name) != series_sha256(rows):
            raise ReplayArchiveError(f"{name} series digest mismatch")
        if archive.get("endpoints", {}).get(name) != public_endpoints(name, frame):
            raise ReplayArchiveError(f"{name} endpoints are not the public Kraken reads")
    first_open, last_close = archive.get("first_open"), archive.get("last_close")
    if (not isinstance(first_open, dict) or set(first_open) != set(names)
            or not isinstance(last_close, dict) or set(last_close) != set(names)):
        raise ReplayArchiveError("archive series dates do not match its assets")
    for name in names:
        rows = candles[name]
        if first_open[name] != _iso(_from_ms(rows[0]["timestamp"])):
            raise ReplayArchiveError(f"{name} first_open differs from its first candle")
        if last_close[name] != _iso(_from_ms(rows[-1]["timestamp"]) + timeframe_interval(frame)):
            raise ReplayArchiveError(f"{name} last_close differs from its last candle")
    stamps = {name: [row["timestamp"] for row in candles[name]] for name in names}
    if len({tuple(values) for values in stamps.values()}) != 1:
        raise ReplayArchiveError("asset series are not aligned on the same candles")
    venues = archive.get("venue_rules")
    if not isinstance(venues, dict) or set(venues) != set(names):
        raise ReplayArchiveError("archive venue records do not match its assets")
    for name in names:
        _check_venue(venues[name], name)
    return archive


def _validate_root(root: Path) -> Path:
    raw = Path(root).absolute()
    for candidate in (raw / ARCHIVE_FILE, raw, *raw.parents):
        if candidate.is_symlink():
            raise ValueError(f"replay path contains a symlink: {candidate}")
    root = raw.resolve()
    if not any(part.startswith(".test-tmp") for part in root.parts):
        raise ValueError("replay root must be inside a .test-tmp* directory")
    if root.exists() and not root.is_dir():
        raise ValueError(f"replay root is not a directory: {root}")
    return root


def load_archive(path, *, assets=None, timeframe=None, now=None) -> tuple[dict, str]:
    """Read and validate an archive; return it with the file SHA-256."""
    path = Path(path)
    root = _validate_root(path.parent)
    path = root / path.name
    if path.is_symlink() or not path.is_file():
        raise ReplayArchiveError(f"replay archive is not a regular file: {path}")
    raw = path.read_bytes()
    try:
        archive = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ReplayArchiveError("replay archive is not valid JSON") from error
    return validate_archive(archive, assets=assets, timeframe=timeframe, now=now), hashlib.sha256(raw).hexdigest()


async def _public_prices(assets, timeframe, timeout_seconds):
    from hermes_trading.adapters.price import PriceClient

    async with asyncio.timeout(timeout_seconds):
        async with PriceClient(exchange_id="kraken") as client:
            results = await client.fetch_many(list(assets), timeframe)
    for asset in assets:
        if isinstance(results.get(asset), BaseException):
            raise results[asset]
    return results


async def _public_venues(assets, timeout_seconds):
    from hermes_trading.venue_collection import PublicVenueClient

    async with PublicVenueClient(timeout_seconds=timeout_seconds) as client:
        return await client.fetch_many(assets)


async def fetch_archive(root, *, assets=("ETH/EUR",), timeframe="1h", price_fetch=None,
                        venue_fetch=None, clock=None, environ=None,
                        timeout_seconds=MAX_FETCH_SECONDS) -> dict:
    """One bounded public read, written once as ``replay_archive.json``.

    ``price_fetch``/``venue_fetch``/``clock`` are test-double hooks; using
    either fetch hook marks the archive ``injected_fetch``.
    """
    refuse_credentials(environ)
    assets = tuple(assets)
    if not assets or len(set(assets)) != len(assets) or any(asset not in KRAKEN_PAIRS for asset in assets):
        raise ValueError("replay assets must be distinct supported EUR pairs")
    if timeframe not in KRAKEN_INTERVAL_MINUTES:
        raise ValueError(f"unsupported replay timeframe: {timeframe!r}")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= MAX_FETCH_SECONDS:
        raise ValueError(f"fetch timeout must be within (0, {MAX_FETCH_SECONDS}] seconds")
    root = _validate_root(root)
    target = root / ARCHIVE_FILE
    if target.exists():
        raise ValueError(f"refusing to overwrite {target}")
    clock = clock or (lambda: datetime.now(UTC))
    injected = price_fetch is not None or venue_fetch is not None
    price_fetch = price_fetch or (lambda: _public_prices(assets, timeframe, timeout_seconds))
    venue_fetch = venue_fetch or (lambda: _public_venues(assets, timeout_seconds))
    requested = clock()
    prices, venues = await asyncio.gather(price_fetch(), venue_fetch())
    completed = clock()
    interval = timeframe_interval(timeframe)
    candles = {}
    for asset in assets:
        payload = prices.get(asset)
        if not isinstance(payload, dict) or not isinstance(payload.get("candles"), list):
            raise ReplayArchiveError(f"no public candles for {asset}")
        rows = [_archive_candle(row) for row in payload["candles"]]
        # Keep only candles closed before the request started: the last OHLC
        # row of Kraken is the candle still in progress.
        rows = [row for row in rows if _from_ms(row["timestamp"]) + interval <= requested]
        candles[asset] = validate_series(rows, timeframe, closed_by=requested)
    common = set.intersection(*({row["timestamp"] for row in candles[asset]} for asset in assets))
    candles = {asset: [row for row in candles[asset] if row["timestamp"] in common] for asset in assets}
    for asset in assets:
        candles[asset] = validate_series(candles[asset], timeframe, closed_by=requested)
        _check_venue(venues.get(asset), asset)
    archive = _archive_body(source=SOURCE_INJECTED_FETCH if injected else SOURCE_PUBLIC,
                            timeframe=timeframe, assets=assets, requested_at=requested,
                            completed_at=completed, candles=candles,
                            venue_rules={asset: venues[asset] for asset in assets})
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(archive, indent=2, sort_keys=True) + "\n")
    return archive


class ReplayClock:
    """Simulated clock of the replay; ``sleep`` advances it, never the wall."""

    def __init__(self, start: datetime):
        self._now = start.astimezone(UTC)
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if type(seconds) not in (int, float) or seconds < 0:
            raise ValueError("replay sleep must be a non-negative number of seconds")
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class HistoricalReplayCollector:
    """``collect(assets) -> {asset: MarketSnapshot}`` over a validated archive."""

    def __init__(self, archive_path, *, assets=None, timeframe=None, warmup_candles=50,
                 history_limit=200, lag_seconds=1, now=None):
        if type(warmup_candles) is not int or warmup_candles < 0:
            raise ValueError("warmup_candles must be a non-negative integer")
        if type(history_limit) is not int or not 1 <= history_limit <= MAX_HISTORY_LIMIT:
            raise ValueError(f"history_limit must be an integer from 1 through {MAX_HISTORY_LIMIT}")
        if type(lag_seconds) not in (int, float) or not 0 <= lag_seconds <= MAX_LAG_SECONDS:
            raise ValueError(f"lag_seconds must be within [0, {MAX_LAG_SECONDS}]")
        self.archive, self.archive_sha256 = load_archive(archive_path, assets=assets,
                                                         timeframe=timeframe, now=now)
        self.assets = tuple(self.archive["assets"])
        self.timeframe = self.archive["timeframe"]
        self.interval = timeframe_interval(self.timeframe)
        self.lag = timedelta(seconds=lag_seconds)
        self.history_limit = history_limit
        self.warmup_candles = warmup_candles
        self._series = {asset: self.archive["candles"][asset] for asset in self.assets}
        self._closes = [_from_ms(row["timestamp"]) + self.interval
                        for row in self._series[self.assets[0]]]
        if warmup_candles >= len(self._closes):
            raise ReplayArchiveError("archive is too short for the requested warmup")
        self.source = SOURCE_REPLAY if self.archive["source"] == SOURCE_PUBLIC else SOURCE_REPLAY_NOT_PUBLIC
        self.clock = ReplayClock(self._closes[warmup_candles] + self.lag)
        self.sleep = self.clock.sleep
        self.records: list[dict] = []
        self._last_index: int | None = None

    @property
    def start_at(self) -> datetime:
        return self._closes[self.warmup_candles] + self.lag

    @property
    def activation_at(self) -> datetime:
        """Account activation instant: the open of the first replayed candle."""
        return self._closes[self.warmup_candles] - self.interval

    @property
    def available_cycles(self) -> int:
        return len(self._closes) - self.warmup_candles

    def _visible_index(self, at: datetime) -> int:
        index = None
        for position, close in enumerate(self._closes):
            if close <= at:
                index = position
            else:
                break
        if index is None:
            raise ReplayExhausted("no archived candle is closed at the replay instant")
        if index == len(self._closes) - 1 and at - self._closes[index] > self.interval:
            raise ReplayExhausted("replay instant is past the archived history")
        return index

    def _snapshot(self, asset: str, index: int, at: datetime) -> tuple[MarketSnapshot, dict]:
        start = max(0, index + 1 - self.history_limit)
        window = self._series[asset][start:index + 1]
        candles = [_closed_candle(row, self.interval) for row in window]
        late = [c for c in candles if c.close_timestamp > at]
        if late:
            raise ReplayAnticipationError(f"{asset} candle closing at {_iso(late[0].close_timestamp)} after {_iso(at)}")
        current = candles[-1]
        close = current.close
        venue = dict(self.archive["venue_rules"][asset], received_at=_iso(at))
        price = {
            "bid": close, "ask": close, "last": close,
            "received_at": _iso(at),
            "source": SOURCE_REPLAY if self.source == SOURCE_REPLAY else SOURCE_REPLAY_NOT_PUBLIC,
            "closes": tuple(c.close for c in candles),
            "candles": tuple({"open": c.open, "high": c.high, "low": c.low, "close": c.close} for c in candles),
        }
        snapshot = MarketSnapshot(asset, self.timeframe, "available", "evaluate", "public_price_only",
                                  current, price=price, venue_rules=venue)
        record = {
            "asset": asset,
            "visible_candles": len(window),
            "visible_first_open": _iso(candles[0].open_timestamp),
            "visible_last_close": _iso(current.close_timestamp),
            "window_sha256": series_sha256(window),
            "close": str(close),
            "venue_received_at": self.archive["venue_rules"][asset]["received_at"],
            "venue_source_sha256": self.archive["venue_rules"][asset]["source_sha256"],
        }
        return snapshot, record

    async def collect(self, assets) -> dict[str, MarketSnapshot]:
        if tuple(assets) != self.assets:
            raise ValueError("requested assets differ from the replay archive")
        at = self.clock()
        index = self._visible_index(at)
        if self._last_index is not None and index <= self._last_index:
            raise ReplayExhausted("no new closed candle since the previous replay cycle")
        snapshots, rows = {}, []
        for asset in self.assets:
            snapshots[asset], row = self._snapshot(asset, index, at)
            rows.append(row)
        self._last_index = index
        record = {"cycle": len(self.records) + 1, "at": _iso(at), "candle_index": index, "assets": rows}
        record["fingerprint"] = fingerprint(record)
        self.records.append(record)
        return snapshots

    def provenance(self) -> dict:
        return _plain({
            "archive_file": ARCHIVE_FILE,
            "archive_sha256": self.archive_sha256,
            "archive_fingerprint": self.archive["fingerprint"],
            "archive_source": self.archive["source"],
            "archive_requested_at": self.archive["requested_at"],
            "series_sha256": self.archive["series_sha256"],
            "replay_source": self.source,
            "warmup_candles": self.warmup_candles,
            "history_limit": self.history_limit,
            "lag_seconds": self.lag.total_seconds(),
            "execution_price_basis": EXECUTION_PRICE_BASIS,
            "venue_timestamp_basis": VENUE_TIMESTAMP_BASIS,
            "start_at": _iso(self.start_at),
            "cycles": self.records,
        })


def main(argv=None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Fetch one public Kraken candle archive for the pilot replay.")
    parser.add_argument("--root", required=True, type=Path, help="output directory inside .test-tmp*")
    args = parser.parse_args(argv)
    try:
        archive = asyncio.run(fetch_archive(args.root))
    except (ValueError, PermissionError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    summary = {key: archive[key] for key in ("source", "timeframe", "assets", "requested_at", "completed_at",
                                             "first_open", "last_close", "series_sha256", "fingerprint")}
    summary["candles"] = {asset: len(rows) for asset, rows in archive["candles"].items()}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
