from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_trading import paper_offline
from hermes_trading.reflect import run_fallback_reflection
from hermes_trading.reflection_supervision import WriterNotAuthorized


class PaperOfflineReflectionTests(unittest.TestCase):
    """Prove that a paper-offline session is deterministic-worker owned and
    that ``reflect --fallback`` can consume its closed trades without ever
    calling a model provider.
    """

    def _tmp_root(self) -> Path:
        base = Path(tempfile.gettempdir()) / ".test-tmp-paper-offline-reflection-pytest"
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

    def test_session_declares_deterministic_worker_owner(self) -> None:
        root = self._tmp_root()
        result = self._run_session(root)

        owner_path = root / "account" / "reflection_owner.json"
        self.assertTrue(owner_path.is_file())
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        self.assertEqual(owner["schema_version"], 1)
        self.assertEqual(owner["owner"], "deterministic_worker")
        self.assertTrue(owner["experiment_id"])
        self.assertEqual(owner["cadence_closed_trades"], 5)
        self.assertIs(owner["one_variable_only"], True)
        self.assertEqual(result["paths"]["reflection_owner"], str(owner_path))

    def test_fallback_reflection_consumes_closures_and_writes_hypothesis(self) -> None:
        root = self._tmp_root()
        self._run_session(root)
        account_dir = root / "account"

        with (
            patch("hermes_trading.reflect.httpx.post") as post,
            patch("hermes_trading.model_runner.subprocess.run") as run,
        ):
            updated = run_fallback_reflection(account_dir)
            post.assert_not_called()
            run.assert_not_called()

        self.assertEqual(updated["version"], "02")
        self.assertIn(updated["entry"]["threshold"], {28, 32})

        hypotheses = [
            json.loads(line)
            for line in (account_dir / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        fallback = next(
            (h for h in hypotheses if h.get("mode") == "fallback"),
            None,
        )
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["strategy_version"], "02")
        self.assertIn("changed", fallback)
        self.assertIn("before", fallback)
        self.assertIn("after", fallback)
        self.assertIn("reason", fallback)

        # Ownership was not converted by the fallback run.
        owner = json.loads((account_dir / "reflection_owner.json").read_text(encoding="utf-8"))
        self.assertEqual(owner["owner"], "deterministic_worker")

    def test_fallback_reflection_refuses_external_llm_owner(self) -> None:
        root = self._tmp_root()
        self._run_session(root)
        account_dir = root / "account"

        owner = json.loads((account_dir / "reflection_owner.json").read_text(encoding="utf-8"))
        owner["owner"] = "external_llm"
        owner["model"] = {"provider": "openrouter", "name": "test-model"}
        (account_dir / "reflection_owner.json").write_text(
            json.dumps(owner, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with (
            patch("hermes_trading.reflect.httpx.post") as post,
            patch("hermes_trading.reflect._load_yaml") as load_yaml,
        ):
            with self.assertRaises(WriterNotAuthorized):
                run_fallback_reflection(account_dir)
            post.assert_not_called()
            load_yaml.assert_not_called()

    def test_cli_reflect_fallback_runs_on_paper_offline_account(self) -> None:
        root = self._tmp_root()
        self._run_session(root)
        env = dict(os.environ)
        env.setdefault("PYTHONPATH", "tests")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_trading.reflect",
                "--fallback",
                "--state-dir",
                str(root / "account"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version: '02'", result.stdout)
        self.assertIn("entry:", result.stdout)


if __name__ == "__main__":
    unittest.main()
