"""Recette status report (schema_version 1) and its read-only endpoint.

Everything runs in ``.test-tmp-recette-report-*`` roots with the unchanged
``pilot_paper`` bootstrap: no market, model, Discord, Railway or secret.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from hermes_trading import pilot_paper
from hermes_trading.paired_packet_storage import store_record
from hermes_trading.reflection_supervision import _json
from scripts import recette_status_report as report_module

AT = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
TOP_LEVEL_KEYS = {
    "schema_version", "kind", "generated_at", "heartbeat", "experiment_id",
    "manifest_status", "session", "entry_admission", "storage",
    "growth_and_projection", "daily_summary", "locks", "recent_errors", "accounts",
    "notifications", "runtime", "volume", "bounds", "worker_recovery", "history_retention",
}
ACCOUNT_KEYS = {"arm", "wallet_eur", "currency", "starting_balance", "status",
                "net_equity", "observed_at", "error_code"}
DAILY_SUMMARY_ACCOUNT_KEYS = {"days", "decisions", "last_date", "last_balance", "last_costs"}
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

    def write_observations(self, account, stamps, balance="100", costs="0.1"):
        """Insert wallet observations so retention can build daily summaries."""
        from hermes_trading import paper_history_retention as retention

        db_path = self.pilot / account / "wallet_equity.sqlite"
        config = {
            "schema_version": 1,
            "experiment_id": "pilot-paper-recette",
            "currency": "EUR",
            "starting_balance": "100",
            "activated_at": (AT - timedelta(days=40)).isoformat(),
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
                    "net_equity": balance,
                    "known_accrued_costs": costs,
                    "known_cost_equity": str(float(balance) - float(costs)),
                }
                db.execute(
                    "INSERT INTO observations VALUES (?,?)",
                    (stamp.isoformat(), store_record(db, observation)),
                )
            db.commit()
        finally:
            db.close()
        retention.apply_retention(self.pilot, now=AT, backup_verified=True)


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
        self.assertEqual(report["notifications"], {"status": "disabled"})
        # LAT-141: outside the service nothing identifies the runtime or the volume.
        self.assertEqual(report["runtime"]["status"], "unavailable")
        self.assertEqual(report["runtime"]["reason"], "not_provided")
        self.assertEqual(report["volume"], {"status": "unknown", "marker_created_at": None,
                                            "service_started_at": None})
        self.assertEqual(report["bounds"], {})
        self.assertEqual(report["worker_recovery"], {
            "status": "not_applicable", "reason": "pilot_has_no_worker_latch",
            "proven_by": "ci_worker_replay"})
        self.assertEqual(report["history_retention"], {
            "status": "not_applicable", "reason": "retention_runs_in_paired_worker_only",
            "proven_by": "ci_worker_replay"})
        self.assertEqual(report["session"], {"status": "none"})
        self.assertEqual(report["entry_admission"],
                         {"policy": "absent", "enabled": False, "blocked": False, "reasons": []})
        storage = report["storage"]
        self.assertEqual(storage["status"], "ready")
        self.assertEqual(storage["limits_source"], "recette_default")
        for key in ("state_bytes", "free_bytes", "reserve_bytes", "max_state_bytes", "files"):
            self.assertIsInstance(storage[key], int, key)
        self.assertGreaterEqual(storage["reserve_bytes"], 128 * 1024 * 1024)
        growth = report["growth_and_projection"]
        self.assertEqual(growth["schema_version"], 1)
        self.assertEqual(growth["status"], "insufficient_data")
        self.assertIsNone(growth["bytes_per_day"])
        self.assertIsNone(growth["days_before_reserve"])
        self.assertIs(type(growth["headroom_bytes"]), int)
        daily_summary = report["daily_summary"]
        self.assertEqual(daily_summary["status"], "ready")
        self.assertEqual(daily_summary["total_decisions"], 0)
        self.assertEqual(sorted(daily_summary["accounts"]), sorted(
            f"{arm}_{wallet}" for arm in pilot_paper.ARMS for wallet in pilot_paper.WALLETS_EUR))
        for name, summary in daily_summary["accounts"].items():
            self.assertEqual(set(summary), DAILY_SUMMARY_ACCOUNT_KEYS, name)
            self.assertEqual((summary["days"], summary["decisions"]), (0, 0))
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

    def test_class_shaped_strings_are_not_codes_unless_listed(self) -> None:
        forged = "SyntheticPrivateTokenError"
        for value in (forged, f"{forged}: detail", "AbcdefPrivateException", "XyzTimeout"):
            with self.subTest(value=value):
                self.assertEqual(report_module.error_code(value), report_module.UNRECOGNIZED_CODE)
        forged_class = type(forged, (RuntimeError,), {})
        self.assertEqual(report_module.error_code(forged_class("x")), report_module.UNRECOGNIZED_CODE)
        self.assertEqual(report_module.error_code(OSError("x")), "OSError")
        self.assertEqual(report_module.error_code("ValueError: detail"), "ValueError")
        (self.pilot / pilot_paper.SESSION_REPORT).write_text(json.dumps({
            "finished_at": AT.isoformat(), "stop_reason": "error", "error": forged}))
        (self.pilot / "control_100" / "wallet_metrics.json").write_text(json.dumps({
            "status": "measurement_blocked", "observed_at": AT.isoformat(),
            "error": f"{forged}: x"}))
        report = self.build(event="error", service_errors=[
            {"at": AT.isoformat(), "source": "recette_service", "code": forged}])
        raw = report_module.encode_report(report).decode()
        self.assertNotIn(forged, raw)
        self.assertEqual(report["session"]["error_code"], report_module.UNRECOGNIZED_CODE)
        self.assertTrue(report["recent_errors"])
        for row in report["recent_errors"]:
            self.assertEqual(row["code"], report_module.UNRECOGNIZED_CODE)

    def test_free_text_fields_are_validated_not_truncated(self) -> None:
        sentinel = "SYNTHETIC_PRIVATE_SENTINEL"
        for value in (sentinel, f"2026-11-15T10:00:00+00:00 {sentinel}",
                      f"2026-11-15T10:00:00{sentinel}", "2026-11-15T10:00:00", 1731664800,
                      "x" * 41):
            with self.subTest(value=value):
                self.assertIsNone(report_module._timestamp(value))
        self.assertEqual(report_module._timestamp("2026-11-15T11:00:00+01:00"),
                         "2026-11-15T10:00:00+00:00")
        manifest_path = self.pilot / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest_path.write_text(json.dumps(dict(manifest, status=sentinel)))
        account_path = self.pilot / "control_100" / "wallet_account.json"
        account = json.loads(account_path.read_text())
        account_path.write_text(json.dumps(dict(account, currency=sentinel[:8])))
        (self.pilot / pilot_paper.SESSION_REPORT).write_text(json.dumps({
            "started_at": sentinel, "finished_at": f"2026-11-15T10:00:00+00:00{sentinel}",
            "stop_reason": "error", "error": "ValueError", "completed_cycles": -1}))
        (self.pilot / "control_100" / "wallet_metrics.json").write_text(json.dumps({
            "status": "measurement_blocked", "observed_at": sentinel, "error": "OSError"}))
        report = self.build(event="error", service={"code_version": sentinel, "pass": -3},
                            service_errors=[{"at": sentinel, "source": "recette_service",
                                             "code": "RuntimeError"}])
        raw = report_module.encode_report(report).decode()
        self.assertNotIn(sentinel, raw)
        self.assertNotIn(sentinel[:8], raw)
        self.assertEqual(report["manifest_status"], report_module.UNRECOGNIZED_CODE)
        self.assertEqual(report["experiment_id"], "pilot-paper-recette")
        self.assertIsNone(report["session"]["started_at"])
        self.assertIsNone(report["session"]["finished_at"])
        self.assertIsNone(report["session"]["completed_cycles"])
        self.assertIsNone(report["heartbeat"]["code_version"])
        self.assertIsNone(report["heartbeat"]["pass"])
        control = report["accounts"]["accounts"]["control_100"]
        self.assertIsNone(control["currency"])
        self.assertIsNone(control["observed_at"])
        self.assertEqual(len(report["recent_errors"]), 3)
        for row in report["recent_errors"]:
            self.assertIsNone(row["at"])

    def test_daily_summary_preserves_cycle_count_after_retention(self) -> None:
        yesterday = AT - timedelta(days=1)
        self.write_observations("adaptive_100", [yesterday, AT], balance="102.5", costs="0")
        report = self.build()
        daily_summary = report["daily_summary"]
        self.assertEqual(daily_summary["status"], "ready")
        self.assertEqual(daily_summary["total_decisions"], 2)
        summary = daily_summary["accounts"]["adaptive_100"]
        self.assertEqual(summary["days"], 2)
        self.assertEqual(summary["decisions"], 2)
        self.assertEqual(summary["last_date"], AT.date().isoformat())
        self.assertEqual(summary["last_balance"], "102.5")
        self.assertEqual(summary["last_costs"], "0")
        for name in daily_summary["accounts"]:
            if name != "adaptive_100":
                self.assertEqual(daily_summary["accounts"][name]["decisions"], 0)
        raw = report_module.encode_report(report).decode()
        self.assertNotIn(str(self.pilot), raw)
        self.assertNotIn(str(self.volume), raw)

    def test_malformed_experiment_id_is_not_published(self) -> None:
        manifest_path = self.pilot / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest_path.write_text(json.dumps(dict(manifest, experiment_id="Private Name/x")))
        report = self.build()
        self.assertIsNone(report["experiment_id"])
        self.assertNotIn("Private Name", report_module.encode_report(report).decode())

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


class NotificationReportTests(unittest.TestCase):
    """LAT-122: alert and cadence counters exposed in the report (fake transport)."""

    def setUp(self) -> None:
        from fakes.notifications import ManualClock, RecordingTransport

        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-report-notify-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.clock = ManualClock(AT)
        self.transport = RecordingTransport()
        self.stored = []

    def serve(self, projection, *, transport=None, cycles=3):
        import asyncio
        import io
        from unittest import mock
        from scripts import recette_service as service

        async def runner(root, *, environ, bounds, on_cycle=None, on_tick=None):
            for _ in range(cycles):
                on_cycle()
                self.stored.append(report_module.read_recette_report(self.root / report_module.REPORT_NAME))
                self.clock.advance(300)
            (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return {"stop_reason": "max_cycles_reached", "completed_cycles": cycles}

        with mock.patch.object(report_module, "_growth_and_projection", return_value=projection):
            return asyncio.run(service.serve(
                self.root, environ=self.environ, session_runner=runner, max_cycles=cycles,
                max_runtime_seconds=3600, interval_seconds=3600, reflect_every=1,
                sleep=self.clock.sleep, stream=io.StringIO(), report_clock=self.clock,
                notification_transport=self.transport if transport is None else transport))

    def test_alert_counters_and_balance_cadence_are_in_the_report(self) -> None:
        projection = {"schema_version": 1, "status": "projected", "bytes_per_day": 10,
                      "days_before_reserve": 3, "headroom_bytes": 30, "sample_days": 4}
        self.serve(projection)
        final = report_module.read_recette_report(self.root / report_module.REPORT_NAME)
        notifications = final["notifications"]
        below = notifications["alerts"]["storage_projection_below_7_days"]
        # start 12:00 (emitted), cycles 12:00/12:05/12:10 (suppressed), pass 12:15
        # (delay elapsed: emitted with 3), stop 12:15 (suppressed again, pending).
        self.assertEqual((below["active"], below["occurrences"], below["emitted"],
                          below["suppressed_total"], below["pending_suppressed"]),
                         (True, 6, 2, 4, 1))
        self.assertEqual(notifications["alerts"]["storage_projection_unavailable"]["emitted"], 0)
        self.assertEqual(notifications["balances"], {"slot_seconds": 900, "proposed": 2,
                                                     "last_proposed_at": "2026-11-16T12:15:00+00:00"})
        self.assertEqual((notifications["status"], notifications["transport"], notifications["state"]),
                         ("ready", "local_outbox", "fresh"))
        self.assertEqual(notifications["messages"], 4)
        self.assertEqual([(m["kind"], m.get("suppressed")) for m in self.transport.messages],
                         [("alert", 0), ("balances", None), ("alert", 3), ("balances", None)])
        balances = self.transport.messages[1]["accounts"]
        self.assertEqual(len(balances), 8)
        # The counters are those of the report being published, cycle by cycle.
        self.assertEqual([r["notifications"]["alerts"]["storage_projection_below_7_days"]["pending_suppressed"]
                          for r in self.stored], [1, 2, 3])
        self.assertNotIn(str(self.base), json.dumps(notifications))
        self.assertLessEqual(len(report_module.encode_report(final)), report_module.MAX_REPORT_BYTES)

    def test_quiet_projection_raises_no_alert_but_balances_continue(self) -> None:
        self.serve({"schema_version": 1, "status": "projected", "bytes_per_day": 10,
                    "days_before_reserve": 7, "headroom_bytes": 70, "sample_days": 4})
        notifications = report_module.read_recette_report(self.root / report_module.REPORT_NAME)["notifications"]
        self.assertEqual({name: row["emitted"] for name, row in notifications["alerts"].items()},
                         {"storage_projection_below_7_days": 0, "storage_projection_unavailable": 0})
        self.assertEqual(self.transport.kinds(), ["balances", "balances"])

    def test_unavailable_projection_is_its_own_alert_type(self) -> None:
        self.serve({"schema_version": 1, "status": "unavailable", "bytes_per_day": None,
                    "days_before_reserve": None, "reason": "storage_unavailable"}, cycles=1)
        alerts = report_module.read_recette_report(self.root / report_module.REPORT_NAME)["notifications"]["alerts"]
        self.assertEqual(alerts["storage_projection_unavailable"]["emitted"], 1)
        self.assertEqual(alerts["storage_projection_below_7_days"]["occurrences"], 0)

    def test_broken_notification_state_never_stops_the_report(self) -> None:
        from scripts import recette_notifications

        self.root.mkdir(parents=True)
        # Unwritable state target: a directory where the file should be.
        projection = {"schema_version": 1, "status": "insufficient_data", "bytes_per_day": None,
                      "days_before_reserve": None}
        from scripts import recette_service as service
        service.prepare_recette_volume(self.root, environ=self.environ)
        (self.root / recette_notifications.STATE_NAME).mkdir()
        summary = self.serve(projection, cycles=1)
        self.assertEqual(summary["stop_reason"], "stop_requested")
        final = report_module.read_recette_report(self.root / report_module.REPORT_NAME)
        self.assertEqual(final["notifications"], {"status": "unavailable", "reason": "ValueError"})
        self.assertEqual(final["heartbeat"]["event"], "stop")

    def test_transport_failures_are_counted_not_raised(self) -> None:
        from fakes.notifications import RecordingTransport

        self.serve({"schema_version": 1, "status": "insufficient_data", "bytes_per_day": None,
                    "days_before_reserve": None}, transport=RecordingTransport(failures=1), cycles=1)
        notifications = report_module.read_recette_report(self.root / report_module.REPORT_NAME)["notifications"]
        # start 12:00 proposes the slot (send fails), pass/stop 12:05 stay in it.
        self.assertEqual(notifications["transport_errors"], 1)
        self.assertEqual(notifications["balances"]["proposed"], 1)
        self.assertEqual(notifications["outbox"], 1)


if __name__ == "__main__":
    unittest.main()
