"""Local alert demonstration and its controlled storage limits (LAT-141, delivery 2).

Everything runs in ``.test-tmp-recette-demo-*`` roots with a simulated clock:
no volume is filled, no network, no Discord, no secret. The demonstration is
checked to be labelled as local, never as a recette observation.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from fakes.notifications import RecordingTransport
from hermes_trading import pilot_paper
from scripts import recette_alert_demo as demo
from scripts import recette_evidence as evidence
from scripts import recette_fingerprint as fingerprint
from scripts import recette_notifications as notifications
from scripts import recette_service as service
from scripts import recette_status_report as status_report
from scripts import recette_storage_limits as limits_module

MIB = 1024 * 1024
AT = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
VALID = {"schema_version": 1, "experiment_id": "pilot-paper-recette",
         "max_state_bytes": 256 * MIB, "reserve_bytes": 128 * MIB}
PAPER = {"HERMES_TRADING_MODE": "paper", "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
         "HERMES_NOTIFY_ENABLED": "false"}


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix=".test-tmp-recette-demo-", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def limits_file(self, content, name="limits.json") -> Path:
        path = self.base / name
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path


class StorageLimitsTests(Fixture):
    def test_valid_limits_are_accepted_and_normalized(self) -> None:
        self.assertEqual(limits_module.load_demo_limits(self.limits_file(VALID)), VALID)
        self.assertEqual(limits_module.validate_demo_limits(limits_module.DEFAULT_DEMO_LIMITS), VALID)

    def test_invalid_limits_are_refused(self) -> None:
        cases = {
            "missing_key": {k: v for k, v in VALID.items() if k != "reserve_bytes"},
            "extra_key": dict(VALID, protective_stop_on_pressure=True),
            "schema": dict(VALID, schema_version=2),
            "schema_bool": dict(VALID, schema_version=True),
            "experiment": dict(VALID, experiment_id="other"),
            "float_budget": dict(VALID, max_state_bytes=268435456.0),
            "string_budget": dict(VALID, max_state_bytes="268435456"),
            "bool_reserve": dict(VALID, reserve_bytes=True),
            "negative": dict(VALID, max_state_bytes=-1),
            "zero": dict(VALID, max_state_bytes=0),
            "reserve_under_two_packets": dict(VALID, reserve_bytes=128 * MIB - 1),
            "reserve_equals_budget": dict(VALID, max_state_bytes=128 * MIB),
            "reserve_above_budget": dict(VALID, reserve_bytes=300 * MIB),
            "budget_above_bound": dict(VALID, max_state_bytes=1024 ** 3 + 1),
            "not_object": [VALID],
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(limits_module.StorageLimitsError):
                    limits_module.load_demo_limits(self.limits_file(value, name=f"{name}.json"))
        raw_cases = {
            "duplicate_key": '{"schema_version":1,"schema_version":1,"experiment_id":"pilot-paper-recette",'
                             '"max_state_bytes":268435456,"reserve_bytes":134217728}',
            "nan": '{"schema_version":1,"experiment_id":"pilot-paper-recette",'
                   '"max_state_bytes":NaN,"reserve_bytes":134217728}',
            "not_json": "max_state_bytes=1",
            "too_large": json.dumps(dict(VALID)) + " " * 5000,
        }
        for name, raw in raw_cases.items():
            with self.subTest(case=name):
                with self.assertRaises(limits_module.StorageLimitsError):
                    limits_module.load_demo_limits(self.limits_file(raw, name=f"{name}.json"))

    def test_linked_missing_or_special_files_are_refused(self) -> None:
        target = self.limits_file(VALID)
        link = self.base / "link.json"
        link.symlink_to(target)
        fifo = self.base / "fifo.json"
        os.mkfifo(fifo)
        for path in (link, self.base / "absent.json", fifo, self.base):
            with self.subTest(path=path.name):
                with self.assertRaises(limits_module.StorageLimitsError):
                    limits_module.load_demo_limits(path)


class ServiceDemoLimitsTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.base / "volume"

    async def runner(self, root, *, environ, bounds, on_cycle=None):
        (Path(root) / pilot_paper.STOP_FILE).write_text("stop")
        return {"stop_reason": "max_cycles_reached", "completed_cycles": 1, "samples": 1,
                "entries": 0, "exits": 0, "public_proof": "not_eligible_injected_collector"}

    async def no_sleep(self, seconds):
        return None

    def serve(self, root=None, environ=None, **kwargs):
        options = dict(environ=PAPER if environ is None else environ, stream=io.StringIO(),
                       sleep=self.no_sleep, report_clock=lambda: AT, session_runner=self.runner,
                       max_cycles=1, interval_seconds=60)
        options.update(kwargs)
        return asyncio.run(service.serve(self.root if root is None else root, **options))

    def test_demo_limits_reach_the_report_as_local_demo(self) -> None:
        self.serve(storage_limits_path=self.limits_file(VALID))
        report = status_report.read_recette_report(self.root / status_report.REPORT_NAME)
        storage = report["storage"]
        self.assertEqual(storage["limits_source"], "local_demo")
        self.assertEqual((storage["max_state_bytes"], storage["reserve_bytes"]),
                         (VALID["max_state_bytes"], VALID["reserve_bytes"]))
        self.assertEqual(report["growth_and_projection"]["status"], "insufficient_data")
        self.assertEqual(report["notifications"]["transport"], "local_outbox")
        verdict = evidence.check_window(dict(report, freshness={"stale": False}), "0" * 64)
        self.assertFalse(verdict["window_may_open"])
        row = next(r for r in verdict["criteria"] if r["criterion"] == "default_recette_limits")
        self.assertEqual((row["passed"], row["observed"]), (False, "local_demo"))

    def test_demo_limits_are_refused_in_the_deployment_and_with_another_transport(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="recette-demo-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        path = self.limits_file(VALID)
        cases = (
            ("deployment", dict(root=outside / "state", environ=dict(PAPER, HERMES_RECETTE="true"))),
            ("outside_test_tmp", dict(root=outside / "state")),
            ("injected_transport", dict(notification_transport=RecordingTransport())),
        )
        for name, extra in cases:
            with self.subTest(case=name):
                with self.assertRaises(limits_module.StorageLimitsError):
                    self.serve(storage_limits_path=path, **extra)
                self.assertFalse((outside / "state").exists())
                self.assertFalse(self.root.exists())

    def test_invalid_limits_are_refused_before_any_write_and_by_the_cli(self) -> None:
        bad = self.limits_file(dict(VALID, reserve_bytes=1))
        with self.assertRaises(limits_module.StorageLimitsError):
            self.serve(storage_limits_path=bad)
        self.assertFalse(self.root.exists())
        code = service.main(["--root", str(self.root), "--storage-limits", str(bad), "--max-cycles", "1"],
                            environ=dict(PAPER), session_runner=self.runner)
        self.assertEqual(code, 2)
        self.assertFalse(self.root.exists())


class AlertDemoTests(Fixture):
    def run_demo(self, **kwargs):
        output = self.base / "run"
        return output, demo.run_demo(output, environ=dict(PAPER), **kwargs)

    def scenario(self, result, name):
        return next(s for s in result["scenarios"] if s["name"] == name)

    def steps(self, result, name):
        return {step["step"] + "@" + str(step.get("offset_seconds", "")): step
                for step in self.scenario(result, name)["steps"]}

    def test_every_scenario_passes_and_is_labelled_local(self) -> None:
        output, result = self.run_demo()
        self.assertTrue(result["passed"])
        for scenario in result["scenarios"]:
            for step in scenario["steps"]:
                self.assertTrue(step["passed"], (scenario["name"], step))
        self.assertEqual(result["evidence"], "local_demonstration")
        self.assertIs(result["recette_observation"], False)
        self.assertEqual(result["clock"], "simulated")
        self.assertEqual(result["transport"], "local_outbox")
        self.assertEqual(result["limits_source"], "local_demo")
        self.assertEqual(result["limits_origin"], "default")
        self.assertEqual(result["runtime"], fingerprint.runtime_fingerprint())
        stored = json.loads((output / demo.RESULT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(stored, result)
        for relative in result["outputs"]:
            self.assertTrue((output / relative).is_file(), relative)
        self.assertNotIn(str(ROOT), json.dumps(result))

    def test_threshold_is_strictly_below_seven_days(self) -> None:
        _, result = self.run_demo()
        scenario = self.scenario(result, "threshold_boundary")
        g = scenario["growth_bytes_per_day"]
        self.assertEqual(scenario["headroom_bytes"], {"seven_days": 7 * g, "six_days": 7 * g - 1})
        steps = self.steps(result, "threshold_boundary")
        self.assertEqual(steps["seven_days_does_not_alert@900"]["observed"]["alerts"], [])
        self.assertEqual(steps["six_days_alerts@0"]["observed"]["alerts"], [
            {"type": "storage_projection_below_7_days", "reason": "occurrence",
             "suppressed": 0, "days_before_reserve": 6}])
        self.assertEqual(steps["insufficient_data_never_alerts@0"]["projection_status"], "insufficient_data")
        self.assertEqual(steps["insufficient_data_never_alerts@0"]["observed"]["alerts"], [])

    def test_aggregation_delay_and_restart_boundary_are_exact(self) -> None:
        output, result = self.run_demo()
        steps = self.steps(result, "aggregation_and_delay")
        self.assertEqual(steps["one_second_before_limit_suppressed@899"]["observed"]["alerts"], [])
        self.assertEqual(steps["at_fifteen_minutes_emitted_with_count@900"]["observed"]["alerts"][0]["suppressed"], 3)
        restart = self.steps(result, "restart_boundary")
        self.assertEqual(restart["after_restart_still_inside_delay@899"]["observed"],
                         {"alerts": [], "balances": 0})
        flushed = restart["condition_stopped_count_flushed_at_limit@900"]["observed"]["alerts"]
        self.assertEqual(flushed, [{"type": "storage_projection_below_7_days", "reason": "suppressed_flush",
                                    "suppressed": 2, "days_before_reserve": None}])
        state = json.loads((output / "restart_boundary" / notifications.STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(state["restarts"], 2)
        self.assertEqual([m["kind"] for m in state["outbox"]], ["alert", "balances", "alert", "balances"])
        self.assertEqual(state["transport_errors"], 0)

    def test_types_keep_separate_delays_and_counters(self) -> None:
        _, result = self.run_demo()
        counters = self.scenario(result, "type_isolation")["counters"]["alerts"]
        self.assertEqual(counters["storage_projection_below_7_days"]["suppressed_total"], 1)
        self.assertEqual(counters["storage_projection_unavailable"]["suppressed_total"], 1)
        steps = self.steps(result, "type_isolation")
        self.assertEqual(steps["other_type_has_its_own_delay@60"]["observed"]["alerts"][0]["suppressed"], 0)

    def test_limits_file_is_used_and_still_demonstrates_the_boundary(self) -> None:
        custom = dict(VALID, max_state_bytes=512 * MIB, reserve_bytes=200 * MIB)
        _, result = self.run_demo(limits_path=self.limits_file(custom))
        self.assertTrue(result["passed"])
        self.assertEqual((result["limits_origin"], result["limits"]), ("file", custom))

    def test_demo_is_refused_in_the_deployment_outside_test_tmp_or_on_a_used_output(self) -> None:
        with self.assertRaises(demo.DemoError):
            demo.run_demo(self.base / "run", environ=dict(PAPER, HERMES_RECETTE="true"))
        outside = Path(tempfile.mkdtemp(prefix="recette-demo-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with self.assertRaises(demo.DemoError):
            demo.run_demo(outside / "run", environ=dict(PAPER))
        used = self.base / "used"
        used.mkdir()
        (used / "keep.txt").write_text("x")
        with self.assertRaises(demo.DemoError):
            demo.run_demo(used, environ=dict(PAPER))
        self.assertEqual([p.name for p in used.iterdir()], ["keep.txt"])
        with self.assertRaises(limits_module.StorageLimitsError):
            demo.run_demo(self.base / "run2", environ=dict(PAPER),
                          limits_path=self.limits_file(dict(VALID, reserve_bytes=1)))
        self.assertFalse((self.base / "run2").exists())

    def test_cli_summary_and_result_are_refused_as_recette_evidence(self) -> None:
        out = io.StringIO()
        code = demo.main(["--output", str(self.base / "cli")], environ=dict(PAPER), stdout=out)
        self.assertEqual(code, 0)
        summary = json.loads(out.getvalue())
        self.assertEqual((summary["evidence"], summary["recette_observation"], summary["passed"]),
                         ("local_demonstration", False, True))
        with self.assertRaises(evidence.EvidenceError):
            evidence.load_report(self.base / "cli" / demo.RESULT_NAME)
        out = io.StringIO()
        self.assertEqual(evidence.main(["window", str(self.base / "cli" / demo.RESULT_NAME)], stdout=out), 2)


if __name__ == "__main__":
    unittest.main()
