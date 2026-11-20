"""Compact, expunged recette status report (``schema_version: 1``) and its
read-only HTTP endpoint.

The recette service (``scripts.recette_service``) writes
``<volume>/recette_status.json`` at start, after every paper cycle, after every
bounded session and when it stops or fails. The contract is documented in
``docs/RECETTE.md`` (section « Rapport de recette »).

Privacy rules, enforced here and by ``tests/test_recette_status_report.py``:

* no absolute path, no environment value, no raw exception message: errors are
  reduced to a bounded code (exception class name or a known stop reason);
* only public paper metrics: balances, counts, byte sizes, booleans, timestamps.

The endpoint serves only that one file, for ``GET``/``HEAD /recette``: any other
path is ``404`` and any other method ``405``. The request path never reaches the
filesystem, so no arbitrary path can be read, and nothing is ever written.
"""
from __future__ import annotations

import builtins
import json
import os
import re
import shutil
import stat
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from hermes_trading import paper_history_retention as retention

SCHEMA_VERSION = 1
REPORT_KIND = "hermes_recette_status"
REPORT_NAME = "recette_status.json"
ENDPOINT_PATH = "/recette"
EVENTS = ("start", "cycle", "tick", "pass", "stop", "error")
# A report older than one interval plus this grace is stale (the cycle itself
# and the public-market retries fit well inside it).
STALE_GRACE_SECONDS = 900.0
MAX_REPORT_BYTES = 64 * 1024
MAX_INPUT_BYTES = 256 * 1024
MAX_RECENT_ERRORS = 10
# Recette default storage budget: the whole volume, with a reserve of at least
# two maximum packets (``paired_storage_admission`` rule) or a tenth of it.
DEFAULT_RESERVE_FRACTION = 10


class RecetteReportError(RuntimeError):
    """The recette report cannot be written safely."""


def _utc(value: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if value is None else value
    if value.tzinfo is None:
        raise ValueError("an aware time is required")
    return value.astimezone(timezone.utc)


# Publishable codes. Any other string becomes ``UNRECOGNIZED_CODE``: a free-form
# message, even without a colon or spaces, is never copied into the report.
KNOWN_CODES = frozenset({
    # pilot_paper stop reasons and public proof verdicts
    "max_cycles_reached", "stop_requested", "max_runtime_reached", "engine_halted",
    "hard_runtime_limit", "interrupted", "error", "collection_unavailable",
    "not_eligible_injected_collector", "not_eligible_replay_not_public",
    "not_eligible_no_public_sample", "not_eligible_zero_trade",
    "candidate_requires_review", "candidate_requires_review_public_replay",
    # paper_safety entry admission reasons
    "volume_measurement_unknown", "volume_threshold", "worker_error_halt",
    "supervisor_error_halt", "supervisor_recovery_pending", "supervisor_health_missing",
    "supervisor_health_stale", "safety_state_unknown",
    # paired_storage_admission reasons
    "state_budget", "filesystem_reserve", "measurement_unavailable", "limits_unavailable",
    # wallet observation / metrics statuses
    "observed", "measured", "incomplete", "insolvent", "missing", "current", "blocked",
    "unavailable", "not_observed", "measurement_blocked",
    # recette service (error source and codes)
    "recette_service", "volume_unidentified",
})
UNRECOGNIZED_CODE = "unrecognized"
# Publishable exception class names: an explicit list, never a pattern. The
# standard exception names of ``builtins`` plus the classes raised on the
# pilot, wallet, storage and recette paths. A string merely shaped like a class
# name (``SomethingPrivateError``) is not a code.
KNOWN_ERROR_CLASSES = frozenset({
    name for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
}) | frozenset({
    "PilotSessionError", "PilotCollectionUnavailable", "CapacityUnavailable",
    "StorageStopReached", "PairedStopReached", "PairedDeadlineReached",
    "ExecutionSafetyError", "TradingModeError", "NetworkBlockedError",
    "TransportError", "ExchangePayloadError", "VenueConstraintsError",
    "SchemaError", "UnsupportedSchemaError", "ReplayArchiveError", "ReplayExhausted",
    "CaptureSourceChanged", "SnapshotReportHeartbeatMismatch", "RecetteError",
    "RecetteReportError", "JSONDecodeError",
})


def error_code(value: Any) -> str | None:
    """Reduce an error to a publishable code; any free-form text is dropped.

    Exceptions give their class name when it is in ``KNOWN_ERROR_CLASSES``;
    strings are kept only when their head (before ``":"``, the message being
    dropped) is in ``KNOWN_CODES`` or ``KNOWN_ERROR_CLASSES``. Everything else
    is ``UNRECOGNIZED_CODE``.
    """
    if value is None:
        return None
    if isinstance(value, BaseException):
        name = type(value).__name__
        return name if name in KNOWN_ERROR_CLASSES else UNRECOGNIZED_CODE
    if not isinstance(value, str):
        return UNRECOGNIZED_CODE
    head = value.split(":", 1)[0].strip()
    if head in KNOWN_CODES or head in KNOWN_ERROR_CLASSES:
        return head
    return UNRECOGNIZED_CODE


def _read_json(path: Path, *, limit: int = MAX_INPUT_BYTES) -> Any:
    """Bounded read of one regular, link-free JSON file; ``None`` otherwise."""
    try:
        # O_NONBLOCK: opening a FIFO (or a device) must not wait for a writer;
        # ``fstat`` then refuses every non-regular file immediately.
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0))
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return None
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def _decimal_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return str(number) if number.is_finite() else None


