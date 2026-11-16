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

SCHEMA_VERSION = 1
REPORT_KIND = "hermes_recette_status"
REPORT_NAME = "recette_status.json"
ENDPOINT_PATH = "/recette"
EVENTS = ("start", "cycle", "pass", "stop", "error")
# A report older than one interval plus this grace is stale (the cycle itself
# and the public-market retries fit well inside it).
STALE_GRACE_SECONDS = 900.0
MAX_REPORT_BYTES = 64 * 1024
MAX_INPUT_BYTES = 256 * 1024
MAX_RECENT_ERRORS = 10
# Recette default storage budget: the whole volume, with a reserve of at least
# two maximum packets (``paired_storage_admission`` rule) or a tenth of it.
DEFAULT_RESERVE_FRACTION = 10
PENDING_TICKET_5 = "pending_lat_120_daily_summary"


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
# Exception class names only: CamelCase identifier with a conventional suffix.
_CLASS_CODE = re.compile(
    r"[A-Z][A-Za-z0-9]{0,47}(Error|Exception|Unavailable|Timeout|Interrupt|Exit|Warning)")


def error_code(value: Any) -> str | None:
    """Reduce an error to a publishable code; any free-form text is dropped.

    Exceptions give their class name; strings are kept only when they are a
    known code (``KNOWN_CODES``) or an exception class name, possibly followed
    by ``": message"`` (the message is dropped). Everything else is
    ``UNRECOGNIZED_CODE``.
    """
    if value is None:
        return None
    if isinstance(value, BaseException):
        name = type(value).__name__
        return name if _CLASS_CODE.fullmatch(name) else UNRECOGNIZED_CODE
    if not isinstance(value, str):
        return UNRECOGNIZED_CODE
    head = value.split(":", 1)[0].strip()
    if head in KNOWN_CODES or _CLASS_CODE.fullmatch(head):
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


def _text(value: Any, limit: int = 64) -> str | None:
    return value[:limit] if isinstance(value, str) else None


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
        "started_at": _text(report.get("started_at")),
        "finished_at": _text(report.get("finished_at")),
        "stop_reason": error_code(report.get("stop_reason")),
        "error_code": error_code(report.get("error")),
        "completed_cycles": report.get("completed_cycles") if type(report.get("completed_cycles")) is int else None,
        "samples": report.get("samples") if type(report.get("samples")) is int else None,
        "entries": report.get("entries") if type(report.get("entries")) is int else None,
        "exits": report.get("exits") if type(report.get("exits")) is int else None,
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
        "state_bytes": report.get("state_bytes"),
        "free_bytes": report.get("free_bytes"),
        "reserve_bytes": limits.get("reserve_bytes"),
        "max_state_bytes": limits.get("max_state_bytes"),
        "files": report.get("files"),
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
            "currency": _text(account.get("currency"), 8),
            "starting_balance": _decimal_text(account.get("starting_balance")),
            "status": error_code(metrics.get("status")) if metrics else "not_observed",
            "net_equity": _decimal_text(observation.get("net_equity")),
            "observed_at": _text(metrics.get("observed_at")),
            "error_code": error_code(metrics.get("error")),
        }
    return {"count": len(accounts), "expected": 8, "accounts": dict(sorted(accounts.items()))}


def _recent_errors(session: dict, accounts: dict, service_errors: Iterable[dict]) -> list[dict]:
    errors = [
        {"at": _text(row.get("at")), "source": error_code(row.get("source")), "code": error_code(row.get("code"))}
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
) -> dict:
    """Build the expunged ``schema_version: 1`` report for one recette volume."""
    if event not in EVENTS:
        raise ValueError(f"unknown recette report event: {event}")
    at = _utc(now)
    volume, pilot = Path(volume), Path(pilot)
    manifest = _manifest(pilot)
    experiment_id = _text(manifest.get("experiment_id")) if manifest else None
    session = _session(pilot)
    accounts = _accounts(pilot, manifest)
    service = dict(service or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at": at.isoformat(),
        "heartbeat": {
            "at": at.isoformat(),
            "event": event,
            "interval_seconds": float(interval_seconds),
            "stale_after": (at + timedelta(seconds=float(interval_seconds) + STALE_GRACE_SECONDS)).isoformat(),
            "pass": service.get("pass") if type(service.get("pass")) is int else None,
            "cycle": service.get("cycle") if type(service.get("cycle")) is int else None,
            "code_version": _text(service.get("code_version")),
        },
        "experiment_id": experiment_id,
        "manifest_status": _text(manifest.get("status")) if manifest else "missing",
        "session": session,
        "entry_admission": _entry_admission(pilot, experiment_id),
        "storage": _storage(pilot, experiment_id, storage_limits),
        "growth_and_projection": {"status": "unavailable", "reason": PENDING_TICKET_5},
        "daily_summary": {"status": "unavailable", "reason": PENDING_TICKET_5},
        "locks": _locks(pilot, session),
        "recent_errors": _recent_errors(session, accounts, service_errors),
        "accounts": accounts,
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
