"""Bounded public market collection shared by the eight pilot paper accounts.

One unauthenticated Kraken collection feeds the whole adaptive/control x
50/100/150/200 EUR cohort: every account reads the same sample, identified by
one fingerprint. ``PublicQuoteCollector`` from ``paired_worker`` is reused
as-is (it opens and closes its public clients per call); this module only adds
a finite retry, a hard per-attempt timeout, a wall-clock ceiling of fifteen
minutes and an exported provenance record (endpoint, pair, timestamps,
fingerprint).

No order, model, Discord or Railway call is made. Credentials are refused:
if ``EXCHANGE_API_KEY`` or ``EXCHANGE_API_SECRET`` is set the collection does
not start, because the reused price client would otherwise forward them.
Runtime outputs are confined to a ``.test-tmp*`` directory.

Bounded collection (15 minutes maximum, one sample every 60 seconds)::

    uv run python -m hermes_trading.pilot_market --root .test-tmp-pilot/market \
        --duration-seconds 900 --interval-seconds 60

Exit code 0 only if at least one sample is a complete public observation,
1 otherwise, 2 when the settings or the environment are refused.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

ARMS = ("adaptive", "control")
WALLETS_EUR = (50, 100, 150, 200)
ACCOUNTS = tuple(f"{arm}_{wallet}" for arm in ARMS for wallet in WALLETS_EUR)
ASSETS = ("ETH/EUR",)
TIMEFRAME = "1h"

# Kraken public REST pair names, aligned with venue_collection.PublicVenueClient.
KRAKEN_PAIRS = {"BTC/EUR": "XBTEUR", "ETH/EUR": "ETHEUR", "XRP/EUR": "XRPEUR"}
KRAKEN_PUBLIC = "https://api.kraken.com/0/public"
KRAKEN_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
# Requests go through ccxt.kraken public methods (fetch_ticker, fetch_ohlcv)
# and PublicVenueClient (AssetPairs); the URLs below document those reads.
PUBLIC_CLIENT = "ccxt.kraken public fetch_ticker/fetch_ohlcv + httpx AssetPairs"
CREDENTIAL_VARIABLES = ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET")

MAX_COLLECTION_SECONDS = 15 * 60
MAX_ATTEMPTS = 5
MAX_ATTEMPT_SECONDS = 20
SAMPLES_FILE = "market_samples.jsonl"
SUMMARY_FILE = "market_collection.json"
SOURCE_PUBLIC = "kraken_public_unauthenticated"
SOURCE_INJECTED = "injected_collector"


def _plain(value):
    """Canonical JSON-safe form: Decimals and datetimes become strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported provenance value: {type(value).__name__}")


def _canonical(value) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def public_endpoints(asset: str, timeframe: str = TIMEFRAME) -> dict:
    """Public endpoints read for one asset (ccxt ticker/OHLC, raw AssetPairs)."""
    if asset not in KRAKEN_PAIRS or timeframe not in KRAKEN_INTERVAL_MINUTES:
        raise ValueError(f"unsupported pilot asset or timeframe: {asset!r} {timeframe!r}")
    pair = KRAKEN_PAIRS[asset]
    return {
        "ticker": f"{KRAKEN_PUBLIC}/Ticker?" + urlencode({"pair": pair}),
        "ohlc": f"{KRAKEN_PUBLIC}/OHLC?" + urlencode({"pair": pair, "interval": KRAKEN_INTERVAL_MINUTES[timeframe]}),
        "asset_pairs": f"{KRAKEN_PUBLIC}/AssetPairs",
    }


def refuse_credentials(environ=None) -> None:
    environ = os.environ if environ is None else environ
    present = [name for name in CREDENTIAL_VARIABLES if environ.get(name)]
    if present:
        raise PermissionError("public pilot collection refuses exchange credentials: " + ", ".join(present))


def _asset_record(asset: str, timeframe: str, snapshot) -> dict:
    price = snapshot.price if isinstance(snapshot.price, dict) else {}
    candle = snapshot.candle
    venue = snapshot.venue_rules if isinstance(snapshot.venue_rules, dict) else {}
    record = {
        "asset": asset,
        "pair": KRAKEN_PAIRS[asset],
        "timeframe": timeframe,
        "availability": snapshot.availability,
        "reason": snapshot.reason,
        "endpoints": public_endpoints(asset, timeframe),
        "client": PUBLIC_CLIENT,
        "price_source": price.get("source"),
        "received_at": price.get("received_at"),
        "bid": price.get("bid"),
        "ask": price.get("ask"),
        "last": price.get("last"),
        "candle": None if candle is None else {
            "open_timestamp": candle.open_timestamp,
            "close_timestamp": candle.close_timestamp,
            "open": candle.open, "high": candle.high, "low": candle.low,
            "close": candle.close, "volume": candle.volume,
        },
        "venue": {
            "status": "error" if "error" in venue or not venue else "ok",
            "source_url": venue.get("source_url"),
            "received_at": venue.get("received_at"),
        },
    }
    record = _plain(record)
    record["fingerprint"] = fingerprint(record)
    return record


