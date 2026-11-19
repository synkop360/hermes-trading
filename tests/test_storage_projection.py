"""Storage growth projection, heartbeat ``storage_projection`` contract (LAT-120, delivery 3)."""
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_trading import paper_history_retention as retention


NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
MiB = 1024 * 1024
LIMITS = dict(reserve_bytes=128 * MiB, max_state_bytes=512 * MiB)
CONTRACT_KEYS = {"schema_version", "status", "bytes_per_day", "days_before_reserve"}


def headroom(state, free):
    return max(0, min(free - LIMITS["reserve_bytes"],
                      LIMITS["max_state_bytes"] - LIMITS["reserve_bytes"] - state))


class StorageProjectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def sample(self, at, state_bytes, free_bytes=10 * 1024 * MiB):
        return retention.storage_projection(self.root, now=at, state_bytes=state_bytes,
                                            free_bytes=free_bytes, **LIMITS)

    def assertContract(self, result):
        self.assertTrue(CONTRACT_KEYS <= set(result), result)
        self.assertEqual(result["schema_version"], 1)

    def test_first_sample_is_insufficient_data(self):
        result = self.sample(NOW, 10 * MiB)
        self.assertContract(result)
        self.assertEqual((result["status"], result["bytes_per_day"], result["days_before_reserve"]),
                         ("insufficient_data", None, None))

    def test_less_than_one_day_of_history_is_insufficient_data(self):
        self.sample(NOW - timedelta(hours=23), 10 * MiB)
        result = self.sample(NOW, 20 * MiB)
        self.assertEqual(result["status"], "insufficient_data")

    def test_samples_of_the_same_day_do_not_make_history(self):
        self.sample(NOW, 10 * MiB)
        self.sample(NOW + timedelta(hours=1), 11 * MiB)
        result = self.sample(NOW + timedelta(hours=2), 12 * MiB)
        self.assertEqual(result["status"], "insufficient_data")

    def test_positive_growth_projects_whole_days_with_contract_formula(self):
        for day in range(5):
            result = self.sample(NOW + timedelta(days=day), 10 * MiB + day * 3 * MiB)
        self.assertContract(result)
        self.assertEqual(result["status"], "projected")
        self.assertIs(type(result["bytes_per_day"]), int)
        self.assertEqual(result["bytes_per_day"], 3 * MiB)
        state = 10 * MiB + 4 * 3 * MiB
        self.assertEqual(result["days_before_reserve"], headroom(state, 10 * 1024 * MiB) // (3 * MiB))
        self.assertIs(type(result["days_before_reserve"]), int)

    def test_free_space_bounds_the_headroom(self):
        free = LIMITS["reserve_bytes"] + 10 * MiB
        self.sample(NOW - timedelta(days=1), 10 * MiB, free)
        result = self.sample(NOW, 12 * MiB, free)
        self.assertEqual((result["status"], result["bytes_per_day"], result["days_before_reserve"]),
                         ("projected", 2 * MiB, 5))

    def test_zero_growth_is_non_positive(self):
        for day in range(3):
            result = self.sample(NOW + timedelta(days=day), 50 * MiB)
        self.assertEqual((result["status"], result["bytes_per_day"], result["days_before_reserve"]),
                         ("non_positive_growth", 0, None))

    def test_negative_growth_is_non_positive(self):
        for day in range(3):
            result = self.sample(NOW + timedelta(days=day), 50 * MiB - day * MiB)
        self.assertEqual(result["status"], "non_positive_growth")
        self.assertLess(result["bytes_per_day"], 0)
        self.assertIsNone(result["days_before_reserve"])

    def test_under_the_filesystem_reserve_reports_reserve_reached_and_zero_days(self):
        self.sample(NOW - timedelta(days=1), 10 * MiB)
        result = self.sample(NOW, 11 * MiB, free_bytes=LIMITS["reserve_bytes"] - 1)
        self.assertEqual((result["status"], result["days_before_reserve"]), ("reserve_reached", 0))

    def test_state_budget_exhausted_reports_reserve_reached_even_without_history(self):
        state = LIMITS["max_state_bytes"] - LIMITS["reserve_bytes"]
        result = self.sample(NOW, state)
        self.assertEqual((result["status"], result["days_before_reserve"]), ("reserve_reached", 0))

    def test_window_uses_the_last_thirty_one_days_only(self):
        for day in range(45):
            # Fast growth first, then slow: the old fast days must drop out.
            size = day * 10 * MiB if day < 10 else 90 * MiB + (day - 9) * MiB
            result = self.sample(NOW + timedelta(days=day), size)
        self.assertEqual(result["bytes_per_day"], MiB)
        self.assertLessEqual(result["sample_days"], 31)
        db = sqlite3.connect(self.root / retention.SUMMARY_DB)
        try:
            self.assertLessEqual(db.execute("SELECT COUNT(*) FROM storage_samples").fetchone()[0], 32)
        finally:
            db.close()

    def test_invalid_inputs_raise(self):
        for bad in (-1, True, 1.5, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "non-negative integer"):
                self.sample(NOW, bad)

    def test_heartbeat_projection_reads_the_worker_storage_report(self):
        report = {"status": "ready", "state_bytes": 10 * MiB, "free_bytes": 10 * 1024 * MiB,
                  "limits": dict(LIMITS, schema_version=1, experiment_id="x")}
        result = retention.heartbeat_projection(self.root, report, now=NOW)
        self.assertContract(result)
        self.assertEqual(result["status"], "insufficient_data")

    def test_heartbeat_projection_without_measurement_is_unavailable_not_an_error(self):
        for report in (None, {}, {"status": "blocked", "state_bytes": None, "free_bytes": None, "limits": LIMITS}):
            with self.subTest(report=report):
                result = retention.heartbeat_projection(self.root, report, now=NOW)
                self.assertContract(result)
                self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / retention.SUMMARY_DB).exists())


if __name__ == "__main__":
    unittest.main()
