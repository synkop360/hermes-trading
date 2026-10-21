import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call

from scripts import repair_hypothesis_log as repair

from scripts.repair_hypothesis_log import repair_bytes, apply_repair


class HistoryRepairTests(unittest.TestCase):
    def test_uv_launcher_and_init_are_not_paused_as_workers(self):
        args = [b"-m", b"hermes_trading.colocated"]
        self.assertFalse(repair.is_python_worker(args, "uv", 1))
        self.assertFalse(repair.is_python_worker(args, "python3.11", 1))
        self.assertTrue(repair.is_python_worker(args, "python3.11", 5))

    def test_failed_repair_resumes_every_paused_worker(self):
        with patch.object(repair.signal, "SIGSTOP", 19, create=True), \
                patch.object(repair.signal, "SIGCONT", 18, create=True), \
                patch.object(repair.os, "kill") as kill, \
                patch.object(repair, "_process_state", return_value="S"), \
                patch.object(repair, "_wait_stopped"), \
                patch.object(repair, "_open_log_fd", return_value=False), \
                patch.object(repair, "apply_repair", side_effect=ValueError("drift")):
            with self.assertRaises(ValueError):
                repair.coordinated_repair(Path("unused"), [12, 13], expected_sha256="x", bad_sha256="y")
            self.assertEqual(kill.call_args_list, [call(12, 19), call(13, 19), call(13, 18), call(12, 18)])

    def sample(self):
        bad = b'{"timestamp":"broken" value}\n'
        return b'{"timestamp":"2026-01-01T00:00:00Z"}\n' + bad + b'{"timestamp":"2026-01-02T00:00:00Z"}\n', bad

    def test_only_known_bad_record_changes_and_original_bytes_are_retained(self):
        source, bad = self.sample()
        fixed, detail = repair_bytes(source, hashlib.sha256(bad).hexdigest())
        rows = [json.loads(x) for x in fixed.splitlines()]
        self.assertNotIn("timestamp", rows[1])
        self.assertEqual(base64.b64decode(rows[1]["raw_base64"]), bad)
        self.assertEqual(rows[0]["timestamp"], "2026-01-01T00:00:00Z")
        self.assertEqual(rows[2]["timestamp"], "2026-01-02T00:00:00Z")
        self.assertEqual(detail["line"], 2)

    def test_unknown_corruption_aborts(self):
        source, _ = self.sample()
        with self.assertRaises(ValueError):
            repair_bytes(source, "unknown")

    def test_current_hash_mismatch_leaves_file_unchanged(self):
        source, bad = self.sample()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hypotheses.jsonl"
            path.write_bytes(source)
            with self.assertRaises(ValueError):
                apply_repair(path, expected_sha256="stale", bad_sha256=hashlib.sha256(bad).hexdigest())
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_atomic_repair_preserves_backup_and_parsable_replacement(self):
        source, bad = self.sample()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hypotheses.jsonl"
            path.write_bytes(source)
            result = apply_repair(path, expected_sha256=hashlib.sha256(source).hexdigest(),
                                  bad_sha256=hashlib.sha256(bad).hexdigest())
            self.assertEqual(Path(result["backup"]).read_bytes(), source)
            for line in path.read_bytes().splitlines():
                json.loads(line)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["after_sha256"])


if __name__ == "__main__":
    unittest.main()