class PilotMarketCollector:
    """Finite, timed, unauthenticated collection with exported provenance.

    ``collector`` is injectable for tests; it must expose
    ``async collect(assets) -> {asset: MarketSnapshot}``. Without it the
    shared ``paired_worker.PublicQuoteCollector`` is used and the sample is
    marked as a public Kraken observation. Injected samples are never
    eligible as public proof.
    """

    def __init__(self, *, collector=None, assets=ASSETS, timeframe=TIMEFRAME,
                 attempt_timeout_seconds=MAX_ATTEMPT_SECONDS, max_attempts=3,
                 retry_delay_seconds=2.0, clock=None, sleep=None, environ=None):
        assets = tuple(assets)
        if not assets or len(set(assets)) != len(assets) or any(asset not in KRAKEN_PAIRS for asset in assets):
            raise ValueError("pilot assets must be distinct supported EUR pairs")
        if timeframe not in KRAKEN_INTERVAL_MINUTES:
            raise ValueError(f"unsupported pilot timeframe: {timeframe!r}")
        if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be an integer from 1 through {MAX_ATTEMPTS}")
        if type(attempt_timeout_seconds) not in (int, float) or not 0 < attempt_timeout_seconds <= MAX_ATTEMPT_SECONDS:
            raise ValueError(f"attempt timeout must be within (0, {MAX_ATTEMPT_SECONDS}] seconds")
        if type(retry_delay_seconds) not in (int, float) or not 0 <= retry_delay_seconds <= 60:
            raise ValueError("retry delay must be within [0, 60] seconds")
        self.assets = assets
        self.timeframe = timeframe
        self.attempt_timeout_seconds = attempt_timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep or asyncio.sleep
        self.environ = environ
        self.source = SOURCE_PUBLIC if collector is None else SOURCE_INJECTED
        self._collector = collector
        self._closed = False

    def _quote_collector(self):
        if self._collector is None:
            from hermes_trading.paired_worker import PublicQuoteCollector
            self._collector = PublicQuoteCollector(self.timeframe, timeout_seconds=self.attempt_timeout_seconds)
        return self._collector

    async def aclose(self) -> None:
        """Close an injected collector exposing ``aclose``; idempotent.

        The default ``PublicQuoteCollector`` closes its own price and venue
        clients at the end of every ``collect`` call.
        """
        if self._closed:
            return
        self._closed = True
        close = getattr(self._collector, "aclose", None)
        if close is not None:
            await close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def collect_once(self) -> dict:
        """One shared sample for all eight accounts, retried a finite number of times."""
        if self._closed:
            raise RuntimeError("collector is closed")
        refuse_credentials(self.environ)
        collector = self._quote_collector()
        requested_at = self.clock()
        attempts = []
        snapshots = None
        for number in range(1, self.max_attempts + 1):
            started = self.clock()
            outcome, error = "complete", None
            try:
                # Hard ceiling: the reused collector also bounds itself, a
                # misbehaving injected one must not outlive the attempt.
                async with asyncio.timeout(self.attempt_timeout_seconds + 1):
                    result = await collector.collect(list(self.assets))
                if not isinstance(result, dict) or set(result) != set(self.assets):
                    raise ValueError("collector returned an incomplete asset set")
                snapshots = result
                if any(item.availability != "available" for item in result.values()):
                    outcome = "unavailable"
            except TimeoutError:
                outcome, error = "timeout", "attempt deadline exceeded"
            except Exception as failure:  # noqa: BLE001 - recorded, then retried
                outcome, error = "error", f"{type(failure).__name__}: {failure}"
            attempts.append({"attempt": number, "started_at": started.isoformat(),
                             "finished_at": self.clock().isoformat(), "outcome": outcome, "error": error})
            if outcome == "complete":
                break
            if number < self.max_attempts and self.retry_delay_seconds:
                await self.sleep(self.retry_delay_seconds)
        completed_at = self.clock()
        assets = [] if snapshots is None else [
            _asset_record(asset, self.timeframe, snapshots[asset]) for asset in self.assets]
        available = bool(assets) and all(row["availability"] == "available" for row in assets)
        body = {
            "schema_version": 1,
            "kind": "pilot_public_market_sample",
            "mode": "paper",
            "authenticated": False,
            "source": self.source,
            "public_proof_eligible": self.source == SOURCE_PUBLIC and available,
            "status": "available" if available else ("partial" if assets else "unavailable"),
            "timeframe": self.timeframe,
            "requested_at": requested_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "attempts": attempts,
            "accounts": list(ACCOUNTS),
            "assets": assets,
        }
        body["fingerprint"] = fingerprint(body)
        return body


