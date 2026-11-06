"""Chronological replay contract of archived public candles (LAT-33).

Every archive here comes from an injected fetch with invented candles: it is
a correctness double, marked ``injected_fetch``, and never public evidence.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from venue_fixtures import venue_record

from hermes_trading import pilot_paper, pilot_replay
from hermes_trading.pilot_market import SOURCE_PUBLIC, fingerprint

UTC = timezone.utc
HOUR_MS = 3600 * 1000
ASSET = "ETH/EUR"
PAPER_ENV = {"HERMES_TRADING_MODE": "paper"}


def invented_candles(start: datetime, count: int, *, in_progress: bool = True) -> list[dict]:
    """Invented OHLC rows as ccxt would return them, plus the open candle."""
    base = int(start.timestamp()) * 1000
    rows = []
    for index in range(count + (1 if in_progress else 0)):
        close = Decimal(2000 + (index * 37) % 101 - 50)
        rows.append({"timestamp": Decimal(base + index * HOUR_MS), "open": close + 1,
                     "high": close + 5, "low": close - 5, "close": close, "volume": Decimal("3.5")})
    return rows


def resign(archive: dict) -> dict:
    """Recompute digests so only the rule under test can refuse the archive."""
    archive["series_sha256"] = {asset: pilot_replay.series_sha256(rows) for asset, rows in archive["candles"].items()}
    body = {key: value for key, value in archive.items() if key != "fingerprint"}
    archive["fingerprint"] = fingerprint(body)
    return archive


class ReplayBase(unittest.TestCase):
    count = 80

    def setUp(self) -> None:
        self.base = Path(tempfile.gettempdir()) / ".test-tmp-pilot-replay"
        self.base.mkdir(parents=True, exist_ok=True)
        self.root = self.base / f"replay-{os.urandom(4).hex()}"
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.start = datetime(2026, 1, 1, tzinfo=UTC)
        self.requested = self.start + timedelta(hours=self.count, minutes=10)
        self.archive = self.fetch()

    def fetch(self, *, candles=None, root=None, venue=None):
        rows = invented_candles(self.start, self.count) if candles is None else candles
        times = iter([self.requested, self.requested + timedelta(seconds=2)])

        async def prices():
            return {ASSET: {"schema_version": 1, "candles": copy.deepcopy(rows)}}

        async def venues():
            return {ASSET: venue_record(ASSET, self.requested) if venue is None else venue}

        return asyncio.run(pilot_replay.fetch_archive(
            self.root if root is None else root, price_fetch=prices, venue_fetch=venues,
            clock=lambda: next(times), environ={}))

    @property
    def path(self) -> Path:
        return self.root / pilot_replay.ARCHIVE_FILE

    def write(self, archive: dict) -> None:
        self.path.write_text(json.dumps(archive), encoding="utf-8")

    def replay(self, **kwargs):
        options = dict(warmup_candles=20, history_limit=30)
        options.update(kwargs)
        return pilot_replay.HistoricalReplayCollector(self.path, **options)


class ArchiveTests(ReplayBase):
    def test_archive_keeps_only_closed_candles_with_dates_and_digests(self) -> None:
        archive = json.loads(self.path.read_text(encoding="utf-8"))
        rows = archive["candles"][ASSET]
        self.assertEqual(len(rows), self.count)  # the in-progress candle is dropped
        self.assertEqual(archive["source"], pilot_replay.SOURCE_INJECTED_FETCH)
        self.assertEqual(archive["requested_at"], self.requested.isoformat())
        self.assertEqual(archive["first_open"][ASSET], self.start.isoformat())
        self.assertEqual(archive["last_close"][ASSET], (self.start + timedelta(hours=self.count)).isoformat())
        self.assertEqual(archive["series_sha256"][ASSET], pilot_replay.series_sha256(rows))
        self.assertEqual(archive["fingerprint"], fingerprint({k: v for k, v in archive.items() if k != "fingerprint"}))
        self.assertTrue(all(isinstance(row["close"], str) and type(row["timestamp"]) is int for row in rows))
        self.assertEqual(archive["endpoints"][ASSET]["ohlc"],
                         "https://api.kraken.com/0/public/OHLC?pair=ETHEUR&interval=60")
        loaded, digest = pilot_replay.load_archive(self.path)
        self.assertEqual(loaded, archive)
        self.assertEqual(len(digest), 64)

    def test_archive_is_written_once_inside_test_tmp_without_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.fetch()
        with self.assertRaisesRegex(ValueError, ".test-tmp"):
            asyncio.run(pilot_replay.fetch_archive(Path(tempfile.gettempdir()) / "outside-replay", environ={}))
        with self.assertRaises(PermissionError):
            asyncio.run(pilot_replay.fetch_archive(self.root / "other", environ={"EXCHANGE_API_KEY": "x"}))
        self.assertFalse((self.root / "other").exists())

    def test_symlinked_archive_is_refused(self) -> None:
        link = self.root / "linked"
        link.mkdir()
        (link / pilot_replay.ARCHIVE_FILE).symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "symlink"):
            pilot_replay.load_archive(link / pilot_replay.ARCHIVE_FILE)

    def test_unusable_venue_record_is_refused_at_fetch(self) -> None:
        shutil.rmtree(self.root)
        with self.assertRaisesRegex(pilot_replay.ReplayArchiveError, "venue"):
            self.fetch(venue={"error": "venue_metadata_unavailable"})
        self.assertFalse(self.path.exists())

    def assert_refused(self, mutate, pattern, *, sign=True) -> None:
        archive = copy.deepcopy(self.archive)
        mutate(archive)
        self.write(resign(archive) if sign else archive)
        with self.assertRaisesRegex(pilot_replay.ReplayArchiveError, pattern):
            pilot_replay.load_archive(self.path)

    def test_altered_archive_is_refused_by_fingerprint_or_series_digest(self) -> None:
        def raise_close(archive):
            archive["candles"][ASSET][10]["close"] = "1"
        self.assert_refused(raise_close, "fingerprint", sign=False)

        def keep_series_digest(archive):
            archive["candles"][ASSET][10]["low"] = "1"
            body = {key: value for key, value in archive.items() if key != "fingerprint"}
            archive["fingerprint"] = fingerprint(body)
        self.assert_refused(keep_series_digest, "digest", sign=False)

    def test_invalid_histories_are_refused(self) -> None:
        cases = {
            "duplicated or out of order": lambda a: a["candles"][ASSET].insert(5, dict(a["candles"][ASSET][4])),
            "out of order": lambda a: a["candles"][ASSET].reverse(),
            "gap": lambda a: a["candles"][ASSET].pop(7),
            "aligned": lambda a: a["candles"][ASSET][0].update(timestamp=a["candles"][ASSET][0]["timestamp"] - 1),
            "integer milliseconds": lambda a: a["candles"][ASSET][3].update(timestamp="1767225600000"),
            "decimal string": lambda a: a["candles"][ASSET][3].update(close=2000.5),
            "OHLC": lambda a: a["candles"][ASSET][3].update(high="1"),
            "not closed": lambda a: a.update(requested_at=(self.start + timedelta(hours=self.count - 1)).isoformat(),
                                             completed_at=(self.start + timedelta(hours=self.count - 1)).isoformat()),
            "future-dated": lambda a: a.update(completed_at="2999-01-01T00:00:00+00:00"),
            "empty": lambda a: a["candles"].update({ASSET: []}),
            "venue": lambda a: a["venue_rules"].update({ASSET: {"error": "venue_metadata_unavailable"}}),
            "endpoints": lambda a: a["endpoints"][ASSET].update(ohlc="https://example.invalid/ohlc"),
            "source": lambda a: a.update(source="kraken_private"),
        }
        for pattern, mutate in cases.items():
            with self.subTest(pattern):
                self.assert_refused(mutate, pattern)

    def test_archive_must_match_pilot_assets_and_timeframe(self) -> None:
        with self.assertRaisesRegex(pilot_replay.ReplayArchiveError, "timeframe"):
            pilot_replay.load_archive(self.path, timeframe="15m")
        with self.assertRaisesRegex(pilot_replay.ReplayArchiveError, "assets"):
            pilot_replay.load_archive(self.path, assets=["BTC/EUR"])

    def test_archive_too_short_for_warmup_is_refused(self) -> None:
        with self.assertRaisesRegex(pilot_replay.ReplayArchiveError, "warmup"):
            self.replay(warmup_candles=self.count)


class ChronologyTests(ReplayBase):
    def drive(self, replay, cycles):
        async def run():
            seen = []
            for _ in range(cycles):
                seen.append((replay.clock(), await replay.collect((ASSET,))))
                await replay.sleep(3600)
            return seen
        return asyncio.run(run())

    def test_each_cycle_sees_only_candles_closed_at_its_instant(self) -> None:
        replay = self.replay()
        interval = timedelta(hours=1)
        for number, (at, snapshots) in enumerate(self.drive(replay, 10)):
            snapshot = snapshots[ASSET]
            expected_close = self.start + interval * (20 + number + 1)
            self.assertEqual(at, expected_close + timedelta(seconds=1))
            self.assertEqual(snapshot.candle.close_timestamp, expected_close)
            self.assertLessEqual(snapshot.candle.close_timestamp, at)
            self.assertEqual(snapshot.price["received_at"], at.isoformat())
            self.assertEqual(snapshot.venue_rules["received_at"], at.isoformat())
            self.assertEqual(snapshot.price["last"], snapshot.candle.close)
            self.assertEqual(len(snapshot.price["closes"]), min(30, 20 + number + 1))
            self.assertEqual(snapshot.price["closes"][-1], snapshot.candle.close)
        records = replay.provenance()["cycles"]
        self.assertEqual([row["candle_index"] for row in records], list(range(20, 30)))
        for row in records:
            self.assertLessEqual(row["assets"][0]["visible_last_close"], row["at"])

    def test_future_candles_cannot_change_an_earlier_cycle(self) -> None:
        baseline = [(at, snap[ASSET].price, snap[ASSET].candle) for at, snap in self.drive(self.replay(), 5)]
        altered = copy.deepcopy(self.archive)
        for row in altered["candles"][ASSET][26:]:  # candles closing after cycle 5
            row.update(open="9000", high="9100", low="8900", close="9050")
        self.write(resign(altered))
        replayed = [(at, snap[ASSET].price, snap[ASSET].candle) for at, snap in self.drive(self.replay(), 5)]
        self.assertEqual(baseline, replayed)
        self.assertNotEqual(self.replay().archive["fingerprint"], self.archive["fingerprint"])

    def test_clock_only_moves_through_sleep_and_a_repeated_candle_is_refused(self) -> None:
        replay = self.replay()
        start = replay.clock()
        asyncio.run(replay.collect((ASSET,)))
        self.assertEqual(replay.clock(), start)
        with self.assertRaises(pilot_replay.ReplayExhausted):
            asyncio.run(replay.collect((ASSET,)))
        asyncio.run(replay.sleep(1800))  # half a candle: still nothing new closed
        with self.assertRaises(pilot_replay.ReplayExhausted):
            asyncio.run(replay.collect((ASSET,)))
        asyncio.run(replay.sleep(1800))
        self.assertEqual(asyncio.run(replay.collect((ASSET,)))[ASSET].candle.close_timestamp,
                         start - timedelta(seconds=1) + timedelta(hours=1))

    def test_replay_stops_at_the_end_of_the_archive(self) -> None:
        replay = self.replay(warmup_candles=self.count - 2)
        self.assertEqual(replay.available_cycles, 2)
        self.drive(replay, 2)
        with self.assertRaises(pilot_replay.ReplayExhausted):
            asyncio.run(replay.collect((ASSET,)))

    def test_a_window_reaching_past_the_instant_raises(self) -> None:
        replay = self.replay()
        with patch.object(replay, "_visible_index", return_value=25):
            with self.assertRaises(pilot_replay.ReplayAnticipationError):
                asyncio.run(replay.collect((ASSET,)))

    def test_injected_archive_is_never_a_public_replay(self) -> None:
        replay = self.replay()
        self.assertEqual(replay.source, pilot_replay.SOURCE_REPLAY_NOT_PUBLIC)
        provenance = replay.provenance()
        self.assertEqual(provenance["archive_source"], pilot_replay.SOURCE_INJECTED_FETCH)
        self.assertEqual(provenance["execution_price_basis"], "current_candle_close")
        self.assertEqual(provenance["venue_timestamp_basis"], "restamped_at_cycle_instant")
        self.assertNotEqual(SOURCE_PUBLIC, provenance["archive_source"])

    def test_invalid_replay_settings_are_refused(self) -> None:
        for options in ({"warmup_candles": -1}, {"history_limit": 0}, {"history_limit": 721},
                        {"lag_seconds": 61}, {"warmup_candles": True}):
            with self.subTest(options), self.assertRaises(ValueError):
                self.replay(**options)


class ReplaySessionTests(ReplayBase):
    count = 40

    def setUp(self) -> None:
        super().setUp()
        self.pilot = self.root / "pilot"
        self.replay_double = self.replay(warmup_candles=20, history_limit=30)
        pilot_paper.init_pilot_accounts(self.pilot, now=self.replay_double.activation_at)

    def test_session_runs_on_the_replay_clock_and_is_not_public_proof(self) -> None:
        report = asyncio.run(pilot_paper.run_pilot_session(
            self.pilot, max_cycles=3, max_runtime_seconds=4 * 3600, interval_seconds=3600,
            environ=PAPER_ENV, replay=self.replay_double))
        self.assertEqual(report["stop_reason"], "max_cycles_reached", report["error"])
        self.assertEqual(report["completed_cycles"], 3)
        self.assertEqual(report["source"], pilot_replay.SOURCE_REPLAY_NOT_PUBLIC)
        self.assertEqual(report["public_proof"], "not_eligible_replay_not_public")
        self.assertEqual(report["public_proof_eligible_samples"], 0)
        replayed = report["replay"]["cycles"]
        self.assertEqual([row["cycle"] for row in replayed], [1, 2, 3])
        for cycle, row in zip(report["cycles"], replayed):
            self.assertEqual(cycle["started_at"], row["at"])
            self.assertLessEqual(row["assets"][0]["visible_last_close"], row["at"])
        self.assertEqual(report["replay"]["archive_fingerprint"], self.archive["fingerprint"])
        written = json.loads((self.pilot / pilot_paper.SESSION_REPORT).read_text(encoding="utf-8"))
        self.assertEqual(written["replay"]["archive_sha256"], self.replay_double.archive_sha256)

    def test_session_refuses_more_cycles_than_archived_or_mixed_doubles(self) -> None:
        with self.assertRaisesRegex(ValueError, "only 20 cycle"):
            asyncio.run(pilot_paper.run_pilot_session(
                self.pilot, max_cycles=21, max_runtime_seconds=3600, interval_seconds=3600,
                environ=PAPER_ENV, replay=self.replay_double))
        with self.assertRaisesRegex(ValueError, "one archived candle"):
            asyncio.run(pilot_paper.run_pilot_session(
                self.pilot, max_cycles=1, max_runtime_seconds=3600, interval_seconds=1800,
                environ=PAPER_ENV, replay=self.replay_double))
        with self.assertRaisesRegex(ValueError, "own collector"):
            asyncio.run(pilot_paper.run_pilot_session(
                self.pilot, max_cycles=1, max_runtime_seconds=3600, interval_seconds=3600,
                environ=PAPER_ENV, replay=self.replay_double, clock=lambda: datetime.now(UTC)))

    def test_cli_init_only_activates_accounts_at_the_replay_start(self) -> None:
        other = self.root / "cli-pilot"
        with patch("sys.stdout"):
            pilot_paper.main(["--root", str(other), "--init-only", "--replay-archive", str(self.path),
                              "--replay-warmup", "20"])
        account = json.loads((other / "adaptive_100" / "wallet_account.json").read_text(encoding="utf-8"))
        self.assertEqual(account["activated_at"], self.replay_double.activation_at.isoformat())

    def test_cli_refuses_an_invalid_archive(self) -> None:
        archive = copy.deepcopy(self.archive)
        archive["candles"][ASSET].pop(3)
        self.write(resign(archive))
        with patch("sys.stderr"), self.assertRaises(SystemExit) as stop:
            pilot_paper.main(["--root", str(self.root / "cli-bad"), "--init-only",
                              "--replay-archive", str(self.path)])
        self.assertEqual(stop.exception.code, 2)
        self.assertFalse((self.root / "cli-bad").exists())


if __name__ == "__main__":
    unittest.main()
