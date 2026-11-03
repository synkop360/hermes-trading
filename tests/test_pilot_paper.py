"""The pilot bootstrap initializes eight deterministic-worker accounts only.

No collection, model access or order flow is exercised here; the doubles of
tests/fakes stay reserved to the modules that need them.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

import pilot_paper
from hermes_trading.execution_policy import strategy_is_frozen
from hermes_trading.reflection_supervision import load_owner_policy, writer_allowed

NAMES = {f"{arm}_{wallet}" for arm in ("adaptive", "control") for wallet in (50, 100, 150, 200)}


class PilotPaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.gettempdir()) / ".test-tmp-pilot-paper"
        self.base.mkdir(parents=True, exist_ok=True)
        self.root = self.base / f"pilot-{os.urandom(4).hex()}"
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_init_creates_eight_fresh_eur_accounts_owned_by_deterministic_worker(self) -> None:
        receipt = pilot_paper.init_pilot_accounts(self.root, now=self.at)
        self.assertEqual(receipt["status"], "initialized")
        self.assertEqual(receipt["collection"], "not_started")
        self.assertEqual(receipt["model_access"], "not_configured")
        self.assertEqual(set(receipt["accounts"]), NAMES)
        for name in NAMES:
            arm, wallet = name.split("_")
            account_dir = self.root / name
            config = json.loads((account_dir / "wallet_account.json").read_text(encoding="utf-8"))
            self.assertEqual(config["schema_version"], 1)
            self.assertEqual(config["currency"], "EUR")
            self.assertEqual(config["starting_balance"], wallet)
            self.assertEqual(config["experiment_id"], receipt["experiment_id"])
            self.assertEqual(config["activated_at"], self.at.isoformat())
            policy = load_owner_policy(account_dir)
            self.assertEqual(policy["owner"], "deterministic_worker")
            self.assertEqual(policy["experiment_id"], receipt["experiment_id"])
            self.assertTrue(writer_allowed(account_dir, "deterministic_worker"))
            self.assertFalse(writer_allowed(account_dir, "external_llm"))

    def test_controls_are_fixed_and_strategies_are_bound_in_manifest(self) -> None:
        pilot_paper.init_pilot_accounts(self.root, now=self.at)
        controls, adaptives = [], []
        for wallet in (50, 100, 150, 200):
            control = yaml.safe_load((self.root / f"control_{wallet}" / "strategy.yaml").read_text())
            adaptive = yaml.safe_load((self.root / f"adaptive_{wallet}" / "strategy.yaml").read_text())
            self.assertTrue(strategy_is_frozen(control))
            self.assertFalse(strategy_is_frozen(adaptive))
            controls.append(json.dumps(control, sort_keys=True))
            adaptives.append(json.dumps(adaptive, sort_keys=True))
        self.assertEqual(len(set(controls)), 1)
        self.assertEqual(len(set(adaptives)), 1)
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "pilot_bootstrap")
        self.assertFalse(manifest["execution_enabled"])
        self.assertEqual(len(manifest["profiles"]), 8)
        self.assertEqual({profile["directory"] for profile in manifest["profiles"]}, NAMES)
        for profile in manifest["profiles"]:
            digest = hashlib.sha256(
                (self.root / profile["directory"] / "strategy.yaml").read_bytes()
            ).hexdigest()
            self.assertEqual(profile["strategy_sha256"], digest)

    def test_existing_root_is_refused_and_nothing_is_written(self) -> None:
        self.root.mkdir(parents=True)
        with self.assertRaises(ValueError):
            pilot_paper.init_pilot_accounts(self.root, now=self.at)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_root_outside_test_tmp_is_refused(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "pilot-outside"
        self.addCleanup(lambda: shutil.rmtree(outside.parent, ignore_errors=True))
        with self.assertRaises(ValueError):
            pilot_paper.init_pilot_accounts(outside, now=self.at)
        self.assertFalse(outside.exists())

    def test_bootstrap_is_deterministic_for_a_fixed_clock(self) -> None:
        other = self.base / f"pilot-{os.urandom(4).hex()}"
        self.addCleanup(lambda: shutil.rmtree(other, ignore_errors=True))
        pilot_paper.init_pilot_accounts(self.root, now=self.at)
        pilot_paper.init_pilot_accounts(other, now=self.at)
        self.assertEqual(
            (self.root / "manifest.json").read_bytes(),
            (other / "manifest.json").read_bytes(),
        )
        for name in NAMES:
            for filename in ("strategy.yaml", "goal.yaml", "wallet_account.json",
                             "wallet_costs.json", "reflection_owner.json"):
                self.assertEqual(
                    (self.root / name / filename).read_bytes(),
                    (other / name / filename).read_bytes(),
                    f"{name}/{filename} differs between identical boots",
                )

    def test_bootstrap_starts_no_collection_or_model_state(self) -> None:
        pilot_paper.init_pilot_accounts(self.root, now=self.at)
        for path in self.root.rglob("*"):
            self.assertTrue(str(path).startswith(str(self.root)), f"escape: {path}")
        for name in NAMES:
            account_dir = self.root / name
            self.assertFalse((account_dir / "trading.db").exists())
            self.assertFalse((account_dir / "model_access.json").exists())
            self.assertFalse((account_dir / "paper_publications.sqlite").exists())
            self.assertFalse((account_dir / "wallet_equity.sqlite").exists())

    def test_cli_init_only_prints_the_receipt(self) -> None:
        argv = ["pilot_paper", "--root", str(self.root), "--init-only"]
        stdout = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
            pilot_paper.main()
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["status"], "initialized")
        self.assertEqual(set(receipt["accounts"]), NAMES)
        self.assertTrue((self.root / "manifest.json").is_file())

    def test_cli_without_init_only_is_rejected(self) -> None:
        argv = ["pilot_paper", "--root", str(self.root)]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                pilot_paper.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertFalse(self.root.exists())

    def test_cli_refuses_an_existing_root(self) -> None:
        self.root.mkdir(parents=True)
        argv = ["pilot_paper", "--root", str(self.root), "--init-only"]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                pilot_paper.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
