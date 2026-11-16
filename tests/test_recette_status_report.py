"""Recette status report (schema_version 1) and its read-only endpoint.

Everything runs in ``.test-tmp-recette-report-*`` roots with the unchanged
``pilot_paper`` bootstrap: no market, model, Discord, Railway or secret.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from hermes_trading import pilot_paper
from scripts import recette_status_report as report_module

AT = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
TOP_LEVEL_KEYS = {
    "schema_version", "kind", "generated_at", "heartbeat", "experiment_id",
    "manifest_status", "session", "entry_admission", "storage",
    "growth_and_projection", "daily_summary", "locks", "recent_errors", "accounts",
}
ACCOUNT_KEYS = {"arm", "wallet_eur", "currency", "starting_balance", "status",
                "net_equity", "observed_at", "error_code"}
SECRET = "SECRET_VALUE_never_published"


class ReportFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-report-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.volume = self.base / "volume"
        self.volume.mkdir()
        self.pilot = self.volume / ".test-tmp-pilot"
        pilot_paper.init_pilot_accounts(self.pilot, experiment_id="pilot-paper-recette", now=AT)

    def build(self, event="cycle", now=AT, **kwargs):
        kwargs.setdefault("interval_seconds", 3600)
        return report_module.build_recette_report(self.volume, self.pilot, event=event,
                                                  now=now, **kwargs)


class ReportContentTests(ReportFixture):
    def test_schema_is_stable_and_carries_every_required_field(self) -> None:
        report = self.build(service={"pass": 2, "cycle": 5, "code_version": "0.1.0"})
        self.assertEqual(set(report), TOP_LEVEL_KEYS)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["kind"], "hermes_recette_status")
        self.assertEqual(report["generated_at"], AT.isoformat())
        self.assertEqual(report["heartbeat"], {
            "at": AT.isoformat(), "event": "cycle", "interval_seconds": 3600.0,
            "stale_after": (AT + timedelta(seconds=3600 + 900)).isoformat(),
            "pass": 2, "cycle": 5, "code_version": "0.1.0"})
        self.assertEqual(report["experiment_id"], "pilot-paper-recette")
        self.assertEqual(report["session"], {"status": "none"})
        self.assertEqual(report["entry_admission"],
                         {"policy": "absent", "enabled": False, "blocked": False, "reasons": []})
        storage = report["storage"]
        self.assertEqual(storage["status"], "ready")
        self.assertEqual(storage["limits_source"], "recette_default")
        for key in ("state_bytes", "free_bytes", "reserve_bytes", "max_state_bytes", "files"):
            self.assertIsInstance(storage[key], int, key)
        self.assertGreaterEqual(storage["reserve_bytes"], 128 * 1024 * 1024)
        self.assertEqual(report["growth_and_projection"],
                         {"status": "unavailable", "reason": "pending_lat_120_daily_summary"})
        self.assertEqual(report["daily_summary"]["status"], "unavailable")
        self.assertEqual(report["locks"]["active"], [])
        self.assertEqual(report["recent_errors"], [])
        accounts = report["accounts"]
        self.assertEqual((accounts["count"], accounts["expected"]), (8, 8))
        self.assertEqual(sorted(accounts["accounts"]), sorted(
            f"{arm}_{wallet}" for arm in pilot_paper.ARMS for wallet in pilot_paper.WALLETS_EUR))
        for name, account in accounts["accounts"].items():
            self.assertEqual(set(account), ACCOUNT_KEYS, name)
            self.assertEqual(account["currency"], "EUR")
            self.assertEqual(account["starting_balance"], str(account["wallet_eur"]))
            self.assertEqual(account["status"], "not_observed")
        self.assertLess(len(report_module.encode_report(report)), report_module.MAX_REPORT_BYTES)

    def test_observed_balances_and_finished_session_are_reported(self) -> None:
        metrics = {"schema_version": 1, "status": "observed", "observed_at": AT.isoformat(),
                   "observation": {"net_equity": "101.25", "at": AT.isoformat()}}
        (self.pilot / "control_100" / "wallet_metrics.json").write_text(json.dumps(metrics))
        (self.pilot / pilot_paper.SESSION_REPORT).write_text(json.dumps({
            "started_at": AT.isoformat(), "finished_at": AT.isoformat(),
            "stop_reason": "max_cycles_reached", "error": None, "completed_cycles": 24,
            "samples": 24, "entries": 3, "exits": 2, "public_proof": "candidate_requires_review"}))
        report = self.build()
        account = report["accounts"]["accounts"]["control_100"]
        self.assertEqual((account["status"], account["net_equity"]), ("observed", "101.25"))
        self.assertEqual(report["session"]["completed_cycles"], 24)
        self.assertEqual(report["session"]["stop_reason"], "max_cycles_reached")
        self.assertIsNone(report["session"]["error_code"])

    def test_errors_are_reduced_to_codes_and_no_private_data_is_published(self) -> None:
        (self.pilot / pilot_paper.SESSION_REPORT).write_text(json.dumps({
            "finished_at": AT.isoformat(), "stop_reason": "error",
            "error": f"PilotSessionError: token={SECRET} at {self.base}"}))
        (self.pilot / "adaptive_50" / "wallet_metrics.json").write_text(json.dumps({
            "status": "measurement_blocked", "observed_at": AT.isoformat(),
            "error": f"ValueError: {SECRET}"}))
        report = self.build(event="error", service_errors=[
            {"at": AT.isoformat(), "source": "recette_service", "code": RuntimeError(SECRET)}])
        raw = report_module.encode_report(report).decode()
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(str(self.base), raw)
        self.assertNotIn(str(ROOT), raw)
        self.assertEqual(sorted((row["source"], row["code"]) for row in report["recent_errors"]), [
            ("account_adaptive_50", "ValueError"), ("pilot_session", "PilotSessionError"),
            ("recette_service", "RuntimeError")])

    def test_free_form_messages_without_colon_are_not_published(self) -> None:
        sentinel = "FAKE_SENTINEL_never_published"
        for value in (f"request failed token={sentinel}", sentinel, sentinel.lower(),
                      f"{sentinel}Error", f"token={sentinel}", "a" * 200, 42, ["x"]):
            with self.subTest(value=value):
                self.assertEqual(report_module.error_code(value), report_module.UNRECOGNIZED_CODE)
        self.assertEqual(report_module.error_code("PilotSessionError: " + sentinel),
                         "PilotSessionError")
        self.assertEqual(report_module.error_code("engine_halted"), "engine_halted")
        self.assertEqual(report_module.error_code("measurement_unavailable:OSError"),
                         "measurement_unavailable")
        (self.pilot / pilot_paper.SESSION_REPORT).write_text(json.dumps({
            "finished_at": AT.isoformat(), "stop_reason": f"halted {sentinel}",
            "error": f"request failed token={sentinel}", "public_proof": sentinel}))
        (self.pilot / "control_100" / "wallet_metrics.json").write_text(json.dumps({
            "status": sentinel, "observed_at": AT.isoformat(), "error": sentinel}))
        report = self.build(event="error", service_errors=[
            {"at": AT.isoformat(), "source": sentinel, "code": f"oops {sentinel}"}])
        raw = report_module.encode_report(report).decode()
        self.assertNotIn(sentinel, raw)
        self.assertNotIn(sentinel.lower(), raw)
        self.assertEqual(report["session"]["error_code"], report_module.UNRECOGNIZED_CODE)
        self.assertEqual(report["accounts"]["accounts"]["control_100"]["status"],
                         report_module.UNRECOGNIZED_CODE)

    def test_recent_errors_are_bounded(self) -> None:
        errors = [{"at": (AT + timedelta(minutes=i)).isoformat(), "source": "recette_service",
                   "code": "engine_halted"} for i in range(30)]
        report = self.build(service_errors=errors)
        self.assertEqual(len(report["recent_errors"]), report_module.MAX_RECENT_ERRORS)
        self.assertEqual(report["recent_errors"][0]["at"], errors[-1]["at"])

    def test_locks_are_reported(self) -> None:
        (self.pilot / pilot_paper.STOP_FILE).write_text("stop")
        (self.pilot / "paper_supervisor_recovery_pending.json").write_text("{}")
        (self.pilot / pilot_paper.SESSION_REPORT).write_text(json.dumps({"stop_reason": "engine_halted"}))
        locks = self.build()["locks"]
        self.assertEqual(locks["active"], ["engine_halted", "stop_requested",
                                           "supervisor_recovery_pending"])
        self.assertFalse(locks["worker_entry_halt"])

    def test_unmeasurable_storage_is_blocked_not_ready(self) -> None:
        limits = {"schema_version": 1, "experiment_id": "pilot-paper-recette",
                  "max_state_bytes": 1, "reserve_bytes": 1}
        storage = self.build(storage_limits=limits)["storage"]
        self.assertEqual((storage["status"], storage["limits_source"]), ("blocked", "configured"))
        self.assertEqual(storage["reason"], "limits_invalid_ValueError")

    def test_state_budget_pressure_blocks_storage(self) -> None:
        reserve = 128 * 1024 * 1024
        limits = {"schema_version": 1, "experiment_id": "pilot-paper-recette",
                  "max_state_bytes": reserve + 1, "reserve_bytes": reserve}
        storage = self.build(storage_limits=limits)["storage"]
        self.assertEqual((storage["status"], storage["reason"]), ("blocked", "state_budget"))

    def test_unknown_event_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(event="anything")


class ReportWriteTests(ReportFixture):
    def test_write_is_atomic_and_leaves_no_temporary_file(self) -> None:
        target = report_module.write_recette_report(self.volume, self.build())
        self.assertEqual(target, self.volume / "recette_status.json")
        stored = report_module.read_recette_report(target)
        self.assertEqual(stored["generated_at"], AT.isoformat())
        later = AT + timedelta(hours=1)
        report_module.write_recette_report(self.volume, self.build(now=later))
        self.assertEqual(report_module.read_recette_report(target)["generated_at"], later.isoformat())
        self.assertEqual([p.name for p in self.volume.iterdir() if p.name.endswith(".tmp")], [])

    def test_oversized_report_keeps_the_previous_one(self) -> None:
        target = report_module.write_recette_report(self.volume, self.build())
        before = target.read_bytes()
        oversized = dict(self.build(), padding="x" * report_module.MAX_REPORT_BYTES)
        with self.assertRaises(report_module.RecetteReportError):
            report_module.write_recette_report(self.volume, oversized)
        self.assertEqual(target.read_bytes(), before)

    def test_linked_target_is_refused(self) -> None:
        outside = self.base / "outside.json"
        outside.write_text("{}")
        os.symlink(outside, self.volume / "recette_status.json")
        with self.assertRaises(report_module.RecetteReportError):
            report_module.write_recette_report(self.volume, self.build())
        self.assertEqual(outside.read_text(), "{}")
        self.assertIsNone(report_module.read_recette_report(self.volume / "recette_status.json"))

    def test_freshness_identifies_stale_reports(self) -> None:
        report = self.build()
        fresh = report_module.freshness(report, now=AT + timedelta(seconds=4500))
        self.assertEqual((fresh["age_seconds"], fresh["stale"]), (4500.0, False))
        self.assertTrue(report_module.freshness(report, now=AT + timedelta(seconds=4501))["stale"])
        self.assertTrue(report_module.freshness(report, now=AT - timedelta(seconds=1))["stale"])
        self.assertTrue(report_module.freshness({"generated_at": "bad"}, now=AT)["stale"])


class EndpointTests(ReportFixture):
    def setUp(self) -> None:
        super().setUp()
        self.now = AT + timedelta(minutes=10)
        self.target = self.volume / report_module.REPORT_NAME
        self.server = report_module.start_report_server(
            self.target, host="127.0.0.1", port=0, clock=lambda: self.now)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_get_serves_the_report_with_its_freshness(self) -> None:
        report_module.write_recette_report(self.volume, self.build())
        status, headers, body = self.request("GET", "/recette")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Cache-Control"], "no-store")
        payload = json.loads(body)
        self.assertEqual(set(payload), TOP_LEVEL_KEYS | {"freshness"})
        self.assertEqual(payload["freshness"], {"served_at": self.now.isoformat(),
                                                "age_seconds": 600.0, "stale": False})
        self.now = AT + timedelta(hours=2)
        self.assertTrue(json.loads(self.request("GET", "/recette")[2])["freshness"]["stale"])
        status, headers, body = self.request("HEAD", "/recette")
        self.assertEqual((status, body), (200, b""))

    def test_missing_or_corrupt_report_is_unavailable_and_stale(self) -> None:
        status, _, body = self.request("GET", "/recette")
        self.assertEqual(status, 503)
        self.assertTrue(json.loads(body)["freshness"]["stale"])
        self.target.write_text("{not json")
        self.assertEqual(self.request("GET", "/recette")[0], 503)
        self.target.write_text(json.dumps({"schema_version": 2}))
        self.assertEqual(self.request("GET", "/recette")[0], 503)

    def test_no_arbitrary_path_is_served(self) -> None:
        report_module.write_recette_report(self.volume, self.build())
        (self.volume / "recette_provenance.json").write_text(json.dumps({"secret": SECRET}))
        for path in ("/", "/etc/passwd", "/recette/", "/recette/../recette_provenance.json",
                     "/recette_provenance.json", "/recette?path=/etc/passwd",
                     "/.test-tmp-pilot/manifest.json", "/recette%2f..", "/RECETTE"):
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(SECRET.encode(), body)

    def test_fifo_report_or_source_is_refused_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("no FIFO on this platform")
        os.mkfifo(self.target)
        os.unlink(self.pilot / "manifest.json")
        os.mkfifo(self.pilot / "manifest.json")
        # A subprocess bounds the check: a blocking open would hang it, not the suite.
        code = ("import sys, json; from pathlib import Path; "
                "from scripts import recette_status_report as m; "
                "from datetime import datetime, timezone; "
                "assert m.read_recette_report(sys.argv[1]) is None; "
                "r = m.build_recette_report(Path(sys.argv[2]), Path(sys.argv[3]), event='cycle', "
                "now=datetime(2026, 11, 16, 12, tzinfo=timezone.utc), interval_seconds=3600); "
                "assert r['manifest_status'] == 'missing', r['manifest_status']; print('ok')")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
        result = subprocess.run([sys.executable, "-c", code, str(self.target), str(self.volume),
                                 str(self.pilot)], cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")
        status, _, body = self.request("GET", "/recette")
        self.assertEqual(status, 503)
        self.assertTrue(json.loads(body)["freshness"]["stale"])

    def test_link_swapped_report_is_not_followed(self) -> None:
        outside = self.base / "outside.json"
        outside.write_text(json.dumps(self.build()))
        os.symlink(outside, self.target)
        self.assertEqual(self.request("GET", "/recette")[0], 503)

    def test_writes_are_refused_and_change_nothing(self) -> None:
        report_module.write_recette_report(self.volume, self.build())
        before = self.target.read_bytes()
        entries = sorted(p.name for p in self.volume.iterdir())
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                status, headers, _ = self.request(method, "/recette", body=b'{"schema_version":1}')
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.volume.iterdir()), entries)


if __name__ == "__main__":
    unittest.main()
