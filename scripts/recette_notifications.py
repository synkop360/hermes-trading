"""Recette storage alerts and balance cadence, delivered to a local outbox.

Delivered by LAT-122 (lot 1, ticket 7); contract in ``docs/RECETTE.md``
(section « Alertes et cadence »). Two independent streams, never confused:

* **alerts** — one per *type*. ``storage_projection_below_7_days`` when the
  projection (``growth_and_projection`` of the recette report) gives strictly
  fewer than seven days before the storage reserve; ``storage_projection_unavailable``
  when the projection cannot be computed. The first occurrence of a type is
  emitted at once; further occurrences of the *same* type less than fifteen
  minutes after its last emission are suppressed and counted, and the next
  emission (a new occurrence, or the flush of a pending count once the delay has
  elapsed) carries that exact count. Types never share a delay or a counter.
* **balances** — the eight account balances are proposed once per UTC
  quarter-hour slot (``floor(epoch / 900)``), whatever the alert activity.

Everything is persisted in ``<volume>/recette_notifications.json`` (atomic
replace after ``fsync``) *before* any transport is called: a restart keeps the
delays, the pending counts and the current balance slot, so it neither repeats
an alert inside its delay nor proposes a slot twice. Messages land in a bounded
outbox inside that same file — the recette's only transport, local and fake. An
optional injected ``transport.send(message)`` (tests) is called afterwards;
its failures are counted, never raised. No network, no Discord, no secret:
messages carry only codes, integers, UTC timestamps and decimal balances
already expunged by ``scripts.recette_status_report``.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATE_NAME = "recette_notifications.json"
STATE_SCHEMA_VERSION = 1
STATE_KIND = "hermes_recette_notifications"
MAX_STATE_BYTES = 128 * 1024
OUTBOX_LIMIT = 32
ALERT_DELAY_SECONDS = 900
BALANCE_SLOT_SECONDS = 900
PROJECTION_ALERT_DAYS = 7
ALERT_BELOW_THRESHOLD = "storage_projection_below_7_days"
ALERT_UNAVAILABLE = "storage_projection_unavailable"
ALERT_TYPES = (ALERT_BELOW_THRESHOLD, ALERT_UNAVAILABLE)
# Projection statuses that carry a usable ``days_before_reserve``.
MEASURED_STATUSES = frozenset({"projected", "reserve_reached"})
# Statuses that are valid and quiet: no growth, or not enough samples yet.
QUIET_STATUSES = frozenset({"non_positive_growth", "insufficient_data"})
TRANSPORT_NAME = "local_outbox"
_DECIMAL = re.compile(r"-?\d{1,18}(?:\.\d{1,18})?")
_ACCOUNT = re.compile(r"(?:adaptive|control)_\d{1,6}")


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("an aware time is required")
    return value.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def storage_alert(projection: Any) -> dict | None:
    """The alert raised by one projection, or ``None`` when storage is quiet.

    ``days_before_reserve`` is the floor of ``headroom / bytes_per_day``; for a
    non-negative ratio, ``ratio < 7`` is exactly ``floor(ratio) < 7``, so the
    published integer is compared directly: 6 alerts, 7 does not.
    Anything malformed is treated as an unavailable projection, never as quiet.
    """
    status = projection.get("status") if isinstance(projection, dict) else None
    if status in QUIET_STATUSES:
        return None
    if status in MEASURED_STATUSES:
        days = projection.get("days_before_reserve")
        if _count(days):
            if days < PROJECTION_ALERT_DAYS:
                return {"type": ALERT_BELOW_THRESHOLD, "days_before_reserve": days,
                        "projection_status": status}
            return None
    return {"type": ALERT_UNAVAILABLE, "days_before_reserve": None,
            "projection_status": status if status in MEASURED_STATUSES | {"unavailable"} else "unrecognized"}


def balance_rows(report: Any) -> dict:
    """Account balances already expunged by the report (validated again here)."""
    accounts = report.get("accounts") if isinstance(report, dict) else None
    rows = accounts.get("accounts") if isinstance(accounts, dict) else None
    balances: dict[str, dict] = {}
    for name, row in sorted(rows.items()) if isinstance(rows, dict) else ():
        if not isinstance(name, str) or not _ACCOUNT.fullmatch(name) or not isinstance(row, dict):
            continue
        equity = row.get("net_equity")
        currency = row.get("currency")
        balances[name] = {
            "net_equity": equity if isinstance(equity, str) and _DECIMAL.fullmatch(equity) else None,
            "currency": currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None,
        }
    return balances


def _empty_alert() -> dict:
    return {"active": False, "occurrences": 0, "emitted": 0, "suppressed_total": 0,
            "pending_suppressed": 0, "last_emitted_at": None, "last_seen_at": None}


def initial_state() -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION, "kind": STATE_KIND,
        "alerts": {name: _empty_alert() for name in ALERT_TYPES},
        "balances": {"proposed": 0, "last_slot": None, "last_proposed_at": None},
        "sequence": 0, "restarts": 0, "transport_errors": 0, "outbox": [],
    }


def _valid_state(data: Any) -> bool:
    if not isinstance(data, dict) or set(data) != set(initial_state()):
        return False
    if data["schema_version"] != STATE_SCHEMA_VERSION or data["kind"] != STATE_KIND:
        return False
    if not isinstance(data["alerts"], dict) or set(data["alerts"]) != set(ALERT_TYPES):
        return False
    for alert in data["alerts"].values():
        if not isinstance(alert, dict) or set(alert) != set(_empty_alert()):
            return False
        if type(alert["active"]) is not bool:
            return False
        if not all(_count(alert[k]) for k in ("occurrences", "emitted", "suppressed_total", "pending_suppressed")):
            return False
        for key in ("last_emitted_at", "last_seen_at"):
            if alert[key] is not None and _parse_time(alert[key]) is None:
                return False
    balances = data["balances"]
    if not isinstance(balances, dict) or set(balances) != {"proposed", "last_slot", "last_proposed_at"}:
        return False
    if not _count(balances["proposed"]) or not (balances["last_slot"] is None or _count(balances["last_slot"])):
        return False
    if balances["last_proposed_at"] is not None and _parse_time(balances["last_proposed_at"]) is None:
        return False
    return (all(_count(data[k]) for k in ("sequence", "restarts", "transport_errors"))
            and isinstance(data["outbox"], list) and len(data["outbox"]) <= OUTBOX_LIMIT
            and all(isinstance(m, dict) for m in data["outbox"]))


class RecetteNotifier:
    """Persistent alert aggregation and balance cadence for one recette volume."""

    def __init__(self, volume: Path | str, *, transport: Any = None) -> None:
        self.path = Path(volume) / STATE_NAME
        self.transport = transport
        self.state_status = "fresh"
        self.state = self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> dict:
        path = self.path
        if not path.exists() and not path.is_symlink():
            return initial_state()
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError("state is not a bounded regular file")
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not _valid_state(data):
            # A corrupt state may hide a delay: restarting fresh can repeat one
            # alert, never silence it. The reset itself is visible in the report.
            self.state_status = "reset_invalid"
            state = initial_state()
            state["restarts"] = 1
            return state
        self.state_status = "resumed"
        data["restarts"] += 1
        return data

    def _persist(self) -> None:
        self.state["outbox"] = self.state["outbox"][-OUTBOX_LIMIT:]
        raw = json.dumps(self.state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("notification state exceeds its size bound")
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise ValueError("notification state target is not a regular file")
        temporary = self.path.with_name(f".{STATE_NAME}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(raw + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    # -- streams ---------------------------------------------------------
    def _message(self, kind: str, at: datetime, **fields) -> dict:
        self.state["sequence"] += 1
        return {"sequence": self.state["sequence"], "kind": kind, "at": at.isoformat(), **fields}

    def _emit_alert(self, name: str, alert: dict, at: datetime, detail: dict | None) -> dict:
        suppressed = alert["pending_suppressed"]
        alert["pending_suppressed"] = 0
        alert["emitted"] += 1
        alert["last_emitted_at"] = at.isoformat()
        return self._message(
            "alert", at, type=name, suppressed=suppressed,
            reason="occurrence" if detail is not None else "suppressed_flush",
            days_before_reserve=(detail or {}).get("days_before_reserve"),
            projection_status=(detail or {}).get("projection_status"),
            delay_seconds=ALERT_DELAY_SECONDS)

    def _due(self, alert: dict, at: datetime) -> bool:
        last = _parse_time(alert["last_emitted_at"])
        # A clock moving backwards ends the delay: it may repeat, never silence.
        return last is None or at < last or (at - last) >= timedelta(seconds=ALERT_DELAY_SECONDS)

    def _alerts(self, report: dict, at: datetime) -> list[dict]:
        raised = storage_alert(report.get("growth_and_projection"))
        messages = []
        for name in ALERT_TYPES:
            alert = self.state["alerts"][name]
            current = raised if raised is not None and raised["type"] == name else None
            alert["active"] = current is not None
            if current is not None:
                alert["occurrences"] += 1
                alert["last_seen_at"] = at.isoformat()
                if self._due(alert, at):
                    messages.append(self._emit_alert(name, alert, at, current))
                else:
                    alert["pending_suppressed"] += 1
                    alert["suppressed_total"] += 1
            elif alert["pending_suppressed"] and self._due(alert, at):
                # The condition stopped inside the delay: its count is still owed.
                messages.append(self._emit_alert(name, alert, at, None))
        return messages

    def _balances(self, report: dict, at: datetime) -> list[dict]:
        slot = int(at.timestamp()) // BALANCE_SLOT_SECONDS
        balances = self.state["balances"]
        if balances["last_slot"] is not None and slot <= balances["last_slot"]:
            return []
        balances["last_slot"] = slot
        balances["proposed"] += 1
        balances["last_proposed_at"] = at.isoformat()
        start = datetime.fromtimestamp(slot * BALANCE_SLOT_SECONDS, tz=timezone.utc)
        return [self._message("balances", at, slot_start=start.isoformat(),
                              slot_seconds=BALANCE_SLOT_SECONDS, accounts=balance_rows(report))]

    def process(self, report: dict, now: datetime) -> list[dict]:
        """Evaluate one report; persist, then hand new messages to the transport."""
        at = _utc(now)
        messages = self._alerts(report, at) + self._balances(report, at)
        self.state["outbox"].extend(messages)
        self._persist()
        if self.transport is not None and messages:
            for message in messages:
                try:
                    self.transport.send(dict(message))
                except Exception:  # noqa: BLE001 - counted; a sink never stops the recette
                    self.state["transport_errors"] += 1
            self._persist()
        return messages

    def summary(self) -> dict:
        """Public counters for the recette report (codes and integers only)."""
        alerts = {
            name: {key: alert[key] for key in ("active", "occurrences", "emitted", "suppressed_total",
                                               "pending_suppressed", "last_emitted_at")}
            for name, alert in self.state["alerts"].items()
        }
        balances = self.state["balances"]
        return {
            "status": "ready", "state": self.state_status, "transport": TRANSPORT_NAME,
            "alert_delay_seconds": ALERT_DELAY_SECONDS,
            "projection_alert_days": PROJECTION_ALERT_DAYS,
            "alerts": alerts,
            "balances": {"slot_seconds": BALANCE_SLOT_SECONDS, "proposed": balances["proposed"],
                         "last_proposed_at": balances["last_proposed_at"]},
            "messages": self.state["sequence"], "outbox": len(self.state["outbox"]),
            "restarts": self.state["restarts"], "transport_errors": self.state["transport_errors"],
        }


def disabled_summary(status: str = "disabled") -> dict:
    return {"status": status}
