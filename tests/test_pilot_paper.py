"""The pilot bootstrap initializes eight deterministic-worker accounts only.

No collection, model access or order flow is exercised here; the doubles of
tests/fakes stay reserved to the modules that need them.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from hermes_trading import pilot_paper
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

    def test_dangling_symlink_root_is_refused_and_target_not_created(self) -> None:
        target = self.base / f"target-{os.urandom(4).hex()}"
        self.addCleanup(lambda: shutil.rmtree(target, ignore_errors=True))
        self.root.symlink_to(target, target_is_directory=True)
        self.addCleanup(lambda: self.root.unlink() if self.root.is_symlink() else None)
        with self.assertRaises(ValueError):
            pilot_paper.init_pilot_accounts(self.root, now=self.at)
        self.assertFalse(target.exists())
        self.assertTrue(self.root.is_symlink())

    def test_symlinked_parent_is_refused_and_target_not_written(self) -> None:
        target = self.base / f"target-{os.urandom(4).hex()}"
        target.mkdir()
        self.addCleanup(lambda: shutil.rmtree(target, ignore_errors=True))
        alias = self.base / f"alias-{os.urandom(4).hex()}"
        alias.symlink_to(target, target_is_directory=True)
        self.addCleanup(lambda: alias.unlink() if alias.is_symlink() else None)
        with self.assertRaises(ValueError):
            pilot_paper.init_pilot_accounts(alias / "pilot", now=self.at)
        self.assertEqual(list(target.iterdir()), [])

    def test_root_outside_test_tmp_is_refused(self) -> None:
        probe = Path(tempfile.gettempdir()).resolve()
        while probe.parent != probe and any(
            part.startswith(".test-tmp") for part in probe.parts
        ):
            probe = probe.parent
        self.assertFalse(any(part.startswith(".test-tmp") for part in probe.parts))
        outside = probe / f"pilot-outside-{os.urandom(8).hex()}"
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



# ---------------------------------------------------------------------------
# LAT-32: bounded session, canonical engine and causal fallback publication.
# Every collector below is an invented test double: never market evidence.
# ---------------------------------------------------------------------------

from datetime import timedelta
from decimal import Decimal

from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.persistence.repository import StateRepository

PAPER_ENV = {"HERMES_TRADING_MODE": "paper"}


class FakeClock:
    """Logical time that only moves when the session sleeps."""

    def __init__(self, start):
        self.now = start
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class PriceDouble:
    """Invented ETH/EUR quotes, one price per collection, stamped by the clock."""

    def __init__(self, clock, prices, *, fail_from=None, error=None):
        self.clock = clock
        self.prices = list(prices)
        self.history = [Decimal("100")] * 15
        self.calls = 0
        self.closed = 0
        self.fail_from = fail_from
        self.error = error or OSError("invented outage")

    async def collect(self, assets):
        from venue_fixtures import venue_record

        self.calls += 1
        if self.fail_from is not None and self.calls >= self.fail_from:
            raise self.error
        now = self.clock()
        price = Decimal(self.prices[min(self.calls, len(self.prices)) - 1])
        self.history.append(price)
        close = now - timedelta(minutes=1)
        return {asset: MarketSnapshot(
            asset, "1h", "available", "evaluate", "test-double",
            candle=ClosedCandle(close - timedelta(hours=1), close, price, price, price, price, Decimal("1")),
            price={"closes": tuple(self.history), "bid": price, "ask": price,
                   "received_at": now.isoformat(), "source": "test-double"},
            venue_rules=venue_record(asset, now)) for asset in assets}

    async def aclose(self):
        self.closed += 1


class PilotSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.gettempdir()) / ".test-tmp-pilot-paper"
        self.base.mkdir(parents=True, exist_ok=True)
        self.root = self.base / f"session-{os.urandom(4).hex()}"
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
        pilot_paper.init_pilot_accounts(self.root, now=self.at)
        self.clock = FakeClock(self.at + timedelta(hours=1))

    def run_session(self, double, **kwargs):
        options = dict(max_cycles=3, max_runtime_seconds=6 * 3600, interval_seconds=3600)
        options.update(kwargs)
        return asyncio.run(pilot_paper.run_pilot_session(
            self.root, collector=double, clock=self.clock, sleep=self.clock.sleep,
            environ=PAPER_ENV, collector_options={"retry_delay_seconds": 0}, **options))

    def strategy_bytes(self, name):
        return (self.root / name / "strategy.yaml").read_bytes()

    def positions(self, name):
        repo = StateRepository.open_read_only(self.root / name / "trading.db")
        try:
            return repo.portfolio_positions()
        finally:
            repo.close()

    def publications(self):
        db = sqlite3.connect(self.root / "adaptive_100" / "paper_publications.sqlite")
        try:
            return [(json.loads(request), None if receipt is None else json.loads(receipt))
                    for request, receipt in db.execute("SELECT request, receipt FROM publications ORDER BY rowid")]
        finally:
            db.close()

    def test_eight_accounts_share_each_cycle_and_fallback_publishes_one_variable(self) -> None:
        controls = {w: self.strategy_bytes(f"control_{w}") for w in (50, 100, 150, 200)}
        double = PriceDouble(self.clock, ["80", "70", "80"])
        report = self.run_session(double)
        self.assertEqual(report["stop_reason"], "max_cycles_reached")
        self.assertEqual(report["completed_cycles"], 3)
        self.assertEqual(double.calls, 3)  # one shared collection per cycle
        self.assertEqual(double.closed, 1)
        self.assertTrue(report["collector_closed"])
        self.assertEqual(report["model_access"], "not_used")
        for cycle in report["cycles"]:
            self.assertEqual(cycle["status"], "complete")
            self.assertEqual(len(cycle["accounts"]), 8)
            self.assertEqual(set(cycle["accounts"]), NAMES)
            self.assertEqual(cycle["sample"]["source"], "injected_collector")
            reflection = cycle["reflection"]
            self.assertEqual(reflection["status"], "published")
            self.assertIn(reflection["variable"], ("entry.threshold", "stop_loss_pct"))
        # Causal chain: one receipt per reflection, versions strictly consecutive.
        rows = self.publications()
        self.assertEqual(len(rows), 3)
        versions = [(int(r["base_strategy"]["version"]), int(r["candidate"]["version"])) for r, _ in rows]
        self.assertEqual(versions, [(1, 2), (2, 3), (3, 4)])
        for request, receipt in rows:
            self.assertEqual(receipt["status"], "published")
            self.assertEqual(receipt["writer"], "deterministic_worker")
            self.assertEqual(request["writer"], "deterministic_worker")
            changed = [k for k in pilot_paper._leaf_changes(request["base_strategy"], request["candidate"])
                       if k != "version"]
            self.assertEqual(changed, [request["variable"]])
        active = hashlib.sha256(self.strategy_bytes("adaptive_100")).hexdigest()
        self.assertEqual(rows[-1][1]["candidate_sha256"], active)
        baseline = json.loads((self.root / "manifest.json").read_text())["profiles"][1]
        self.assertEqual(baseline["directory"], "adaptive_100")
        self.assertEqual(hashlib.sha256((self.root / "adaptive_100/history/v0001.yaml").read_bytes()).hexdigest(),
                         baseline["strategy_sha256"])
        # Controls never move; the eight accounts all traded through the engine.
        for wallet, before in controls.items():
            self.assertEqual(self.strategy_bytes(f"control_{wallet}"), before)
        for name in NAMES:
            self.assertGreaterEqual(len(self.positions(name)), 1, name)
        # The first entry uses v1; the re-entry after the first publication uses the published version.
        adaptive_versions = [p.strategy_version for p in self.positions("adaptive_100")]
        self.assertEqual(adaptive_versions[0], "1")
        if len(adaptive_versions) > 1:
            self.assertNotEqual(adaptive_versions[-1], "1")
        for p in self.positions("control_100"):
            self.assertEqual(p.strategy_version, "1")
        # Fallback traceability: hypotheses bound to their publication receipt.
        hypotheses = [json.loads(line) for line in
                      (self.root / "adaptive_100/hypotheses.jsonl").read_text().splitlines()]
        self.assertEqual([h["review_id"] for h in hypotheses], [r["review_id"] for _, r in rows])
        self.assertTrue(all(h["mode"] == "fallback" for h in hypotheses))
        for wallet in (50, 100, 150, 200):
            self.assertFalse((self.root / f"control_{wallet}" / "hypotheses.jsonl").exists())
            self.assertFalse((self.root / f"control_{wallet}" / "model_access.json").exists())
            self.assertFalse((self.root / f"adaptive_{wallet}" / "model_access.json").exists())
        # A fixture never yields a public proof.
        self.assertEqual(report["public_proof"], "not_eligible_injected_collector")
        saved = json.loads((self.root / pilot_paper.SESSION_REPORT).read_text())
        self.assertEqual(saved["cycles"], json.loads(json.dumps(report["cycles"])))

    def test_session_resumes_on_the_same_publication_chain(self) -> None:
        self.run_session(PriceDouble(self.clock, ["80"]), max_cycles=1)
        self.clock.now += timedelta(hours=1)
        report = self.run_session(PriceDouble(self.clock, ["70"]), max_cycles=1)
        self.assertEqual(report["cycles"][0]["status"], "complete")
        versions = [r["candidate"]["version"] for r, _ in self.publications()]
        self.assertEqual([int(v) for v in versions], [2, 3])

    def test_runtime_limit_stops_before_the_cycle_limit(self) -> None:
        double = PriceDouble(self.clock, ["80"])
        report = self.run_session(double, max_cycles=10, max_runtime_seconds=2.5 * 3600)
        self.assertEqual(report["stop_reason"], "max_runtime_reached")
        self.assertEqual(double.calls, 3)
        self.assertEqual(self.clock.sleeps, [3600, 3600, 1800])
        self.assertEqual(double.closed, 1)

    def test_stop_file_interrupts_between_cycles(self) -> None:
        class StopAfterFirst(PriceDouble):
            async def collect(inner, assets):
                result = await PriceDouble.collect(inner, assets)
                (self.root / pilot_paper.STOP_FILE).write_text("stop")
                return result
        double = StopAfterFirst(self.clock, ["80"])
        report = self.run_session(double, max_cycles=5)
        self.assertEqual(report["stop_reason"], "stop_requested")
        self.assertEqual(report["completed_cycles"], 1)
        self.assertEqual(double.closed, 1)
        with self.assertRaises(pilot_paper.PilotSessionError):
            self.run_session(PriceDouble(self.clock, ["80"]))

    def test_cancellation_is_recorded_and_closes_the_collector(self) -> None:
        double = PriceDouble(self.clock, ["80"])

        async def cancelled_sleep(seconds):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(pilot_paper.run_pilot_session(
                self.root, collector=double, clock=self.clock, sleep=cancelled_sleep,
                environ=PAPER_ENV, max_cycles=3, max_runtime_seconds=3 * 3600))
        report = json.loads((self.root / pilot_paper.SESSION_REPORT).read_text())
        self.assertEqual(report["stop_reason"], "interrupted")
        self.assertEqual(report["completed_cycles"], 1)
        self.assertEqual(double.closed, 1)

    def test_collection_failure_stops_without_packet_and_closes(self) -> None:
        double = PriceDouble(self.clock, ["80"], fail_from=2)
        report = self.run_session(double, max_cycles=3)
        self.assertEqual(report["stop_reason"], "collection_unavailable")
        self.assertEqual([c["status"] for c in report["cycles"]], ["complete", "collection_unavailable"])
        self.assertEqual(report["cycles"][1]["sample"]["attempts"], 3)
        self.assertEqual(double.closed, 1)
        db = sqlite3.connect(self.root / "adaptive_100" / "paper_publications.sqlite")
        try:
            statuses = [row[0] for row in db.execute("SELECT status FROM paired_batches")]
        finally:
            db.close()
        self.assertEqual(statuses, ["complete"])

    def test_engine_error_is_reported_and_reraised(self) -> None:
        double = PriceDouble(self.clock, ["80"])
        pilot_paper.activate_pilot_manifest(self.root)
        control = self.root / "control_50" / "strategy.yaml"
        control.write_text(control.read_text() + "\n# drift\n")
        with self.assertRaises(ValueError):
            self.run_session(double)
        report = json.loads((self.root / pilot_paper.SESSION_REPORT).read_text())
        self.assertEqual(report["stop_reason"], "error")
        self.assertIn("ValueError", report["error"])
        self.assertEqual(double.closed, 1)

    def test_credentials_live_mode_and_unbounded_settings_are_refused(self) -> None:
        double = PriceDouble(self.clock, ["80"])
        for environ in ({"HERMES_TRADING_MODE": "live"}, {"EXCHANGE_API_KEY": "x"}):
            with self.assertRaises((pilot_paper.PilotSessionError, PermissionError)):
                asyncio.run(pilot_paper.run_pilot_session(
                    self.root, collector=double, clock=self.clock, sleep=self.clock.sleep,
                    environ=environ, max_cycles=1, max_runtime_seconds=60))
        for bounds in ({"max_cycles": 0}, {"max_cycles": pilot_paper.MAX_SESSION_CYCLES + 1},
                       {"max_runtime_seconds": 0}, {"interval_seconds": 0}, {"reflect_every": 0}):
            with self.assertRaises(ValueError):
                self.run_session(double, **bounds)
        self.assertEqual(double.calls, 0)
        manifest = json.loads((self.root / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "pilot_bootstrap")

    def test_session_requires_a_bootstrap_inside_test_tmp(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        for root in (outside, self.base / "missing-root"):
            with self.assertRaises(ValueError):
                asyncio.run(pilot_paper.run_pilot_session(
                    root, collector=PriceDouble(self.clock, ["80"]), environ=PAPER_ENV,
                    max_cycles=1, max_runtime_seconds=60))

    def test_interrupted_fallback_publication_is_completed_before_a_new_one(self) -> None:
        from hermes_trading import paper_publication

        self.run_session(PriceDouble(self.clock, ["80"]), max_cycles=1, reflect_every=2)
        with patch.object(paper_publication, "_replace_strategy", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                pilot_paper.reflect_adaptive_primary(self.root, now=self.clock())
        rows = self.publications()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][1])
        self.assertEqual(yaml.safe_load(self.strategy_bytes("adaptive_100"))["version"], "1")
        result = pilot_paper.reflect_adaptive_primary(self.root, now=self.clock())
        self.assertEqual(result["status"], "recovered")
        rows = self.publications()
        self.assertEqual(rows[0][1]["candidate_sha256"],
                         hashlib.sha256(self.strategy_bytes("adaptive_100")).hexdigest())

    def crash_before_receipt(self):
        """Replace the adaptive strategy (v1 -> v2) then crash before the receipt."""
        from hermes_trading import paper_publication

        pilot_paper.activate_pilot_manifest(self.root)
        with patch.object(paper_publication, "_commit_receipt", side_effect=OSError("crash before receipt")):
            with self.assertRaises(OSError):
                pilot_paper.reflect_adaptive_primary(self.root, now=self.clock())
        rows = self.publications()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][1])
        self.assertEqual(int(yaml.safe_load(self.strategy_bytes("adaptive_100"))["version"]), 2)
        self.clock.now += timedelta(hours=1)

    def assert_recovered_chain(self, controls, *, publications):
        rows = self.publications()
        self.assertEqual(len(rows), publications)  # no duplicate intent
        self.assertTrue(all(receipt is not None for _, receipt in rows))
        self.assertEqual(len({receipt["review_id"] for _, receipt in rows}), publications)
        versions = [(int(r["base_strategy"]["version"]), int(r["candidate"]["version"])) for r, _ in rows]
        self.assertEqual(versions, [(v, v + 1) for v in range(1, publications + 1)])
        self.assertEqual(rows[-1][1]["candidate_sha256"],
                         hashlib.sha256(self.strategy_bytes("adaptive_100")).hexdigest())
        for wallet, before in controls.items():
            self.assertEqual(self.strategy_bytes(f"control_{wallet}"), before)
        return rows

    def test_session_recovers_a_strategy_replaced_before_its_receipt(self) -> None:
        controls = {w: self.strategy_bytes(f"control_{w}") for w in (50, 100, 150, 200)}
        self.crash_before_receipt()
        double = PriceDouble(self.clock, ["80"])
        report = self.run_session(double, max_cycles=1, reflect_every=2)
        self.assertEqual(report["completed_cycles"], 1)
        self.assertEqual(report["recovered_publication"]["status"], "recovered")
        rows = self.assert_recovered_chain(controls, publications=1)
        self.assertEqual(report["recovered_publication"]["receipt"], rows[0][1])
        # The chain continues from the recovered version, without a second recovery.
        self.clock.now += timedelta(hours=1)
        report = self.run_session(PriceDouble(self.clock, ["70"]), max_cycles=1)
        self.assertIsNone(report["recovered_publication"])
        self.assertEqual(report["cycles"][0]["reflection"]["status"], "published")
        self.assert_recovered_chain(controls, publications=2)

    def test_cli_run_recovers_a_strategy_replaced_before_its_receipt(self) -> None:
        controls = {w: self.strategy_bytes(f"control_{w}") for w in (50, 100, 150, 200)}
        self.crash_before_receipt()
        double = PriceDouble(self.clock, ["80", "70"])
        argv = ["--root", str(self.root), "--run", "--max-cycles", "2",
                "--max-runtime-seconds", "7200", "--interval-seconds", "3600"]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            pilot_paper.main(argv, collector=double, clock=self.clock, sleep=self.clock.sleep,
                             environ=PAPER_ENV)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["completed_cycles"], 2)
        self.assertEqual(report["recovered_publication"]["status"], "recovered")
        self.assertEqual([c["reflection"]["status"] for c in report["cycles"]], ["published", "published"])
        self.assert_recovered_chain(controls, publications=3)

    def assert_report_link_refused(self, link, target, double, *, run=None):
        with self.assertRaises(ValueError):
            (run or self.run_session)(double)
        self.assertEqual(double.calls, 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(target))
        leftovers = [p.name for p in self.root.iterdir() if p.name.startswith(".pilot_session-")]
        self.assertEqual(leftovers, [])

    def test_dangling_report_symlink_is_refused_before_any_write(self) -> None:
        target = self.base / f"outside-{os.urandom(4).hex()}.json"
        link = self.root / pilot_paper.SESSION_REPORT
        link.symlink_to(target)
        manifest = (self.root / "manifest.json").read_bytes()
        self.assert_report_link_refused(link, target, PriceDouble(self.clock, ["80"]))
        self.assertFalse(target.exists())
        self.assertEqual((self.root / "manifest.json").read_bytes(), manifest)  # not even activated

    def test_dangling_report_symlink_is_refused_even_with_an_engine_error(self) -> None:
        target = self.base / f"outside-{os.urandom(4).hex()}.json"
        pilot_paper.activate_pilot_manifest(self.root)
        control = self.root / "control_50" / "strategy.yaml"
        control.write_text(control.read_text() + "\n# drift\n")
        link = self.root / pilot_paper.SESSION_REPORT
        link.symlink_to(target)
        self.assert_report_link_refused(link, target, PriceDouble(self.clock, ["80"]))
        self.assertFalse(target.exists())

    def test_report_symlink_to_an_existing_file_is_not_followed(self) -> None:
        target = self.base / f"outside-{os.urandom(4).hex()}.json"
        target.write_text("untouched")
        self.addCleanup(lambda: target.unlink(missing_ok=True))
        link = self.root / pilot_paper.SESSION_REPORT
        link.symlink_to(target)
        self.assert_report_link_refused(link, target, PriceDouble(self.clock, ["80"]))
        self.assertEqual(target.read_text(), "untouched")

    def test_cli_refuses_a_dangling_report_symlink(self) -> None:
        target = self.base / f"outside-{os.urandom(4).hex()}.json"
        link = self.root / pilot_paper.SESSION_REPORT
        link.symlink_to(target)
        double = PriceDouble(self.clock, ["80"])
        argv = ["--root", str(self.root), "--run", "--max-cycles", "1", "--max-runtime-seconds", "60"]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            pilot_paper.main(argv, collector=double, clock=self.clock, sleep=self.clock.sleep,
                             environ=PAPER_ENV)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(double.calls, 0)
        self.assertTrue(link.is_symlink())
        self.assertFalse(target.exists())

    def test_cli_run_is_bounded_and_prints_the_report(self) -> None:
        double = PriceDouble(self.clock, ["80", "70"])
        argv = ["--root", str(self.root), "--run", "--max-cycles", "2",
                "--max-runtime-seconds", "7200", "--interval-seconds", "3600"]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            pilot_paper.main(argv, collector=double, clock=self.clock, sleep=self.clock.sleep,
                             environ=PAPER_ENV)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["completed_cycles"], 2)
        self.assertEqual(double.calls, 2)
        self.assertEqual(double.closed, 1)

    def test_cli_run_requires_explicit_bounds(self) -> None:
        for argv in (["--root", str(self.root), "--run"],
                     ["--root", str(self.root), "--run", "--max-cycles", "2"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                pilot_paper.main(argv, collector=PriceDouble(self.clock, ["80"]), environ=PAPER_ENV)
            self.assertEqual(ctx.exception.code, 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            pilot_paper.main(["--root", str(self.root), "--run", "--max-cycles", "9999",
                              "--max-runtime-seconds", "60"],
                             collector=PriceDouble(self.clock, ["80"]), environ=PAPER_ENV)
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
