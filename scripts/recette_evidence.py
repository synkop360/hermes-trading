"""Offline checks of saved recette reports: window opening/closing, restart durability.

Delivered by LAT-141 (lot 1, C1); protocol in ``docs/RECETTE.md`` (section
« Protocole de preuve »). Reads only files given on the command line --
reports saved from ``GET /recette`` (with their ``freshness``) -- and prints a
JSON verdict; it never calls the network, never restarts anything and never
writes. Every criterion is listed with what was observed, so a refusal says why.

* ``window REPORT --expected-fingerprint HEX`` -- may the 24 h window open? The
  executed fingerprint must be identified **and** equal the delivery's; the
  projection must be **computed** (``projected``: ``insufficient_data``,
  ``non_positive_growth``, ``unavailable`` or ``reserve_reached`` never
  qualify); default recette limits (a ``local_demo`` report never qualifies);
  a fresh, healthy report.
* ``window-close OPEN CLOSE --expected-fingerprint HEX`` -- both reports
  qualify, on the same volume and the same fingerprint, at least 24 h apart. A
  different fingerprint means a new merge reached the recette: requalify, then
  open a new window.
* ``restart BEFORE AFTER`` -- durability of the pilot across one restart:
  same volume, a new process, accounts, history, summaries and projection
  samples kept, notification state resumed, bounds provenance reported.

A local demonstration result (``scripts.recette_alert_demo``) is refused: it
is never recette evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from scripts import recette_fingerprint as fingerprint
from scripts import recette_status_report as status_report

WINDOW_HOURS = 24
CLEAN_STOPS = frozenset({"max_cycles_reached", "max_runtime_reached", "stop_requested"})
RULES = (
    "window_opens_only_after_identified_fingerprint_and_projected_status",
    "integration_frozen_during_window",
    "window_lasts_at_least_24_hours",
    "any_new_merge_requires_requalification_then_new_window",
    "local_demonstration_is_never_recette_evidence",
)


class EvidenceError(RuntimeError):
    """A file is not a usable recette report."""


def load_report(path: Path | str) -> dict:
    raw = status_report._read_json(Path(path), limit=status_report.MAX_REPORT_BYTES + 4096)
    # A recorded public observation wraps the served payload:
    # ``{"received_at", "status", "sha256", "report": {...}}``.
    if isinstance(raw, dict) and "kind" not in raw and isinstance(raw.get("report"), dict):
        raw = raw["report"]
    if isinstance(raw, dict) and raw.get("kind") == "hermes_recette_alert_demo":
        raise EvidenceError("a local demonstration is never recette evidence")
    if not isinstance(raw, dict) or raw.get("schema_version") != status_report.SCHEMA_VERSION \
            or raw.get("kind") != status_report.REPORT_KIND:
        raise EvidenceError("not a schema_version 1 recette report")
    return raw


def _criterion(name: str, passed: bool, observed) -> dict:
    return {"criterion": name, "passed": bool(passed), "observed": observed}


def _get(report: dict, *keys):
    value = report
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _runtime_id(report: dict) -> str | None:
    runtime = report.get("runtime")
    if fingerprint.is_valid_summary(runtime) and runtime.get("status") == "identified":
        return runtime["fingerprint"]
    return None


def window_criteria(report: dict, expected: str | None) -> list[dict]:
    executed = _runtime_id(report)
    session = report.get("session") if isinstance(report.get("session"), dict) else {}
    accounts = report.get("accounts") if isinstance(report.get("accounts"), dict) else {}
    freshness = report.get("freshness")
    return [
        _criterion("runtime_fingerprint_identified", executed is not None,
                   _get(report, "runtime", "status")),
        _criterion("runtime_matches_delivery",
                   executed is not None and expected is not None and executed == expected,
                   {"executed": executed, "expected": expected}),
        _criterion("projection_computed", _get(report, "growth_and_projection", "status") == "projected",
                   {"status": _get(report, "growth_and_projection", "status"),
                    "days_before_reserve": _get(report, "growth_and_projection", "days_before_reserve"),
                    "sample_days": _get(report, "growth_and_projection", "sample_days")}),
        _criterion("default_recette_limits", _get(report, "storage", "limits_source") == "recette_default",
                   _get(report, "storage", "limits_source")),
        _criterion("served_and_fresh", isinstance(freshness, dict) and freshness.get("stale") is False,
                   freshness.get("stale") if isinstance(freshness, dict) else "freshness_absent"),
        _criterion("storage_ready", _get(report, "storage", "status") == "ready",
                   _get(report, "storage", "status")),
        _criterion("no_recent_errors", report.get("recent_errors") == [],
                   len(report["recent_errors"]) if isinstance(report.get("recent_errors"), list) else None),
        _criterion("no_active_locks", _get(report, "locks", "active") == [], _get(report, "locks", "active")),
        _criterion("session_clean", session.get("status") == "none"
                   or (session.get("status") == "finished" and session.get("stop_reason") in CLEAN_STOPS
                       and session.get("error_code") is None),
                   {"status": session.get("status"), "stop_reason": session.get("stop_reason")}),
        _criterion("eight_accounts", accounts.get("count") == accounts.get("expected") == 8,
                   {"count": accounts.get("count"), "expected": accounts.get("expected")}),
        _criterion("volume_identified", _get(report, "volume", "status") in ("initialized", "recognized")
                   and _get(report, "volume", "marker_created_at") is not None,
                   _get(report, "volume", "status")),
    ]


def check_window(report: dict, expected: str | None) -> dict:
    criteria = window_criteria(report, expected)
    return {"kind": "hermes_recette_window_open_check", "generated_at": report.get("generated_at"),
            "window_may_open": all(row["passed"] for row in criteria), "criteria": criteria,
            "rules": list(RULES)}


def check_window_close(opening: dict, closing: dict, expected: str | None) -> dict:
    open_rows, close_rows = window_criteria(opening, expected), window_criteria(closing, expected)
    start, end = _time(opening.get("generated_at")), _time(closing.get("generated_at"))
    hours = (end - start).total_seconds() / 3600 if start and end else None
    same_runtime = _runtime_id(opening) is not None and _runtime_id(opening) == _runtime_id(closing)
    criteria = [
        _criterion("opening_qualifies", all(r["passed"] for r in open_rows),
                   [r["criterion"] for r in open_rows if not r["passed"]]),
        _criterion("closing_qualifies", all(r["passed"] for r in close_rows),
                   [r["criterion"] for r in close_rows if not r["passed"]]),
        _criterion("same_runtime_fingerprint", same_runtime,
                   {"opening": _runtime_id(opening), "closing": _runtime_id(closing),
                    "on_change": "new_merge_requires_requalification_then_new_window"}),
        _criterion("same_volume", _get(opening, "volume", "marker_created_at") is not None
                   and _get(opening, "volume", "marker_created_at") == _get(closing, "volume", "marker_created_at"),
                   {"opening": _get(opening, "volume", "marker_created_at"),
                    "closing": _get(closing, "volume", "marker_created_at")}),
        _criterion("at_least_24_hours", end is not None and start is not None
                   and end - start >= timedelta(hours=WINDOW_HOURS), hours),
    ]
    return {"kind": "hermes_recette_window_close_check", "window_satisfied": all(r["passed"] for r in criteria),
            "criteria": criteria, "rules": list(RULES),
            "note": "Intermediate GET observations during the window remain required by the protocol."}


def _accounts(report: dict) -> dict:
    rows = _get(report, "accounts", "accounts")
    return {name: row.get("starting_balance") for name, row in rows.items()} if isinstance(rows, dict) else {}


def _summary_days(report: dict) -> dict:
    rows = _get(report, "daily_summary", "accounts")
    return {name: row.get("days") for name, row in rows.items()
            if isinstance(row, dict)} if isinstance(rows, dict) else {}


def _not_less(after, before) -> bool:
    return type(after) is int and type(before) is int and after >= before


def check_restart(before: dict, after: dict) -> dict:
    b_start, a_start = _time(_get(before, "volume", "service_started_at")), _time(_get(after, "volume", "service_started_at"))
    b_days, a_days = _summary_days(before), _summary_days(after)
    criteria = [
        _criterion("same_experiment", before.get("experiment_id") is not None
                   and before.get("experiment_id") == after.get("experiment_id"),
                   {"before": before.get("experiment_id"), "after": after.get("experiment_id")}),
        _criterion("same_volume_identity", _get(before, "volume", "marker_created_at") is not None
                   and _get(before, "volume", "marker_created_at") == _get(after, "volume", "marker_created_at"),
                   {"before": _get(before, "volume", "marker_created_at"),
                    "after": _get(after, "volume", "marker_created_at")}),
        _criterion("volume_recognized_after_restart", _get(after, "volume", "status") == "recognized",
                   _get(after, "volume", "status")),
        _criterion("new_service_process", b_start is not None and a_start is not None and a_start > b_start,
                   {"before": _get(before, "volume", "service_started_at"),
                    "after": _get(after, "volume", "service_started_at")}),
        _criterion("accounts_preserved", len(_accounts(before)) == 8 and _accounts(before) == _accounts(after),
                   {"before": len(_accounts(before)), "after": len(_accounts(after))}),
        _criterion("history_preserved", _not_less(_get(after, "daily_summary", "total_decisions"),
                                                  _get(before, "daily_summary", "total_decisions")),
                   {"before": _get(before, "daily_summary", "total_decisions"),
                    "after": _get(after, "daily_summary", "total_decisions")}),
        _criterion("daily_summaries_preserved", bool(b_days) and set(b_days) == set(a_days)
                   and all(_not_less(a_days[name], b_days[name]) for name in b_days),
                   {"before": b_days, "after": a_days}),
        _criterion("projection_samples_preserved",
                   _not_less(_get(after, "growth_and_projection", "sample_days"),
                             _get(before, "growth_and_projection", "sample_days")),
                   {"before": _get(before, "growth_and_projection", "sample_days"),
                    "after": _get(after, "growth_and_projection", "sample_days")}),
        _criterion("notification_state_resumed", _get(after, "notifications", "state") == "resumed"
                   and _not_less(_get(after, "notifications", "restarts"), 1)
                   and (_get(after, "notifications", "restarts") or 0) > (_get(before, "notifications", "restarts") or 0),
                   {"state": _get(after, "notifications", "state"),
                    "restarts_before": _get(before, "notifications", "restarts"),
                    "restarts_after": _get(after, "notifications", "restarts")}),
        _criterion("balance_cadence_not_reset", _not_less(_get(after, "notifications", "balances", "proposed"),
                                                          _get(before, "notifications", "balances", "proposed")),
                   {"before": _get(before, "notifications", "balances", "proposed"),
                    "after": _get(after, "notifications", "balances", "proposed")}),
    ]
    return {
        "kind": "hermes_recette_restart_check",
        "durability_proven": all(r["passed"] for r in criteria), "criteria": criteria,
        "bounds_provenance": {"before": before.get("bounds"), "after": after.get("bounds")},
        "runtime": {"before": _runtime_id(before), "after": _runtime_id(after),
                    "same": _runtime_id(before) is not None and _runtime_id(before) == _runtime_id(after)},
        "not_covered": ["history_retention_archive", "worker_recovery"],
    }


def _hex(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value or ""):
        raise argparse.ArgumentTypeError("expected a 64-character lowercase hexadecimal fingerprint")
    return value


def main(argv=None, *, stdout=None) -> int:
    stdout = sys.stdout if stdout is None else stdout
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    window = commands.add_parser("window")
    window.add_argument("report", type=Path)
    window.add_argument("--expected-fingerprint", type=_hex, default=None)
    close = commands.add_parser("window-close")
    close.add_argument("opening", type=Path)
    close.add_argument("closing", type=Path)
    close.add_argument("--expected-fingerprint", type=_hex, default=None)
    restart = commands.add_parser("restart")
    restart.add_argument("before", type=Path)
    restart.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "window":
            result = check_window(load_report(args.report), args.expected_fingerprint)
            verdict = result["window_may_open"]
        elif args.command == "window-close":
            result = check_window_close(load_report(args.opening), load_report(args.closing),
                                        args.expected_fingerprint)
            verdict = result["window_satisfied"]
        else:
            result = check_restart(load_report(args.before), load_report(args.after))
            verdict = result["durability_proven"]
    except EvidenceError as error:
        print(json.dumps({"kind": "hermes_recette_evidence_refused", "reason": str(error)}), file=stdout)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
