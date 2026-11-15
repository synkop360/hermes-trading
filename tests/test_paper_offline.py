from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_trading import paper_offline


class PaperOfflineTests(unittest.TestCase):
    def _tmp_root(self) -> Path:
        base = Path(tempfile.gettempdir()) / ".test-tmp-paper-offline-pytest"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"session-{os.urandom(4).hex()}"
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def _run_session(self, root: Path, cycles: int = 6) -> dict:
        with paper_offline._block_sockets():
            result = __import__("asyncio").run(
                paper_offline.run_paper_offline(
                    root,
                    cycles=cycles,
                    interval_seconds=0,
                    fixture_path=paper_offline._default_fixture_path(),
                )
            )
        return result

    def test_session_runs_six_cycles_and_produces_entry_and_exit(self) -> None:
        root = self._tmp_root()
        result = self._run_session(root)

        account_dir = root / "account"
        self.assertTrue((account_dir / "trading.db").is_file())
        self.assertTrue((account_dir / "decisions.jsonl").is_file())
        self.assertTrue((account_dir / "trades.jsonl").is_file())
        self.assertTrue((account_dir / "result.json").is_file())

        decisions = [
            json.loads(line)
            for line in (account_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        actions = {d["action"] for d in decisions}
        self.assertIn("enter", actions)
        self.assertIn("exit", actions)

        self.assertEqual(result["cycles_completed"], 6)
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["execution"], "simulated")
        self.assertEqual(result["origin"], "synthetic_offline_fixture")
        self.assertGreaterEqual(result["financials"]["trade_count"], 1)
        self.assertIsNotNone(result["financials"]["realized_pnl"])
        self.assertNotEqual(result["financials"]["realized_pnl"], "0")

    def test_result_json_matches_database(self) -> None:
        root = self._tmp_root()
        result = self._run_session(root)
        written = json.loads((root / "account" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(written["cycles_completed"], result["cycles_completed"])
        self.assertEqual(written["financials"]["trade_count"], result["financials"]["trade_count"])
        self.assertEqual(written["financials"]["realized_pnl"], result["financials"]["realized_pnl"])

    def test_existing_root_is_rejected(self) -> None:
        root = self._tmp_root()
        root.mkdir(parents=True)
        with self.assertRaises(ValueError):
            __import__("asyncio").run(
                paper_offline.run_paper_offline(
                    root,
                    cycles=6,
                    interval_seconds=0,
                    fixture_path=paper_offline._default_fixture_path(),
                )
            )

    def test_main_rejects_existing_root(self) -> None:
        root = self._tmp_root()
        root.mkdir(parents=True)
        argv = ["paper_offline", "--root", str(root), "--cycles", "6"]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                paper_offline.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_network_sockets_are_blocked(self) -> None:
        root = self._tmp_root()
        with paper_offline._block_sockets():
            result = __import__("asyncio").run(
                paper_offline.run_paper_offline(
                    root,
                    cycles=6,
                    interval_seconds=0,
                    fixture_path=paper_offline._default_fixture_path(),
                )
            )
        self.assertEqual(result["cycles_completed"], 6)

    def test_state_stays_under_root(self) -> None:
        root = self._tmp_root()
        self._run_session(root)
        for path in root.rglob("*"):
            self.assertTrue(
                str(path).startswith(str(root)),
                f"file written outside the session root: {path}",
            )


if __name__ == "__main__":
    unittest.main()
