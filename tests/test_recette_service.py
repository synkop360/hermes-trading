"""The recette service starts only on an identified empty volume, without secrets.

No market, model, Discord or Railway call is made here: sessions are injected
test doubles and the environment checks run on plain dictionaries.
"""
from __future__ import annotations

import asyncio
import http.client
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

from scripts import recette_service as service
from scripts import recette_status_report as status_report
from hermes_trading import pilot_paper

AT = datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc)
CLEAN_REPORT = {
    "stop_reason": "max_cycles_reached", "completed_cycles": 1,
    "samples": 1, "entries": 1, "exits": 0, "public_proof": "not_eligible_injected_collector",
}


class RecetteServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}

    def prepare(self, root=None, environ=None, now=AT):
        return service.prepare_recette_volume(
            self.root if root is None else root,
            environ=self.environ if environ is None else environ, now=now)

    def test_forbidden_families_are_refused_even_with_an_empty_value(self) -> None:
        for prefix in ("EXCHANGE_", "HERMES_CLOUD_CHECKPOINT_", "HERMES_DISCORD_"):
            with self.subTest(prefix=prefix):
                self.root.mkdir(parents=True, exist_ok=True)
                environ = dict(self.environ)
                environ[prefix + "TOKEN"] = ""
                with self.assertRaises(service.RecetteError) as ctx:
                    self.prepare(environ=environ)
                self.assertIn(prefix + "TOKEN", str(ctx.exception))
                self.assertEqual(list(self.root.iterdir()), [])

    def test_refusal_names_the_variable_without_displaying_its_value(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        environ = dict(self.environ, EXCHANGE_API_SECRET="SECRET_VALUE")
        with self.assertRaises(service.RecetteError) as ctx:
            self.prepare(environ=environ)
        self.assertIn("EXCHANGE_API_SECRET", str(ctx.exception))
        self.assertNotIn("SECRET_VALUE", str(ctx.exception))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_non_empty_volume_is_refused_untouched(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / "manifest.json").write_text('{"status": "paper_ready"}')
        (self.root / "trades.jsonl").write_text("{}\n")
        with self.assertRaises(service.RecetteError):
            self.prepare()
        self.assertTrue((self.root / "trades.jsonl").is_file())
        self.assertFalse((self.root / service.MARKER_NAME).exists())

    def test_empty_root_outside_test_tmp_is_refused_without_recette_opt_in(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="recette-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with self.assertRaises(service.RecetteError):
            self.prepare(root=outside / "state", environ={})
        self.assertFalse((outside / "state").exists())

    def test_recette_opt_in_allows_a_volume_outside_test_tmp(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="recette-deploy-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        environ = dict(self.environ, HERMES_RECETTE="true")
        prepared = self.prepare(root=outside / "state", environ=environ)
        self.assertEqual(prepared["status"], "initialized")
        self.assertTrue((outside / "state" / service.PILOT_DIRNAME / "manifest.json").is_file())

    def test_paper_posture_is_enforced_before_any_volume_write(self) -> None:
        cases = (
            {"HERMES_TRADING_MODE": "live"},
            {"HERMES_TRADING_MODE": "PAPER", "HERMES_TRADING_ALLOW_REAL_ORDERS": "true"},
            {"HERMES_NOTIFY_ENABLED": "true"},
        )
        for extra in cases:
            with self.subTest(extra=extra):
                environ = dict(self.environ, **extra)
                with self.assertRaises(service.RecetteError):
                    self.prepare(environ=environ)
                self.assertFalse(self.root.exists())

    def test_init_writes_the_provenance_marker_and_the_pilot_bootstrap(self) -> None:
        prepared = self.prepare()
        self.assertEqual(prepared["status"], "initialized")
        marker = json.loads((self.root / service.MARKER_NAME).read_text(encoding="utf-8"))
        self.assertEqual(marker["schema_version"], service.MARKER_SCHEMA_VERSION)
        self.assertEqual(marker["kind"], service.RECETTE_KIND)
        self.assertEqual(marker["service"], service.SERVICE_NAME)
        self.assertEqual(marker["experiment_id"], service.RECETTE_EXPERIMENT_ID)
        self.assertEqual(marker["created_at"], AT.isoformat())
        self.assertEqual(marker["pilot_root"], service.PILOT_DIRNAME)
        self.assertIsInstance(marker["code_version"], str)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         sorted([service.MARKER_NAME, service.PILOT_DIRNAME]))
        manifest = json.loads((self.root / service.PILOT_DIRNAME / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "pilot_bootstrap")
        self.assertEqual(manifest["experiment_id"], service.RECETTE_EXPERIMENT_ID)
        self.assertEqual(len(manifest["profiles"]), 8)

    def test_restart_on_the_same_volume_is_recognized(self) -> None:
        self.prepare()
        marker_before = (self.root / service.MARKER_NAME).read_text(encoding="utf-8")
        prepared = self.prepare(now=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(prepared["status"], "recognized")
        self.assertEqual((self.root / service.MARKER_NAME).read_text(encoding="utf-8"),
                         marker_before)
        self.assertEqual(prepared["marker"]["created_at"], AT.isoformat())

    def test_existing_empty_volume_directory_is_kept_as_mount_point(self) -> None:
        self.root.mkdir(parents=True)
        inode = self.root.stat().st_ino
        (self.root / "lost+found").mkdir()
        prepared = self.prepare()
        self.assertEqual(prepared["status"], "initialized")
        self.assertEqual(self.root.stat().st_ino, inode)
        self.assertTrue((self.root / service.PILOT_DIRNAME / "manifest.json").is_file())
        self.assertEqual(self.prepare()["status"], "recognized")

    def test_non_empty_lost_and_found_is_not_tolerated(self) -> None:
        (self.root / "lost+found").mkdir(parents=True)
        (self.root / "lost+found" / "#123").write_bytes(b"x")
        with self.assertRaises(service.RecetteError):
            self.prepare()
        self.assertFalse((self.root / service.MARKER_NAME).exists())

    def test_unreadable_lost_and_found_is_not_treated_as_empty(self) -> None:
        lost = self.root / "lost+found"
        lost.mkdir(parents=True)
        (lost / "unknown").write_bytes(b"x")
        real_iterdir = Path.iterdir

        def iterdir(path):
            if Path(path) == lost:
                raise PermissionError(13, "Permission denied", str(path))
            return real_iterdir(path)

        with patch.object(Path, "iterdir", iterdir):
            self.assertFalse(service.volume_is_empty(self.root))
            with self.assertRaises(service.RecetteError):
                self.prepare()
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["lost+found"])
        self.assertFalse((self.root / service.MARKER_NAME).exists())
        self.assertFalse((self.root / service.PILOT_DIRNAME).exists())

    def test_marker_without_pilot_bootstrap_is_refused(self) -> None:
        self.prepare()
        shutil.rmtree(self.root / service.PILOT_DIRNAME)
        with self.assertRaises(service.RecetteError):
            self.prepare()

    def test_bootstrap_without_marker_is_refused_after_a_crash(self) -> None:
        self.prepare()
        (self.root / service.MARKER_NAME).unlink()
        with self.assertRaises(service.RecetteError) as ctx:
            self.prepare()
        self.assertIn("unknown origin", str(ctx.exception))

    def test_incompatible_marker_is_refused(self) -> None:
        self.prepare()
        path = self.root / service.MARKER_NAME
        marker = json.loads(path.read_text(encoding="utf-8"))
        original = dict(marker)
        for key, value in (("schema_version", service.MARKER_SCHEMA_VERSION + 1),
                           ("kind", "other"), ("service", "other"),
                           ("experiment_id", "other"), ("pilot_root", "elsewhere")):
            with self.subTest(key=key):
                path.write_text(json.dumps(dict(original, **{key: value})), encoding="utf-8")
                with self.assertRaises(service.RecetteError):
                    self.prepare()

    def test_corrupt_or_missing_marker_is_treated_as_unknown_origin(self) -> None:
        self.prepare()
        (self.root / service.MARKER_NAME).write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(service.RecetteError):
            self.prepare()
        (self.root / service.MARKER_NAME).unlink()
        with self.assertRaises(service.RecetteError):
            self.prepare()

    def test_symlinked_root_path_is_refused_before_any_write(self) -> None:
        target = self.base / "elsewhere"
        target.mkdir()
        alias = self.base / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(service.RecetteError):
            self.prepare(root=alias / "state")
        self.assertEqual(list(target.iterdir()), [])

    def test_bounds_come_from_the_environment_and_stay_within_pilot_limits(self) -> None:
        environ = dict(self.environ, HERMES_RECETTE_MAX_CYCLES="2",
                       HERMES_RECETTE_MAX_RUNTIME_SECONDS="7200",
                       HERMES_RECETTE_INTERVAL_SECONDS="1800",
                       HERMES_RECETTE_REFLECT_EVERY="2")
        self.assertEqual(service.resolve_bounds(environ), {
            "max_cycles": 2, "max_runtime_seconds": 7200.0,
            "interval_seconds": 1800.0, "reflect_every": 2})
        for name, raw in ((service.ENV_MAX_CYCLES, "x"),
                          (service.ENV_MAX_RUNTIME_SECONDS, "x"),
                          (service.ENV_MAX_CYCLES, str(pilot_paper.MAX_SESSION_CYCLES + 1)),
                          (service.ENV_INTERVAL_SECONDS, "0")):
            with self.subTest(name=name, raw=raw):
                with self.assertRaises(service.RecetteError):
                    service.resolve_bounds(dict(self.environ, **{name: raw}))


class RecetteServeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-serve-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.calls = []
        self.sleeps = []

    async def fake_sleep(self, seconds):
        self.sleeps.append(seconds)

    def runner(self, reports=None, error=None):
        reports = reports if reports is not None else [dict(CLEAN_REPORT)]

        async def fake(root, *, environ, bounds, on_cycle=None):
            self.calls.append({"root": root, "environ": dict(environ),
                               "bounds": dict(bounds)})
            if error is not None:
                raise error
            index = min(len(self.calls) - 1, len(reports) - 1)
            report = dict(reports[index])
            if len(self.calls) >= len(reports):
                (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return report

        return fake

    def serve(self, runner, **kwargs):
        output = io.StringIO()
        options = dict(environ=self.environ, max_cycles=2, max_runtime_seconds=3600,
                       interval_seconds=60, reflect_every=1, stream=output,
                       sleep=self.fake_sleep)
        options.update(kwargs)
        summary = asyncio.run(service.serve(self.root, session_runner=runner, **options))
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        return summary, records

    def test_invalid_bounds_are_refused_before_any_write(self) -> None:
        self.root.mkdir(parents=True)
        cases = ({"max_cycles": 0}, {"max_runtime_seconds": 0},
                 {"interval_seconds": 0}, {"reflect_every": -1})
        for bad in cases:
            with self.subTest(**bad):
                with self.assertRaises(service.RecetteError):
                    self.serve(self.runner(), **bad)
                self.assertEqual(list(self.root.iterdir()), [])
        environ = dict(self.environ, **{service.ENV_MAX_CYCLES: "0"})
        with self.assertRaises(service.RecetteError):
            asyncio.run(service.serve(self.root, environ=environ,
                                      session_runner=self.runner(),
                                      stream=io.StringIO(), sleep=self.fake_sleep))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_local_start_initializes_an_empty_root_and_serves_bounded_passes(self) -> None:
        summary, records = self.serve(self.runner([dict(CLEAN_REPORT), dict(CLEAN_REPORT)]))
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual(len(self.calls), 2)
        first, second = self.calls
        self.assertEqual(first["root"], second["root"])
        self.assertEqual(first["bounds"]["max_cycles"], 2)
        self.assertEqual(first["environ"].get("HERMES_TRADING_MODE"), "paper")
        self.assertEqual([r["kind"] for r in records],
                         ["recette_service_start", "recette_pass", "recette_pass",
                          "recette_service_stop"])
        self.assertEqual(records[1]["stop_reason"], "max_cycles_reached")
        self.assertEqual(Path(first["root"]), self.root.resolve() / service.PILOT_DIRNAME)
        self.assertEqual(self.sleeps, [60])
        self.assertTrue((self.root / service.MARKER_NAME).is_file())
        self.assertTrue((self.root / service.PILOT_DIRNAME / "manifest.json").is_file())

    def test_serving_twice_resumes_the_identified_volume(self) -> None:
        _, first = self.serve(self.runner())
        (self.root / service.PILOT_DIRNAME / pilot_paper.STOP_FILE).unlink()
        calls_after_first = len(self.calls)
        _, second = self.serve(self.runner())
        self.assertEqual(len(self.calls), calls_after_first + 1)
        self.assertEqual(first[0]["volume"], "initialized")
        self.assertEqual(second[0]["volume"], "recognized")
        self.assertTrue((self.root / service.MARKER_NAME).is_file())
        self.assertTrue((self.root / service.PILOT_DIRNAME / "manifest.json").is_file())

    def test_stop_file_ends_the_service_without_a_session(self) -> None:
        service.prepare_recette_volume(self.root, environ=self.environ, now=AT)
        (self.root / service.PILOT_DIRNAME / pilot_paper.STOP_FILE).write_text("stop")
        summary, records = self.serve(self.runner())
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual(self.calls, [])
        self.assertEqual(records[0]["volume"], "recognized")
        self.assertEqual([r["kind"] for r in records],
                         ["recette_service_start", "recette_service_stop"])

    def test_session_outside_clean_bounds_is_refused(self) -> None:
        for reason in ("collection_unavailable", "error", "engine_halted"):
            with self.subTest(reason=reason):
                self.root = self.base / f"volume-{reason}"
                self.calls.clear()
                report = dict(CLEAN_REPORT, stop_reason=reason)
                with self.assertRaises(service.RecetteError):
                    self.serve(self.runner([report]))
                self.assertEqual(len(self.calls), 1)

    def test_forbidden_variables_are_refused_before_any_session(self) -> None:
        runner = self.runner()
        with self.assertRaises(service.RecetteError):
            self.serve(runner, environ=dict(self.environ, HERMES_DISCORD_WEBHOOK_URL=""))
        self.assertEqual(self.calls, [])
        self.assertFalse(self.root.exists())

    def test_recette_opt_in_serves_a_volume_outside_test_tmp(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="recette-deploy-serve-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.root = outside / "state"
        summary, _ = self.serve(self.runner(),
                                environ=dict(self.environ, HERMES_RECETTE="true"))
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual(Path(self.calls[0]["root"]),
                         self.root.resolve() / service.PILOT_DIRNAME)


class RecetteRealSessionTests(unittest.TestCase):
    """Local start on an empty root with the real bounded pilot session.

    The market is the invented ``PriceDouble`` of ``test_pilot_paper`` and the
    clock is logical: no network, no model, no credential.
    """

    def setUp(self) -> None:
        from test_pilot_paper import FakeClock, PriceDouble

        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-real-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        # The volume is initialized at the real time; logical time starts after it.
        self.clock = FakeClock(datetime.now(timezone.utc).replace(microsecond=0)
                               + timedelta(hours=1))
        self.double = PriceDouble(self.clock, ["100", "80", "80", "80"])
        self.reports = []

    def runner(self, stop_after):
        async def run(root, *, environ, bounds, on_cycle=None):
            report = await pilot_paper.run_pilot_session(
                root, collector=self.double, clock=self.clock,
                sleep=service.cycle_sleep(self.clock.sleep, on_cycle),
                environ=environ, collector_options={"retry_delay_seconds": 0},
                **bounds)
            self.reports.append(report)
            if len(self.reports) >= stop_after:
                (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return report

        return run

    def serve(self, stop_after):
        output = io.StringIO()
        summary = asyncio.run(service.serve(
            self.root, environ=self.environ, session_runner=self.runner(stop_after),
            max_cycles=1, max_runtime_seconds=7200, interval_seconds=3600,
            reflect_every=1, sleep=self.clock.sleep, stream=output))
        return summary, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_local_start_on_empty_root_then_restart_is_recognized(self) -> None:
        summary, records = self.serve(stop_after=2)
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual(records[0]["volume"], "initialized")
        self.assertEqual([r["stop_reason"] for r in self.reports],
                         ["max_cycles_reached", "max_cycles_reached"])
        manifest = json.loads((self.root / service.PILOT_DIRNAME / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "paper_ready")
        self.assertEqual(manifest["experiment_id"], service.RECETTE_EXPERIMENT_ID)
        for report in self.reports:
            self.assertEqual(report["reflection_writer"], pilot_paper.FALLBACK_WRITER)
            self.assertEqual([c["reflection"]["status"] for c in report["cycles"]],
                             ["published"])
            self.assertEqual(report["public_proof"], "not_eligible_injected_collector")
        (self.root / service.PILOT_DIRNAME / pilot_paper.STOP_FILE).unlink()
        summary, records = self.serve(stop_after=3)
        self.assertEqual(records[0]["volume"], "recognized")
        self.assertEqual(len(self.reports), 3)
        self.assertEqual(self.double.closed, 3)

    def test_deployed_volume_outside_test_tmp_runs_the_unchanged_pilot(self) -> None:
        # Like /app/state: no .test-tmp* ancestor, HERMES_RECETTE=true opt-in.
        outside = Path(tempfile.mkdtemp(prefix="recette-deployed-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.root = outside / "state"
        self.assertFalse(any(p.startswith(".test-tmp") for p in self.root.resolve().parts))
        self.environ = dict(self.environ, HERMES_RECETTE="true")
        summary, records = self.serve(stop_after=1)
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual(records[0]["volume"], "initialized")
        self.assertEqual(self.reports[0]["stop_reason"], "max_cycles_reached")
        self.assertTrue((self.root / service.PILOT_DIRNAME / "manifest.json").is_file())


class RecetteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-cli-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.calls = []

    def runner(self):
        async def fake(root, *, environ, bounds, on_cycle=None):
            self.calls.append(root)
            (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return dict(CLEAN_REPORT)

        return fake

    def main(self, argv, runner, environ=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            code = service.main(argv, environ=self.environ if environ is None else environ,
                                session_runner=runner)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_serves_bounded_passes_until_the_stop_file(self) -> None:
        code, out, err = self.main(
            ["--root", str(self.root), "--max-cycles", "1",
             "--max-runtime-seconds", "600", "--interval-seconds", "60"],
            self.runner())
        self.assertEqual(code, 0, err)
        self.assertIn('"stop_reason": "stop_requested"', out)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            json.loads((self.root / service.MARKER_NAME).read_text(encoding="utf-8"))["kind"],
            service.RECETTE_KIND)
        self.assertEqual(Path(self.calls[0]), self.root.resolve() / service.PILOT_DIRNAME)

    def test_cli_refuses_an_unknown_volume_with_exit_code_2(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / "unknown-leftover.bin").write_bytes(b"x")
        code, out, err = self.main(
            ["--root", str(self.root), "--max-cycles", "1",
             "--max-runtime-seconds", "600", "--interval-seconds", "60"],
            self.runner())
        self.assertEqual(code, 2)
        self.assertIn("unknown origin", err)
        self.assertEqual(self.calls, [])
        self.assertNotIn("unknown-leftover.bin", err)

    def test_cli_refuses_a_forbidden_variable_with_exit_code_2(self) -> None:
        code, out, err = self.main(
            ["--root", str(self.root), "--max-cycles", "1",
             "--max-runtime-seconds", "600", "--interval-seconds", "60"],
            self.runner(), environ=dict(self.environ, EXCHANGE_API_KEY=""))
        self.assertEqual(code, 2)
        self.assertIn("EXCHANGE_API_KEY", err)
        self.assertEqual(self.calls, [])



class RecetteReportServiceTests(unittest.TestCase):
    """The service publishes the schema_version 1 report and serves it read-only."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-status-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.seen = []

    async def fake_sleep(self, seconds):
        self.seen.append(("sleep", self.stored()["heartbeat"]["event"]))

    def stored(self):
        return status_report.read_recette_report(self.root / status_report.REPORT_NAME)

    def serve(self, runner, **kwargs):
        options = dict(environ=self.environ, max_cycles=3, max_runtime_seconds=3600,
                       interval_seconds=60, reflect_every=1, stream=io.StringIO(),
                       sleep=self.fake_sleep)
        options.update(kwargs)
        return asyncio.run(service.serve(self.root, session_runner=runner, **options))

    def test_report_is_written_at_start_after_each_cycle_pass_and_stop(self) -> None:
        passes = []

        async def runner(root, *, environ, bounds, on_cycle=None):
            passes.append(root)
            self.seen.append(("pass_start", self.stored()["heartbeat"]["event"]))
            for _ in range(2):
                on_cycle()
                heartbeat = self.stored()["heartbeat"]
                self.seen.append(("cycle", heartbeat["event"], heartbeat["pass"], heartbeat["cycle"]))
            if len(passes) == 2:
                (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return dict(CLEAN_REPORT, completed_cycles=3)

        summary = self.serve(runner)
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual(self.seen, [
            ("pass_start", "start"), ("cycle", "cycle", 1, 1), ("cycle", "cycle", 1, 2),
            ("sleep", "pass"),
            ("pass_start", "pass"), ("cycle", "cycle", 2, 1), ("cycle", "cycle", 2, 2)])
        report = self.stored()
        self.assertEqual(report["heartbeat"]["event"], "stop")
        self.assertEqual((report["heartbeat"]["pass"], report["heartbeat"]["cycle"]), (2, 3))
        self.assertEqual(report["heartbeat"]["interval_seconds"], 60.0)
        self.assertEqual(report["accounts"]["count"], 8)
        self.assertEqual(report["locks"]["active"], ["stop_requested"])
        self.assertEqual(report["experiment_id"], service.RECETTE_EXPERIMENT_ID)

    def test_session_outside_bounds_publishes_an_error_report(self) -> None:
        async def runner(root, *, environ, bounds, on_cycle=None):
            return dict(CLEAN_REPORT, stop_reason="collection_unavailable")

        with self.assertRaises(service.RecetteError):
            self.serve(runner)
        report = self.stored()
        self.assertEqual(report["heartbeat"]["event"], "error")
        self.assertEqual([(e["source"], e["code"]) for e in report["recent_errors"]],
                         [("recette_service", "collection_unavailable")])

    def test_runner_failure_is_published_as_a_code_without_its_message(self) -> None:
        async def runner(root, *, environ, bounds, on_cycle=None):
            raise pilot_paper.PilotSessionError("token=SECRET_VALUE in " + str(self.base))

        with self.assertRaises(pilot_paper.PilotSessionError):
            self.serve(runner)
        raw = (self.root / status_report.REPORT_NAME).read_text(encoding="utf-8")
        self.assertNotIn("SECRET_VALUE", raw)
        self.assertNotIn(str(self.base), raw)
        self.assertEqual(self.stored()["recent_errors"][0]["code"], "PilotSessionError")

    def test_report_failure_never_stops_the_session(self) -> None:
        async def runner(root, *, environ, bounds, on_cycle=None):
            on_cycle()
            (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return dict(CLEAN_REPORT)

        output = io.StringIO()
        with patch.object(service.status_report, "write_recette_report",
                          side_effect=OSError("disk SECRET_VALUE")):
            summary = self.serve(runner, stream=output)
        self.assertEqual(summary["stop_reason"], "stop_requested")
        errors = [json.loads(line) for line in output.getvalue().splitlines()
                  if '"recette_report_error"' in line]
        self.assertEqual([e["event"] for e in errors], ["start", "cycle", "pass", "stop"])
        self.assertNotIn("SECRET_VALUE", output.getvalue())

    def test_endpoint_serves_the_report_during_the_service_only(self) -> None:
        servers, responses = [], []

        def get(path, method="GET"):
            connection = http.client.HTTPConnection("127.0.0.1", servers[0].server_address[1], timeout=5)
            try:
                connection.request(method, path)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        async def runner(root, *, environ, bounds, on_cycle=None):
            responses.append(get("/recette"))
            responses.append(get("/recette", "POST"))
            responses.append(get("/recette_provenance.json"))
            (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return dict(CLEAN_REPORT)

        output = io.StringIO()
        self.serve(runner, http_port=0, http_host="127.0.0.1", on_server=servers.append,
                   stream=output)
        (status, body), (post, _), (other, _) = responses
        self.assertEqual((status, post, other), (200, 405, 404))
        payload = json.loads(body)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["heartbeat"]["event"], "start")
        self.assertFalse(payload["freshness"]["stale"])
        self.assertIn('"kind": "recette_report_endpoint"', output.getvalue())
        with self.assertRaises(OSError):
            get("/recette")

    def test_endpoint_port_comes_from_port_and_is_validated_before_any_write(self) -> None:
        self.assertEqual(service.resolve_http_port({"PORT": "8080"}), 8080)
        self.assertIsNone(service.resolve_http_port({}))
        self.assertEqual(service.resolve_http_port({"PORT": "8080"}, 9000), 9000)
        for bad in ("http", "70000", "-1"):
            with self.subTest(port=bad):
                with self.assertRaises(service.RecetteError):
                    self.serve(None, environ=dict(self.environ, PORT=bad))
                self.assertFalse(self.root.exists())


class RecetteReportRealSessionTests(unittest.TestCase):
    """A real two-cycle pilot session reports between its cycles."""

    def setUp(self) -> None:
        from test_pilot_paper import FakeClock, PriceDouble

        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-status-real-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "volume"
        self.environ = {"HERMES_TRADING_MODE": "paper",
                        "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
                        "HERMES_NOTIFY_ENABLED": "false"}
        self.clock = FakeClock(datetime.now(timezone.utc).replace(microsecond=0)
                               + timedelta(hours=1))
        self.double = PriceDouble(self.clock, ["100", "80", "80", "80"])
        self.between = []

    def test_real_session_publishes_a_report_between_cycles(self) -> None:
        async def sleep(seconds):
            self.between.append(status_report.read_recette_report(
                self.root / status_report.REPORT_NAME))
            await self.clock.sleep(seconds)

        async def run(root, *, environ, bounds, on_cycle=None):
            report = await pilot_paper.run_pilot_session(
                root, collector=self.double, clock=self.clock,
                sleep=service.cycle_sleep(sleep, on_cycle),
                environ=environ, collector_options={"retry_delay_seconds": 0}, **bounds)
            (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
            return report

        summary = asyncio.run(service.serve(
            self.root, environ=self.environ, session_runner=run, max_cycles=2,
            max_runtime_seconds=3 * 3600, interval_seconds=3600, reflect_every=1,
            sleep=self.clock.sleep, stream=io.StringIO(), report_clock=self.clock))
        self.assertEqual(summary["stop_reason"], "stop_requested")
        self.assertEqual([r["heartbeat"]["event"] for r in self.between], ["cycle"])
        cycle = self.between[0]
        self.assertEqual(cycle["manifest_status"], "paper_ready")
        observed = [a for a in cycle["accounts"]["accounts"].values() if a["net_equity"] is not None]
        self.assertEqual(len(observed), 8)
        final = status_report.read_recette_report(self.root / status_report.REPORT_NAME)
        self.assertEqual(final["heartbeat"]["event"], "stop")
        self.assertEqual(final["session"]["completed_cycles"], 2)
        self.assertEqual(final["session"]["stop_reason"], "max_cycles_reached")
        self.assertEqual(final["storage"]["status"], "ready")
        self.assertEqual(final["recent_errors"], [])

if __name__ == "__main__":
    unittest.main()