# Free text never reaches the report: every string field is either a code
# (``error_code``), a normalized timestamp, or matched against a strict pattern
# or list. A value that does not qualify is dropped (``None``) or, for a status,
# becomes ``UNRECOGNIZED_CODE``; it is never truncated and copied.
MAX_TIMESTAMP_CHARS = 40
EXPERIMENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
CODE_VERSION_PATTERN = re.compile(
    r"unknown|[0-9]{1,6}(\.[0-9]{1,6}){0,3}((a|b|rc|\.post|\.dev)[0-9]{1,6}){0,3}")
KNOWN_MANIFEST_STATUSES = frozenset({"pilot_bootstrap", "paper_ready"})


def _timestamp(value: Any) -> str | None:
    """An aware ISO 8601 time, normalized to UTC; anything else is ``None``."""
    if not isinstance(value, str) or len(value) > MAX_TIMESTAMP_CHARS:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc).isoformat()
    except (OverflowError, ValueError):
        return None


def _matching(value: Any, pattern: re.Pattern) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _manifest_status(manifest: dict | None) -> str:
    if manifest is None:
        return "missing"
    status = manifest.get("status")
    return status if status in KNOWN_MANIFEST_STATUSES else UNRECOGNIZED_CODE


def _manifest(pilot: Path) -> dict | None:
    manifest = _read_json(pilot / "manifest.json")
    return manifest if isinstance(manifest, dict) else None


def _session(pilot: Path) -> dict:
    """Summary of the last finished bounded session (``pilot_session.json``)."""
    from hermes_trading import pilot_paper

    report = _read_json(pilot / pilot_paper.SESSION_REPORT)
    if not isinstance(report, dict):
        return {"status": "none"}
    return {
        "status": "finished",
        "started_at": _timestamp(report.get("started_at")),
        "finished_at": _timestamp(report.get("finished_at")),
        "stop_reason": error_code(report.get("stop_reason")),
        "error_code": error_code(report.get("error")),
        "completed_cycles": _count(report.get("completed_cycles")),
        "samples": _count(report.get("samples")),
        "entries": _count(report.get("entries")),
        "exits": _count(report.get("exits")),
        "public_proof": error_code(report.get("public_proof")),
    }


def _entry_admission(pilot: Path, experiment_id: str | None) -> dict:
    """``paper_safety`` entry admission of the pilot; unknown blocks."""
    from hermes_trading import paper_safety

    try:
        policy = paper_safety.load_policy(pilot, experiment_id)
        status = paper_safety.entry_status(pilot, policy=policy)
    except Exception as error:  # noqa: BLE001 - reported as a code only
        return {"policy": "unreadable", "enabled": True, "blocked": True,
                "reasons": ["admission_unavailable_" + error_code(error)]}
    reasons = [code for code in (error_code(reason) for reason in status.get("reasons") or []) if code]
    return {
        "policy": "absent" if policy is None else "present",
        "enabled": bool(status.get("enabled")),
        "blocked": bool(status.get("blocked")),
        "reasons": reasons[:16],
    }