def _refuse_output_links(root: Path) -> None:
    """Refuse output files that are symlinks, dangling ones included.

    ``Path.exists`` follows links, so a dangling ``market_samples.jsonl`` link
    would pass as absent and the append would create its target elsewhere.
    """
    for name in (SAMPLES_FILE, SUMMARY_FILE):
        if (root / name).is_symlink():
            raise ValueError(f"refusing symlinked {name} in {root}")


def _validate_root(root: Path) -> Path:
    _refuse_output_links(Path(root).absolute())
    root = Path(root).resolve()
    _refuse_output_links(root)
    if not any(part.startswith(".test-tmp") for part in root.parts):
        raise ValueError("collection root must be inside a .test-tmp* directory")
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError(f"collection root is not a directory: {root}")
    for name in (SAMPLES_FILE, SUMMARY_FILE):
        if (root / name).exists():
            raise ValueError(f"refusing to overwrite existing {name} in {root}")
    return root


async def run_collection(root, *, duration_seconds, interval_seconds=60.0, collector=None,
                         clock=None, sleep=None, environ=None, **collector_options) -> dict:
    """Collect shared samples until the bounded deadline; append them as JSONL.

    The run is capped at fifteen minutes and at ``ceil(duration / interval)``
    samples (one per started interval). Returns the summary also written to ``market_collection.json``.
    """
    if type(duration_seconds) not in (int, float) or not 0 < duration_seconds <= MAX_COLLECTION_SECONDS:
        raise ValueError(f"duration must be within (0, {MAX_COLLECTION_SECONDS}] seconds")
    if type(interval_seconds) not in (int, float) or not 1 <= interval_seconds <= duration_seconds:
        raise ValueError("interval must be at least one second and at most the duration")
    refuse_credentials(environ)
    root = _validate_root(root)
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleep = sleep or asyncio.sleep
    max_samples = math.ceil(duration_seconds / interval_seconds)
    root.mkdir(parents=True, exist_ok=True)
    samples_path = root / SAMPLES_FILE
    fingerprints, statuses = [], []
    eligible = 0
    async with PilotMarketCollector(collector=collector, clock=clock, sleep=sleep,
                                    environ=environ, **collector_options) as pilot:
        # Importing the worker stack is slow on some disks; it must not eat
        # the collection window, so the deadline starts once it is loaded.
        pilot._quote_collector()
        started = clock()
        deadline = started + timedelta(seconds=duration_seconds)
        cut = False
        try:
            # Hard wall-clock ceiling: retries of a late sample cannot push
            # the run past the requested duration (itself at most 15 min).
            async with asyncio.timeout(duration_seconds):
                while len(fingerprints) < max_samples and clock() < deadline:
                    sample = await pilot.collect_once()
                    with samples_path.open("a", encoding="utf-8") as handle:
                        handle.write(_canonical(sample) + "\n")
                    fingerprints.append(sample["fingerprint"])
                    statuses.append(sample["status"])
                    eligible += sample["public_proof_eligible"]
                    remaining = (deadline - clock()).total_seconds()
                    if len(fingerprints) >= max_samples or remaining <= 0:
                        break
                    await sleep(min(interval_seconds, remaining))
        except TimeoutError:
            cut = True
        source = pilot.source
    summary = {
        "schema_version": 1,
        "kind": "pilot_public_market_collection",
        "mode": "paper",
        "authenticated": False,
        "source": source,
        "accounts": list(ACCOUNTS),
        "started_at": started.isoformat(),
        "finished_at": clock().isoformat(),
        "duration_limit_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "samples": len(fingerprints),
        "cut_at_hard_deadline": cut,
        "statuses": {name: statuses.count(name) for name in sorted(set(statuses))},
        "public_proof_eligible_samples": eligible,
        "sample_fingerprints": fingerprints,
        "samples_file": SAMPLES_FILE,
        "samples_sha256": hashlib.sha256(samples_path.read_bytes()).hexdigest() if samples_path.exists() else None,
    }
    (root / SUMMARY_FILE).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded public Kraken collection shared by the eight pilot paper accounts.")
    parser.add_argument("--root", required=True, type=Path, help="output directory inside .test-tmp*")
    parser.add_argument("--duration-seconds", type=float, default=60.0, help=f"at most {MAX_COLLECTION_SECONDS}")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run_collection(args.root, duration_seconds=args.duration_seconds,
                                             interval_seconds=args.interval_seconds,
                                             max_attempts=args.max_attempts))
    except (ValueError, PermissionError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["public_proof_eligible_samples"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
