import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import hermes_watch


class WatchCadenceTests(unittest.TestCase):
    def count(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "records.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in rows))
            return hermes_watch._count_after(p, datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_closed_trades_use_close_time_and_exclude_old_history(self):
        self.assertEqual(self.count([
            {"closed_at": "2026-09-30T23:59:00Z"},
            {"closed_at": "2026-10-01T00:00:00Z"},
            {"closed_at": "2026-10-01T00:01:00Z"},
        ]), 1)

    def test_decisions_use_decision_time(self):
        self.assertEqual(self.count([
            {"decided_at": "2026-09-30T00:00:00Z"},
            {"decided_at": "2026-10-02T00:00:00Z"},
        ]), 1)

    def test_missing_or_invalid_time_cannot_trigger_a_paid_review(self):
        self.assertEqual(self.count([{}, {"closed_at": "bad"}, {"closed_at": 1},
                                     {"closed_at": ["bad"]}, {"closed_at": {"bad": 1}}]), 0)

    def test_legacy_timestamp_and_naive_utc_are_supported(self):
        self.assertEqual(self.count([
            {"timestamp": "2026-09-30T00:00:00"},
            {"timestamp": "2026-10-02T00:00:00"},
        ]), 1)

    def test_maintenance_blocks_sync_and_model_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "watcher_maintenance.json").write_text('{"reason":"history repair"}')
            with patch.object(hermes_watch, "STATE_DIR", state), \
                    patch.object(hermes_watch, "_load_dotenv") as env, \
                    patch.object(hermes_watch, "_download_remote_file") as download, \
                    patch.object(hermes_watch, "_run_reflection") as reflect:
                self.assertEqual(hermes_watch.main(), 0)
            env.assert_not_called()
            download.assert_not_called()
            reflect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