def default_storage_limits(volume: Path, experiment_id: str | None) -> dict | None:
    """Recette budget: the whole volume, reserve max(2 packets, 1/10)."""
    from hermes_trading.paired_packet_storage import MAX_PACKET_BYTES

    try:
        total = shutil.disk_usage(volume).total
    except OSError:
        return None
    total = min(int(total), 1024 ** 4)
    reserve = max(2 * MAX_PACKET_BYTES, total // DEFAULT_RESERVE_FRACTION)
    if reserve >= total:
        return None
    return {"schema_version": 1, "experiment_id": experiment_id,
            "max_state_bytes": total, "reserve_bytes": reserve}


def _storage(volume: Path, experiment_id: str | None, limits: dict | None) -> dict:
    from hermes_trading.paired_storage_admission import inspect_storage

    source = "configured" if limits is not None else "recette_default"
    limits = limits if limits is not None else default_storage_limits(volume, experiment_id)
    if limits is None:
        return {"status": "blocked", "reason": "limits_unavailable", "limits_source": source,
                "state_bytes": None, "free_bytes": None, "reserve_bytes": None,
                "max_state_bytes": None, "files": None}
    try:
        report = inspect_storage(volume, limits)
        reason = error_code(report.get("reason"))
    except (OSError, ValueError) as error:
        report = {"status": "blocked"}
        reason = "limits_invalid_" + error_code(error)
    return {
        "status": "ready" if report.get("status") == "ready" else "blocked",
        "reason": reason,
        "limits_source": source,
        "state_bytes": _count(report.get("state_bytes")),
        "free_bytes": _count(report.get("free_bytes")),
        "reserve_bytes": _count(limits.get("reserve_bytes")),
        "max_state_bytes": _count(limits.get("max_state_bytes")),
        "files": _count(report.get("files")),
    }


def _locks(pilot: Path, session: dict) -> dict:
    from hermes_trading import pilot_paper

    safety = _read_json(pilot / "paper_safety_state.json")
    worker = safety.get("worker") if isinstance(safety, dict) else None
    supervisor = safety.get("supervisor") if isinstance(safety, dict) else None
    flags = {
        "stop_requested": (pilot / pilot_paper.STOP_FILE).exists(),
        "worker_entry_halt": bool(isinstance(worker, dict) and worker.get("entry_halt")),
        "supervisor_entry_halt": bool(isinstance(supervisor, dict) and supervisor.get("entry_halt")),
        "supervisor_recovery_pending": (pilot / "paper_supervisor_recovery_pending.json").exists(),
        "engine_halted": session.get("stop_reason") == "engine_halted",
    }
    return {"active": sorted(name for name, value in flags.items() if value), **flags}


def _accounts(pilot: Path, manifest: dict | None) -> dict:
    """Balance of the eight pilot accounts (starting balance, last net equity)."""
    accounts: dict[str, dict] = {}
    profiles = manifest.get("profiles") if isinstance(manifest, dict) else None
    for profile in profiles if isinstance(profiles, list) else []:
        if not isinstance(profile, dict):
            continue
        arm, wallet, directory = profile.get("arm"), profile.get("wallet_eur"), profile.get("directory")
        if (arm not in ("adaptive", "control") or type(wallet) is not int
                or directory != f"{arm}_{wallet}"):
            continue
        account = _read_json(pilot / directory / "wallet_account.json")
        account = account if isinstance(account, dict) else {}
        metrics = _read_json(pilot / directory / "wallet_metrics.json")
        metrics = metrics if isinstance(metrics, dict) else {}
        observation = metrics.get("observation") if isinstance(metrics.get("observation"), dict) else {}
        accounts[directory] = {
            "arm": arm,
            "wallet_eur": wallet,
            "currency": _matching(account.get("currency"), CURRENCY_PATTERN),
            "starting_balance": _decimal_text(account.get("starting_balance")),
            "status": error_code(metrics.get("status")) if metrics else "not_observed",
            "net_equity": _decimal_text(observation.get("net_equity")),
            "observed_at": _timestamp(metrics.get("observed_at")),
            "error_code": error_code(metrics.get("error")),
        }
    return {"count": len(accounts), "expected": 8, "accounts": dict(sorted(accounts.items()))}


def _recent_errors(session: dict, accounts: dict, service_errors: Iterable[dict]) -> list[dict]:
    errors = [
        {"at": _timestamp(row.get("at")), "source": error_code(row.get("source")), "code": error_code(row.get("code"))}
        for row in service_errors if isinstance(row, dict)
    ]
    if session.get("error_code"):
        errors.append({"at": session.get("finished_at"), "source": "pilot_session",
                       "code": session["error_code"]})
    for name, account in accounts["accounts"].items():
        if account.get("error_code"):
            errors.append({"at": account.get("observed_at"), "source": "account_" + name,
                           "code": account["error_code"]})
    errors.sort(key=lambda row: row.get("at") or "", reverse=True)
    return errors[:MAX_RECENT_ERRORS]


def _daily_summary(pilot: Path) -> dict:
    """Permanent daily summaries for every account, surviving detail archival."""
    # A FIFO or missing manifest would block or fail inside ``daily_summaries``,
    # so the report reads it only when it is a regular file.
    if not (pilot / "manifest.json").is_file():
        return {"status": "unavailable", "reason": "manifest_unavailable"}
    try:
        summaries = retention.daily_summaries(pilot)
    except Exception as error:  # noqa: BLE001 - reported as a code only
        return {"status": "unavailable", "reason": error_code(error)}
    accounts: dict[str, dict] = {}
    total_decisions = 0
    for name, rows in sorted(summaries.items()):
        if not rows:
            accounts[name] = {
                "days": 0, "decisions": 0, "last_date": None,
                "last_balance": None, "last_costs": None,
            }
            continue
        decisions = sum(row["decisions"] for row in rows)
        total_decisions += decisions
        last = rows[-1]
        accounts[name] = {
            "days": len(rows),
            "decisions": decisions,
            "last_date": last["date"],
            "last_balance": _decimal_text(last["balance"]),
            "last_costs": _decimal_text(last["costs"]),
        }
    return {"status": "ready", "accounts": accounts, "total_decisions": total_decisions}


def _growth_and_projection(pilot: Path, now: datetime, storage_report: dict) -> dict:
    """Project days before the storage reserve is reached.

    Uses the same integer inputs already validated by ``_storage``.  If the
    storage measurement itself is unavailable, the projection is unavailable too.
    """
    values = {
        "state_bytes": storage_report.get("state_bytes"),
        "free_bytes": storage_report.get("free_bytes"),
        "reserve_bytes": storage_report.get("reserve_bytes"),
        "max_state_bytes": storage_report.get("max_state_bytes"),
    }
    if None in values.values():
        return _unavailable_projection("storage_unavailable")
    try:
        return retention.storage_projection(
            pilot, now,
            state_bytes=values["state_bytes"],
            free_bytes=values["free_bytes"],
            reserve_bytes=values["reserve_bytes"],
            max_state_bytes=values["max_state_bytes"],
        )
    except Exception as error:  # noqa: BLE001 - reported as a code only
        return _unavailable_projection(error_code(error))


def _unavailable_projection(reason: str) -> dict:
    return {"schema_version": retention.PROJECTION_SCHEMA, "status": "unavailable",
            "bytes_per_day": None, "days_before_reserve": None, "reason": reason}


def build_recette_report(
    volume: Path | str,
    pilot: Path | str,
    *,
    event: str,
    now: datetime | None = None,
    interval_seconds: float,
    service: dict | None = None,
    service_errors: Iterable[dict] = (),
    storage_limits: dict | None = None,
    notifications: dict | None = None,
) -> dict:
    """Build the expunged ``schema_version: 1`` report for one recette volume."""
    if event not in EVENTS:
        raise ValueError(f"unknown recette report event: {event}")
    at = _utc(now)
    volume, pilot = Path(volume), Path(pilot)
    manifest = _manifest(pilot)
    # Raw identity for the admission/storage checks; only a well-formed one is published.
    experiment_id = manifest.get("experiment_id") if manifest else None
    experiment_id = experiment_id if isinstance(experiment_id, str) else None
    session = _session(pilot)
    accounts = _accounts(pilot, manifest)
    service = dict(service or {})
    storage_report = _storage(pilot, experiment_id, storage_limits)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at": at.isoformat(),
        "heartbeat": {
            "at": at.isoformat(),
            "event": event,
            "interval_seconds": float(interval_seconds),
            "stale_after": (at + timedelta(seconds=float(interval_seconds) + STALE_GRACE_SECONDS)).isoformat(),
            "pass": _count(service.get("pass")),
            "cycle": _count(service.get("cycle")),
            "code_version": _matching(service.get("code_version"), CODE_VERSION_PATTERN),
        },
        "experiment_id": _matching(experiment_id, EXPERIMENT_ID_PATTERN),
        "manifest_status": _manifest_status(manifest),
        "session": session,
        "entry_admission": _entry_admission(pilot, experiment_id),
        "storage": storage_report,
        "growth_and_projection": _growth_and_projection(pilot, at, storage_report),
        "daily_summary": _daily_summary(pilot),
        "locks": _locks(pilot, session),
        "recent_errors": _recent_errors(session, accounts, service_errors),
        "accounts": accounts,
        # Counters of ``scripts.recette_notifications``; the service fills them
        # in after evaluating this very report (``{"status": "disabled"}`` otherwise).
        "notifications": dict(notifications) if isinstance(notifications, dict) else {"status": "disabled"},
    }


