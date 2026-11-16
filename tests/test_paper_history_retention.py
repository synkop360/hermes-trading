"""Tests for hermes_trading.paper_history_retention (LAT-120, delivery 1)."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_trading import paper_history_retention as retention
from hermes_trading.paired_packet_storage import store_packet, store_record
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _json, _hash


NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


class PaperHistoryRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manifest = {
            "schema_version": 1,
            "status": "paper_ready",
            "execution_enabled": True,
            "assets": ["ETH/EUR"],
            "timeframe": "1h",
            "experiment_id": "retention-test",
            "primary_wallet_eur": 100,
            "profiles": [
                {"arm": "adaptive", "wallet_eur": 100, "directory": "adaptive_100"},
                {"arm": "control", "wallet_eur": 100, "directory": "control_100"},
            ],
        }
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        for profile in self.manifest["profiles"]:
            (self.root / profile["directory"]).mkdir()
        selection = resolve_primary(self.manifest)
        self.primary_dir = self.root / selection.profile_directory(self.manifest, "adaptive")
        self.primary_dir.mkdir(parents=True, exist_ok=True)

    def _write_observations(self, account, stamps):
        db_path = self.root / account / "wallet_equity.sqlite"
        config = {
            "schema_version": 1,
            "experiment_id": "retention-test",
            "currency": "EUR",
            "starting_balance": "100",
            "activated_at": (NOW - timedelta(days=40)).isoformat(),
        }
        db = sqlite3.connect(db_path, timeout=0, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE wallet_config (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL)")
            db.execute("CREATE TABLE observations (at TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("INSERT INTO wallet_config VALUES (1,?)", (_json(config),))
            for stamp in stamps:
                observation = {
                    "schema_version": 1,
                    "at": stamp.isoformat(),
                    "currency": "EUR",
                    "starting_balance": "100",
                    "external_flow": "0",
                    "net_equity": "100",
                    "known_accrued_costs": "0.1",
                    "known_cost_equity": "99.9",
                }
                db.execute(
                    "INSERT INTO observations VALUES (?,?)",
                    (stamp.isoformat(), store_record(db, observation)),
                )
            db.commit()
        finally:
            db.close()

    def _write_batches(self, stamps):
        db_path = self.primary_dir / "paper_publications.sqlite"
        db = sqlite3.connect(db_path, timeout=0, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """CREATE TABLE paired_batches(
                    batch_id TEXT PRIMARY KEY, status TEXT, payload BLOB,
                    input_hash TEXT, completed TEXT)"""
            )
            for index, stamp in enumerate(stamps):
                packet = {
                    "schema_version": 1,
                    "batch_id": f"batch-{index}",
                    "created_at": stamp.isoformat(),
                    "strategies": {"adaptive": {"version": "1"}},
                    "assets": ["ETH/EUR"],
                }
                payload = store_packet(db, packet)
                completed = {"adaptive_100": {"status": "observed"}}
                db.execute(
                    "INSERT INTO paired_batches VALUES (?,?,?,?,?)",
                    (packet["batch_id"], "complete", payload, _hash(packet), store_record(db, completed)),
                )
            db.commit()
        finally:
            db.close()

    def observation_times(self, account):
        db_path = self.root / account / "wallet_equity.sqlite"
        if not db_path.exists():
            return []
        with closing(sqlite3.connect(db_path)) as db:
            return [row[0] for row in db.execute("SELECT at FROM observations ORDER BY at")]

    def batch_times(self):
        from hermes_trading.paired_packet_storage import load_packet
        db_path = self.primary_dir / "paper_publications.sqlite"
        if not db_path.exists():
            return []
        with closing(sqlite3.connect(db_path)) as db:
            return [
                load_packet(db, row[0])["created_at"]
                for row in db.execute("SELECT payload FROM paired_batches ORDER BY rowid")
            ]

    def test_retention_keeps_thirty_days_inclusive(self):
        cutoff = NOW - timedelta(days=30)
        stamps = [cutoff - timedelta(seconds=1), cutoff, NOW]
        self._write_observations("adaptive_100", stamps)
        result = retention.apply_retention(self.root, now=NOW, backup_verified=True)
        kept = self.observation_times("adaptive_100")
        self.assertEqual(kept, [cutoff.isoformat(), NOW.isoformat()])
        self.assertEqual(result["accounts"]["adaptive_100"]["kept"], 2)
        self.assertEqual(result["accounts"]["adaptive_100"]["archived"], 1)

    def test_archive_is_gzip_with_verified_receipt(self):
        cutoff = NOW - timedelta(days=30)
        old = cutoff - timedelta(days=1)
        self._write_observations("adaptive_100", [old, cutoff, NOW])
        retention.apply_retention(self.root, now=NOW, backup_verified=True)
        archive = self.root / ".history-retention" / f"adaptive_100_wallet_equity_{cutoff.date().isoformat()}.jsonl.gz"
        receipt = archive.with_suffix(".receipt.json")
        self.assertTrue(archive.is_file())
        self.assertTrue(receipt.is_file())
        record = json.loads(receipt.read_text())
        self.assertEqual(record["kind"], "wallet_equity:adaptive_100")
        self.assertEqual(record["row_count"], 1)
        # Verify the archive round-trips byte-identically.
        decoded = retention.restore_wallet_archive(archive)
        self.assertEqual(len(decoded), record["original_bytes"])
        self.assertEqual(retention._sha(decoded), record["original_sha256"])

    def test_no_deletion_when_backup_not_verified(self):
        cutoff = NOW - timedelta(days=30)
        stamps = [cutoff - timedelta(days=1), cutoff, NOW]
        self._write_observations("adaptive_100", stamps)
        with self.assertRaisesRegex(ValueError, "daily backup"):
            retention.apply_retention(self.root, now=NOW, backup_verified=False)
        self.assertEqual(len(self.observation_times("adaptive_100")), 3)
        self.assertFalse((self.root / ".history-retention").exists())

    def test_corrupt_archive_prevents_deletion_and_keeps_original(self):
        cutoff = NOW - timedelta(days=30)
        old = cutoff - timedelta(days=1)
        self._write_observations("adaptive_100", [old, cutoff, NOW])

        def fault(stage):
            if stage == "after_archive_write":
                archive = self.root / ".history-retention" / f"adaptive_100_wallet_equity_{cutoff.date().isoformat()}.jsonl.gz"
                archive.write_bytes(b"corrupt")

        with self.assertRaisesRegex(ValueError, "retention archive"):
            retention.apply_retention(self.root, now=NOW, backup_verified=True, fault=fault)
        self.assertEqual(len(self.observation_times("adaptive_100")), 3)

    def test_publication_batches_retained_with_thirty_day_boundary(self):
        cutoff = NOW - timedelta(days=30)
        stamps = [cutoff - timedelta(seconds=1), cutoff, NOW]
        self._write_batches(stamps)
        result = retention.apply_retention(self.root, now=NOW, backup_verified=True)
        kept = self.batch_times()
        self.assertEqual(kept, [cutoff.isoformat(), NOW.isoformat()])
        self.assertEqual(result["publications"]["kept"], 2)
        self.assertEqual(result["publications"]["archived"], 1)
        archive = self.root / ".history-retention" / f"paper_publications_{cutoff.date().isoformat()}.jsonl.gz"
        self.assertTrue(archive.is_file())

    def test_daily_summaries_include_archived_days(self):
        cutoff = NOW - timedelta(days=30)
        # One observation per day from 32 days before the cutoff up to now.
        stamps = [cutoff - timedelta(days=32 - day) for day in range(32)] + [NOW]
        self._write_observations("adaptive_100", stamps)
        self._write_observations("control_100", stamps)
        retention.apply_retention(self.root, now=NOW, backup_verified=True)
        summaries = retention.daily_summaries(self.root)
        self.assertEqual(set(summaries), {"adaptive_100", "control_100"})
        for account, rows in summaries.items():
            self.assertEqual(len(rows), 33, account)
            self.assertEqual(rows[0]["date"], (cutoff - timedelta(days=32)).date().isoformat())
            self.assertEqual(rows[-1]["date"], NOW.date().isoformat())
            for row in rows:
                self.assertIsInstance(row["balance"], str)
                self.assertIsInstance(row["costs"], str)
                self.assertIs(type(row["decisions"]), int)

    def test_idempotent_repeated_retention_is_safe(self):
        cutoff = NOW - timedelta(days=30)
        stamps = [cutoff - timedelta(days=1), cutoff, NOW]
        self._write_observations("adaptive_100", stamps)
        retention.apply_retention(self.root, now=NOW, backup_verified=True)
        result = retention.apply_retention(self.root, now=NOW, backup_verified=True)
        self.assertEqual(result["accounts"]["adaptive_100"]["archived"], 0)
        self.assertEqual(self.observation_times("adaptive_100"), [cutoff.isoformat(), NOW.isoformat()])


    # --- delivery 3: summaries exact across retention runs, lifetime counter ---
    def test_day_split_by_the_cutoff_keeps_its_full_decision_count(self):
        day = (NOW - timedelta(days=30)).replace(hour=0)
        stamps = [day + timedelta(hours=hour) for hour in (1, 6, 18)] + [NOW]
        self._write_observations("adaptive_100", stamps)
        expected = {row["date"]: row["decisions"] for row in retention.daily_summaries(self.root)["adaptive_100"]}
        for hours in (0, 12, 24):
            retention.apply_retention(self.root, now=NOW + timedelta(hours=hours), backup_verified=True)
            got = {row["date"]: row["decisions"] for row in retention.daily_summaries(self.root)["adaptive_100"]}
            self.assertEqual(got, expected, hours)
        self.assertEqual(expected[day.date().isoformat()], 3)
        self.assertEqual(self.observation_times("adaptive_100"), [NOW.isoformat()])

    def test_crash_between_summary_and_deletion_does_not_double_count(self):
        old = NOW - timedelta(days=31)
        self._write_observations("adaptive_100", [old, NOW])

        def crash(stage):
            if stage == "before_detail_delete":
                raise RuntimeError("synthetic crash")
        with self.assertRaises(RuntimeError):
            retention.apply_retention(self.root, now=NOW, backup_verified=True, fault=crash)
        self.assertEqual(len(self.observation_times("adaptive_100")), 2)
        before = retention.daily_summaries(self.root)["adaptive_100"]
        self.assertEqual([row["decisions"] for row in before], [1, 1])
        retention.apply_retention(self.root, now=NOW + timedelta(hours=1), backup_verified=True)
        self.assertEqual(retention.daily_summaries(self.root)["adaptive_100"], before)
        self.assertEqual(self.observation_times("adaptive_100"), [NOW.isoformat()])

    def test_summary_carries_the_last_balance_and_costs_of_the_day(self):
        old = NOW - timedelta(days=40)
        self._write_observations("adaptive_100", [old, old + timedelta(hours=1), NOW])
        retention.apply_retention(self.root, now=NOW, backup_verified=True)
        first = retention.daily_summaries(self.root)["adaptive_100"][0]
        self.assertEqual(first, {"date": old.date().isoformat(), "balance": "99.9", "decisions": 2, "costs": "0.1"})

    def test_latest_and_pending_batches_are_never_removed_and_lifetime_count_survives(self):
        from hermes_trading.paired_worker import _stats
        stamps = [NOW - timedelta(days=40 - day) for day in range(5)]
        self._write_batches(stamps)
        with closing(sqlite3.connect(self.primary_dir / "paper_publications.sqlite")) as db:
            db.execute("UPDATE paired_batches SET status='pending' WHERE batch_id='batch-0'")
            db.commit()
        self.assertEqual(_stats(self.primary_dir)["complete"], 4)
        result = retention.apply_retention(self.root, now=NOW, backup_verified=True)
        self.assertEqual(result["publications"]["archived"], 3)
        with closing(sqlite3.connect(self.primary_dir / "paper_publications.sqlite")) as db:
            left = db.execute("SELECT batch_id,status FROM paired_batches ORDER BY rowid").fetchall()
        self.assertEqual(left, [("batch-0", "pending"), ("batch-4", "complete")])
        stats = _stats(self.primary_dir)
        self.assertEqual(stats["complete"], 4)
        self.assertEqual(stats["latest"]["batch_id"], "batch-4")
        retention.apply_retention(self.root, now=NOW + timedelta(days=1), backup_verified=True)
        self.assertEqual(_stats(self.primary_dir)["complete"], 4)

    def test_same_day_rerun_with_new_old_rows_writes_a_second_verified_archive(self):
        cutoff = NOW - timedelta(days=30)
        self._write_observations("adaptive_100", [cutoff - timedelta(hours=2), cutoff + timedelta(hours=1), NOW])
        retention.apply_retention(self.root, now=NOW, backup_verified=True)
        retention.apply_retention(self.root, now=NOW + timedelta(hours=2), backup_verified=True)
        archives = sorted((self.root / ".history-retention").glob("adaptive_100_wallet_equity_*.jsonl.gz"))
        self.assertEqual(len(archives), 2)
        for archive in archives:
            raw = retention.restore_wallet_archive(archive)
            self.assertEqual(len(raw.splitlines()), 1)
        self.assertEqual(self.observation_times("adaptive_100"), [NOW.isoformat()])

    def test_tampered_archive_is_refused_on_restore(self):
        self._write_observations("adaptive_100", [NOW - timedelta(days=31), NOW])
        retention.apply_retention(self.root, now=NOW, backup_verified=True)
        archive = next((self.root / ".history-retention").glob("*.jsonl.gz"))
        archive.write_bytes(gzip.compress(b"{}\n", mtime=0))
        with self.assertRaisesRegex(ValueError, "differs"):
            retention.restore_wallet_archive(archive)


if __name__ == "__main__":
    unittest.main()
