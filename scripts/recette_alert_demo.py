"""Local demonstration of the storage threshold and the alert cadence.

Delivered by LAT-141 (lot 1, C1); contract in ``docs/RECETTE.md`` (section
« Démonstration locale des alertes »). It runs the *real* recette code --
``paper_history_retention.storage_projection`` (daily samples in SQLite) and
``scripts.recette_notifications.RecetteNotifier`` (persistent state and local
outbox) -- with a **simulated clock** and **declared** storage sizes under
demonstration limits (``scripts.recette_storage_limits``). Nothing fills a
volume, nothing leaves the output directory: the only transport is the local
outbox, kept in ``<output>/<scenario>/recette_notifications.json`` beside
``<output>/demo_result.json``.

This is a local demonstration, **not** a recette observation: the result says
so (``evidence: local_demonstration``, ``recette_observation: false``) and
``scripts.recette_evidence`` refuses it as window evidence. Refused when
``HERMES_RECETTE=true`` and outside a ``.test-tmp*`` tree.

Usage::

    uv run python -m scripts.recette_alert_demo --output .test-tmp-demo/run \\
        [--storage-limits limits.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_trading import paper_history_retention as retention
from scripts import recette_fingerprint as fingerprint
from scripts import recette_notifications as notifications
from scripts import recette_status_report as status_report
from scripts import recette_storage_limits as storage_limits

RESULT_NAME = "demo_result.json"
DEMO_KIND = "hermes_recette_alert_demo"
EVIDENCE = "local_demonstration"
# Simulated instant, aligned on a UTC quarter-hour: never a real deadline.
SIMULATED_START = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
DELAY = notifications.ALERT_DELAY_SECONDS
BELOW = notifications.ALERT_BELOW_THRESHOLD
UNAVAILABLE = notifications.ALERT_UNAVAILABLE
STATEMENT = ("Local demonstration: simulated clock, declared storage sizes, demonstration limits "
             "and local outbox. It is not a recette observation and never opens or satisfies "
             "the 24 h recette window.")
ACCOUNTS = {
    "accounts": {f"{arm}_{wallet}": {"currency": "EUR", "net_equity": f"{wallet}.00"}
                 for arm in ("adaptive", "control") for wallet in (50, 100, 150, 200)},
}


class DemoError(RuntimeError):
    """The demonstration cannot run safely here."""


def _check_output(output: Path, environ) -> Path:
    if (environ.get("HERMES_RECETTE") or "").strip().lower() == "true":
        raise DemoError("the alert demonstration is local only (HERMES_RECETTE=true)")
    output = Path(output).absolute()
    if not any(part.startswith(".test-tmp") for part in output.parts):
        raise DemoError("demonstration output must be inside a .test-tmp* directory")
    for candidate in (output, *output.parents):
        if candidate.is_symlink():
            raise DemoError("demonstration output path contains a symlink")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise DemoError("demonstration output must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    return output


class Demo:
    def __init__(self, output: Path, limits: dict) -> None:
        self.output = output
        self.limits = limits
        budget = limits["max_state_bytes"] - limits["reserve_bytes"]
        # Daily growth: a sixteenth of the usable budget, so eight days fit.
        self.growth = budget // 16
        self.budget = budget

    # -- projections from the real sampling code ------------------------
    def projection(self, name: str, headroom: int) -> tuple[dict, dict]:
        """Two daily samples one simulated day apart; returns (first, second)."""
        directory = self.output / name / "projection"
        directory.mkdir(parents=True)
        state = self.budget - headroom
        common = {"free_bytes": self.limits["max_state_bytes"],
                  "reserve_bytes": self.limits["reserve_bytes"],
                  "max_state_bytes": self.limits["max_state_bytes"]}
        first = retention.storage_projection(directory, SIMULATED_START - timedelta(days=1),
                                             state_bytes=state - self.growth, **common)
        second = retention.storage_projection(directory, SIMULATED_START, state_bytes=state, **common)
        return first, second

    def notifier(self, name: str):
        """The real notifier on its own demonstration volume (local outbox only)."""
        volume = self.output / name
        volume.mkdir(parents=True, exist_ok=True)
        return notifications.RecetteNotifier(volume)

    # -- one notifier step -------------------------------------------------
    @staticmethod
    def report(projection: dict) -> dict:
        return {"growth_and_projection": projection, "accounts": ACCOUNTS}

    @staticmethod
    def observed(messages: list[dict]) -> dict:
        return {
            "alerts": [{"type": m["type"], "reason": m["reason"], "suppressed": m["suppressed"],
                        "days_before_reserve": m["days_before_reserve"]}
                       for m in messages if m["kind"] == "alert"],
            "balances": sum(1 for m in messages if m["kind"] == "balances"),
        }

    def step(self, notifier, label: str, offset: int, projection: dict, expected: dict) -> dict:
        at = SIMULATED_START + timedelta(seconds=offset)
        observed = self.observed(notifier.process(self.report(projection), at))
        return {"step": label, "simulated_at": at.isoformat(), "offset_seconds": offset,
                "projection_status": projection.get("status"),
                "days_before_reserve": projection.get("days_before_reserve"),
                "expected": expected, "observed": observed, "passed": observed == expected}

    @staticmethod
    def counters(notifier) -> dict:
        summary = notifier.summary()
        return {"state": summary["state"], "restarts": summary["restarts"],
                "transport": summary["transport"], "alerts": summary["alerts"],
                "balances_proposed": summary["balances"]["proposed"], "outbox": summary["outbox"]}

    @staticmethod
    def check(label: str, observed, expected) -> dict:
        return {"step": label, "expected": expected, "observed": observed, "passed": observed == expected}

    # -- scenarios ---------------------------------------------------------
    def threshold_boundary(self) -> dict:
        g = self.growth
        first, seven = self.projection("threshold_seven_days", 7 * g)
        _, six = self.projection("threshold_six_days", 7 * g - 1)
        steps = [
            self.check("first_sample_is_insufficient_data", first["status"], "insufficient_data"),
            self.check("headroom_exactly_seven_days", (seven["status"], seven["bytes_per_day"],
                       seven["days_before_reserve"]), ("projected", g, 7)),
            self.check("headroom_one_byte_short_of_seven_days", (six["status"], six["bytes_per_day"],
                       six["days_before_reserve"]), ("projected", g, 6)),
        ]
        quiet = self.notifier("threshold_seven_days")
        steps.append(self.step(quiet, "insufficient_data_never_alerts", 0, first,
                               {"alerts": [], "balances": 1}))
        steps.append(self.step(quiet, "seven_days_does_not_alert", DELAY, seven,
                               {"alerts": [], "balances": 1}))
        loud = self.notifier("threshold_six_days")
        steps.append(self.step(loud, "six_days_alerts", 0, six, {"alerts": [
            {"type": BELOW, "reason": "occurrence", "suppressed": 0, "days_before_reserve": 6}],
            "balances": 1}))
        self.six = six
        self.quiet = first
        return {"name": "threshold_boundary", "growth_bytes_per_day": g,
                "headroom_bytes": {"seven_days": seven["headroom_bytes"], "six_days": six["headroom_bytes"]},
                "steps": steps}

    def aggregation_and_delay(self) -> dict:
        notifier = self.notifier("aggregation_and_delay")
        alert = lambda suppressed: {"type": BELOW, "reason": "occurrence",  # noqa: E731
                                    "suppressed": suppressed, "days_before_reserve": 6}
        steps = [
            self.step(notifier, "first_occurrence_emitted", 0, self.six, {"alerts": [alert(0)], "balances": 1}),
            self.step(notifier, "inside_delay_suppressed", 300, self.six, {"alerts": [], "balances": 0}),
            self.step(notifier, "inside_delay_suppressed", 600, self.six, {"alerts": [], "balances": 0}),
            self.step(notifier, "one_second_before_limit_suppressed", DELAY - 1, self.six,
                      {"alerts": [], "balances": 0}),
            self.step(notifier, "at_fifteen_minutes_emitted_with_count", DELAY, self.six,
                      {"alerts": [alert(3)], "balances": 1}),
        ]
        counters = self.counters(notifier)
        steps.append(self.check("exact_counters", {
            key: counters["alerts"][BELOW][key]
            for key in ("occurrences", "emitted", "suppressed_total", "pending_suppressed")},
            {"occurrences": 5, "emitted": 2, "suppressed_total": 3, "pending_suppressed": 0}))
        return {"name": "aggregation_and_delay", "steps": steps, "counters": counters}

    def restart_boundary(self) -> dict:
        volume = self.output / "restart_boundary"
        notifier = self.notifier(volume.name)
        steps = [
            self.step(notifier, "first_occurrence_emitted", 0, self.six, {"alerts": [
                {"type": BELOW, "reason": "occurrence", "suppressed": 0, "days_before_reserve": 6}],
                "balances": 1}),
            self.step(notifier, "inside_delay_suppressed", 60, self.six, {"alerts": [], "balances": 0}),
        ]
        notifier = self.notifier(volume.name)
        steps.append(self.check("restart_resumes_the_state", (notifier.state_status,
                                notifier.summary()["restarts"]), ("resumed", 1)))
        steps.append(self.step(notifier, "after_restart_still_inside_delay", DELAY - 1, self.six,
                               {"alerts": [], "balances": 0}))
        notifier = self.notifier(volume.name)
        steps.append(self.check("second_restart_resumes_the_state", (notifier.state_status,
                                notifier.summary()["restarts"]), ("resumed", 2)))
        steps.append(self.step(notifier, "condition_stopped_count_flushed_at_limit", DELAY, self.quiet, {
            "alerts": [{"type": BELOW, "reason": "suppressed_flush", "suppressed": 2,
                        "days_before_reserve": None}], "balances": 1}))
        counters = self.counters(notifier)
        steps.append(self.check("exact_counters", {
            key: counters["alerts"][BELOW][key]
            for key in ("occurrences", "emitted", "suppressed_total", "pending_suppressed")},
            {"occurrences": 3, "emitted": 2, "suppressed_total": 2, "pending_suppressed": 0}))
        return {"name": "restart_boundary", "steps": steps, "counters": counters}

    def type_isolation(self) -> dict:
        notifier = self.notifier("type_isolation")
        missing = status_report._unavailable_projection("storage_unavailable")
        below = lambda suppressed: {"type": BELOW, "reason": "occurrence",  # noqa: E731
                                    "suppressed": suppressed, "days_before_reserve": 6}
        steps = [
            self.step(notifier, "threshold_alert_emitted", 0, self.six, {"alerts": [below(0)], "balances": 1}),
            self.step(notifier, "other_type_has_its_own_delay", 60, missing, {"alerts": [
                {"type": UNAVAILABLE, "reason": "occurrence", "suppressed": 0, "days_before_reserve": None}],
                "balances": 0}),
            self.step(notifier, "threshold_suppressed", 120, self.six, {"alerts": [], "balances": 0}),
            self.step(notifier, "unavailable_suppressed", 180, missing, {"alerts": [], "balances": 0}),
            self.step(notifier, "threshold_emitted_with_its_own_count", DELAY, self.six,
                      {"alerts": [below(1)], "balances": 1}),
            self.step(notifier, "unavailable_flushed_at_its_own_limit", 60 + DELAY, self.quiet, {"alerts": [
                {"type": UNAVAILABLE, "reason": "suppressed_flush", "suppressed": 1,
                 "days_before_reserve": None}], "balances": 0}),
        ]
        counters = self.counters(notifier)
        fields = ("occurrences", "emitted", "suppressed_total", "pending_suppressed")
        steps.append(self.check("counters_per_type", {
            name: {key: counters["alerts"][name][key] for key in fields} for name in (BELOW, UNAVAILABLE)}, {
            BELOW: {"occurrences": 3, "emitted": 2, "suppressed_total": 1, "pending_suppressed": 0},
            UNAVAILABLE: {"occurrences": 2, "emitted": 2, "suppressed_total": 1, "pending_suppressed": 0}}))
        return {"name": "type_isolation", "steps": steps, "counters": counters}


def run_demo(output: Path | str, *, limits_path: Path | str | None = None, environ=None) -> dict:
    environ = os.environ if environ is None else environ
    if limits_path is not None:
        limits, origin = storage_limits.load_demo_limits(limits_path), "file"
    else:
        limits, origin = storage_limits.validate_demo_limits(storage_limits.DEFAULT_DEMO_LIMITS), "default"
    output = _check_output(Path(output), environ)
    demo = Demo(output, limits)
    scenarios = [demo.threshold_boundary(), demo.aggregation_and_delay(),
                 demo.restart_boundary(), demo.type_isolation()]
    passed = all(step["passed"] for scenario in scenarios for step in scenario["steps"])
    result = {
        "schema_version": 1, "kind": DEMO_KIND, "evidence": EVIDENCE, "recette_observation": False,
        "statement": STATEMENT, "clock": "simulated", "simulated_start": SIMULATED_START.isoformat(),
        "transport": notifications.TRANSPORT_NAME, "limits_source": storage_limits.LIMITS_SOURCE,
        "limits_origin": origin, "limits": limits,
        "projection_alert_days": notifications.PROJECTION_ALERT_DAYS, "alert_delay_seconds": DELAY,
        "runtime": fingerprint.runtime_fingerprint(), "scenarios": scenarios, "passed": passed,
        "outputs": sorted([RESULT_NAME] + [f"{s['name']}/{notifications.STATE_NAME}"
                                           for s in scenarios if s["name"] != "threshold_boundary"]
                          + [f"threshold_{d}/{notifications.STATE_NAME}" for d in ("seven_days", "six_days")]),
    }
    # The returned value is exactly the stored one (tuples become lists).
    result = json.loads(json.dumps(result))
    (output / RESULT_NAME).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv=None, *, environ=None, stdout=None) -> int:
    stdout = sys.stdout if stdout is None else stdout
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, required=True, help="empty directory under .test-tmp*")
    parser.add_argument("--storage-limits", type=Path, default=None, help="demonstration limits file")
    args = parser.parse_args(argv)
    try:
        result = run_demo(args.output, limits_path=args.storage_limits, environ=environ)
    except (DemoError, storage_limits.StorageLimitsError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr, flush=True)
        return 2
    summary = {"kind": result["kind"], "evidence": result["evidence"],
               "recette_observation": result["recette_observation"], "passed": result["passed"],
               "steps": {s["name"]: [step["passed"] for step in s["steps"]] for s in result["scenarios"]},
               "runtime_fingerprint": result["runtime"].get("fingerprint")}
    print(json.dumps(summary, sort_keys=True), file=stdout)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