def encode_report(report: dict) -> bytes:
    raw = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_REPORT_BYTES:
        raise RecetteReportError("recette report exceeds its size bound")
    return raw


def write_recette_report(volume: Path | str, report: dict) -> Path:
    """Write the report through a sibling temporary file and ``os.replace``.

    Readers see the previous complete report or the new one, never a partial
    file; a link planted at the target is replaced, not followed.
    """
    volume = Path(volume)
    raw = encode_report(report)
    target = volume / REPORT_NAME
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RecetteReportError("recette report target is not a regular file")
    temporary = volume / f".{REPORT_NAME}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(raw + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def read_recette_report(path: Path | str) -> dict | None:
    """The stored report if it is a valid ``schema_version: 1`` report."""
    report = _read_json(Path(path), limit=MAX_REPORT_BYTES + 1)
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION \
            or report.get("kind") != REPORT_KIND:
        return None
    return report


def freshness(report: dict, *, now: datetime | None = None) -> dict:
    """Age of a stored report and whether it is stale (unknown means stale)."""
    at = _utc(now)
    try:
        generated = datetime.fromisoformat(report["generated_at"])
        stale_after = datetime.fromisoformat(report["heartbeat"]["stale_after"])
        age = (at - _utc(generated)).total_seconds()
        stale = at > _utc(stale_after) or age < 0
    except (KeyError, TypeError, ValueError):
        age, stale = None, True
    return {"served_at": at.isoformat(), "age_seconds": age, "stale": stale}


