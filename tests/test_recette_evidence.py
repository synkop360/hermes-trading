"""Offline evidence checks of saved recette reports (LAT-141, delivery 2).

Reports come from the real service on ``.test-tmp-recette-evidence-*`` volumes
with an injected session double and a logical clock. These are local runs:
they prove the checks, not the recette (which is observed on ``GET /recette``).
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from hermes_trading import pilot_paper
from scripts import recette_evidence as evidence
from scripts import recette_fingerprint as fingerprint
from scripts import recette_service as service
from scripts import recette_status_report as status_report

AT = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
PAPER = {"HERMES_TRADING_MODE": "paper", "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
         "HERMES_NOTIFY_ENABLED": "false"}
PROJECTED = {"schema_version": 1, "status": "projected", "bytes_per_day": 1000,
             "days_before_reserve": 400, "headroom_bytes": 400000, "sample_days": 1}


class EvidenceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-evidence-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.now = AT
        self.expected = fingerprint.runtime_fingerprint()["fingerprint"]

    async def runner(self, root, *, environ, bounds, on_cycle=None):
        (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
        return {"stop_reason": "max_cycles_reached", "completed_cycles": 1, "samples": 1,
                "entries": 0, "exits": 0, "public_proof": "not_eligible_injected_collector"}

    async def no_sleep(self, seconds):
        return None

    def served(self, environ=None) -> dict:
        """One local service run, then the report as ``GET /recette`` would serve it."""
        stop = self.root / service.PILOT_DIRNAME / pilot_paper.STOP_FILE
        stop.unlink(missing_ok=True)
        asyncio.run(service.serve(self.root, environ=PAPER if environ is None else environ,
                                  stream=io.StringIO(), sleep=self.no_sleep, report_clock=lambda: self.now,
                                  session_runner=self.runner, max_cycles=1, interval_seconds=3600))
        report = status_report.read_recette_report(self.root / status_report.REPORT_NAME)
        # The STOP lock of the local run is not part of the evidence under test.
        report["locks"] = {**report["locks"], "active": [], "stop_requested": False}
        return {**report, "freshness": status_report.freshness(report, now=self.now)}

    def save(self, report: dict, name: str) -> Path:
        path = self.base / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    @staticmethod
    def failed(result: dict) -> list[str]:
        return [row["criterion"] for row in result["criteria"] if not row["passed"]]


class WindowTests(EvidenceFixture):
    def test_projected_report_with_the_delivery_fingerprint_may_open_the_window(self) -> None:
        report = dict(self.served(), growth_and_projection=dict(PROJECTED))
        result = evidence.check_window(report, self.expected)
        self.assertEqual(self.failed(result), [])
        self.assertTrue(result["window_may_open"])
        self.assertIn("integration_frozen_during_window", result["rules"])

    def test_insufficient_data_or_other_uncomputed_projections_never_qualify(self) -> None:
        report = self.served()
        self.assertEqual(report["growth_and_projection"]["status"], "insufficient_data")
        self.assertEqual(self.failed(evidence.check_window(report, self.expected)), ["projection_computed"])
        for status in ("non_positive_growth", "unavailable", "reserve_reached"):
            with self.subTest(status=status):
                changed = dict(report, growth_and_projection=dict(PROJECTED, status=status))
                self.assertEqual(self.failed(evidence.check_window(changed, self.expected)),
                                 ["projection_computed"])

    def test_unknown_or_different_fingerprint_never_qualifies(self) -> None:
        report = dict(self.served(), growth_and_projection=dict(PROJECTED))
        self.assertEqual(self.failed(evidence.check_window(report, None)), ["runtime_matches_delivery"])
        self.assertEqual(self.failed(evidence.check_window(report, "0" * 64)), ["runtime_matches_delivery"])
        unknown = dict(report, runtime=fingerprint.unavailable("not_provided"))
        self.assertEqual(self.failed(evidence.check_window(unknown, self.expected)),
                         ["runtime_fingerprint_identified", "runtime_matches_delivery"])
        forged = copy.deepcopy(report)
        forged["runtime"]["components"]["scripts"]["sha256"] = "a" * 64
        self.assertIn("runtime_fingerprint_identified", self.failed(evidence.check_window(forged, self.expected)))

    def test_stale_unhealthy_or_unserved_reports_never_qualify(self) -> None:
        report = dict(self.served(), growth_and_projection=dict(PROJECTED))
        cases = {
            "served_and_fresh": [{k: v for k, v in report.items() if k != "freshness"},
                                 dict(report, freshness={"stale": True})],
            "storage_ready": [dict(report, storage=dict(report["storage"], status="blocked"))],
            "no_recent_errors": [dict(report, recent_errors=[{"at": None, "source": "x", "code": "error"}])],
            "no_active_locks": [dict(report, locks=dict(report["locks"], active=["engine_halted"]))],
            "session_clean": [dict(report, session={"status": "finished", "stop_reason": "engine_halted",
                                                    "error_code": None})],
            "eight_accounts": [dict(report, accounts=dict(report["accounts"], count=7))],
        }
        for criterion, reports in cases.items():
            for changed in reports:
                with self.subTest(criterion=criterion):
                    self.assertEqual(self.failed(evidence.check_window(changed, self.expected)), [criterion])

    def test_window_close_needs_24_hours_on_the_same_runtime_and_volume(self) -> None:
        opening = dict(self.served(), growth_and_projection=dict(PROJECTED))
        self.now = AT + timedelta(hours=24)
        closing = dict(self.served(), growth_and_projection=dict(PROJECTED))
        result = evidence.check_window_close(opening, closing, self.expected)
        self.assertEqual(self.failed(result), [])
        self.now = AT + timedelta(hours=23, minutes=59)
        early = dict(self.served(), growth_and_projection=dict(PROJECTED))
        self.assertEqual(self.failed(evidence.check_window_close(opening, early, self.expected)),
                         ["at_least_24_hours"])
        merged = copy.deepcopy(closing)
        merged["runtime"] = fingerprint.summarize({
            "hermes_trading": {"a.py": "0" * 64}, "scripts": {"b.py": "1" * 64},
            "dependencies": {"uv.lock": "2" * 64, "pyproject.toml": "3" * 64}})
        result = evidence.check_window_close(opening, merged, self.expected)
        self.assertIn("same_runtime_fingerprint", self.failed(result))
        self.assertIn("closing_qualifies", self.failed(result))
        row = next(r for r in result["criteria"] if r["criterion"] == "same_runtime_fingerprint")
        self.assertEqual(row["observed"]["on_change"], "new_merge_requires_requalification_then_new_window")


class RestartTests(EvidenceFixture):
    def test_restart_on_the_same_volume_proves_pilot_durability(self) -> None:
        before = self.served()
        self.now = AT + timedelta(minutes=30)
        after = self.served()
        result = evidence.check_restart(before, after)
        self.assertEqual(self.failed(result), [])
        self.assertTrue(result["durability_proven"])
        self.assertEqual(result["bounds_provenance"]["after"]["max_cycles"],
                         {"value": 1, "source": "argument"})
        self.assertTrue(result["runtime"]["same"])
        self.assertEqual(result["not_covered"], ["history_retention_archive", "worker_recovery"])

    def test_a_new_volume_or_lost_state_is_not_durability(self) -> None:
        before = self.served()
        shutil.rmtree(self.root)
        self.now = AT + timedelta(hours=1)
        fresh = self.served()
        self.assertEqual(self.failed(evidence.check_restart(before, fresh)), [
            "same_volume_identity", "volume_recognized_after_restart", "notification_state_resumed"])
        lost = copy.deepcopy(fresh)
        lost["volume"] = dict(before["volume"], status="recognized",
                              service_started_at=fresh["volume"]["service_started_at"])
        lost["notifications"] = dict(before["notifications"], state="resumed",
                                     restarts=before["notifications"]["restarts"] + 1)
        lost["daily_summary"]["total_decisions"] = -1
        lost["accounts"]["accounts"].pop("control_50")
        self.assertEqual(self.failed(evidence.check_restart(before, lost)),
                         ["accounts_preserved", "history_preserved"])
        same_process = copy.deepcopy(before)
        self.assertIn("new_service_process", self.failed(evidence.check_restart(before, same_process)))

    def test_cli_reads_saved_and_wrapped_public_observations(self) -> None:
        before = self.save(self.served(), "before.json")
        self.now = AT + timedelta(minutes=30)
        wrapped = {"received_at": self.now.isoformat(), "status": 200, "sha256": "0" * 64,
                   "report": self.served()}
        after = self.save(wrapped, "after.json")
        out = io.StringIO()
        self.assertEqual(evidence.main(["restart", str(before), str(after)], stdout=out), 0)
        self.assertTrue(json.loads(out.getvalue())["durability_proven"])
        out = io.StringIO()
        self.assertEqual(evidence.main(["window", str(after), "--expected-fingerprint", self.expected],
                                       stdout=out), 1)
        self.assertFalse(json.loads(out.getvalue())["window_may_open"])
        with self.assertRaises(SystemExit):
            evidence.main(["window", str(after), "--expected-fingerprint", "not-hex"], stdout=io.StringIO())
        junk = self.save({"kind": "other"}, "junk.json")
        out = io.StringIO()
        self.assertEqual(evidence.main(["window", str(junk)], stdout=out), 2)


if __name__ == "__main__":
    unittest.main()