def make_handler(report_path: Path | str, *, clock=None) -> type[BaseHTTPRequestHandler]:
    """Read-only handler bound to one fixed report file."""
    report_path = Path(report_path)
    clock = clock or (lambda: datetime.now(timezone.utc))

    class RecetteReportHandler(BaseHTTPRequestHandler):
        server_version = "hermes-recette"
        sys_version = ""

        def _send(self, status: int, payload: dict, *, body: bool = True) -> None:
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 405:
                self.send_header("Allow", "GET, HEAD")
            self.end_headers()
            if body:
                self.wfile.write(raw)

        def _serve(self, *, body: bool) -> None:
            if self.path != ENDPOINT_PATH:
                self._send(404, {"error": "not_found"}, body=body)
                return
            report = read_recette_report(report_path)
            if report is None:
                self._send(503, {"schema_version": SCHEMA_VERSION, "error": "report_unavailable",
                                 "freshness": {"served_at": _utc(clock()).isoformat(),
                                               "age_seconds": None, "stale": True}}, body=body)
                return
            self._send(200, {**report, "freshness": freshness(report, now=clock())}, body=body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self._serve(body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(body=False)

        def _refuse(self) -> None:
            self._send(405, {"error": "method_not_allowed"})

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return RecetteReportHandler


def start_report_server(report_path: Path | str, *, host: str, port: int,
                        clock=None) -> ThreadingHTTPServer:
    """Serve the report from a daemon thread; ``server.shutdown()`` stops it."""
    server = ThreadingHTTPServer((host, port), make_handler(report_path, clock=clock))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="recette-report-http", daemon=True)
    thread.start()
    return server
