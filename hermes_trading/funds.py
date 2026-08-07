from __future__ import annotations

import os
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Mapping

from hermes_trading.adapters.execution import _default_exchange


class FundsAlertConfigError(RuntimeError):
    """Raised when low-funds alert settings are invalid."""


@dataclass(frozen=True)
class FundsAlertSettings:
    enabled: bool
    quote: str
    min_available: float
    min_order_multiple: float
    buffer_pct: float
    cooldown_seconds: int
    resolve_multiple: float
    mention: str


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _float_at_least(name: str, value: str | None, default: str, minimum: float) -> float:
    try:
        parsed = float(value if value not in {None, ""} else default)
    except ValueError as exc:
        raise FundsAlertConfigError(f"{name} must be numeric") from exc
    if parsed < minimum:
        raise FundsAlertConfigError(f"{name} must be at least {minimum}")
    return parsed


def _int_at_least(name: str, value: str | None, default: str, minimum: int) -> int:
    try:
        parsed = int(value if value not in {None, ""} else default)
    except ValueError as exc:
        raise FundsAlertConfigError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise FundsAlertConfigError(f"{name} must be at least {minimum}")
    return parsed


def resolve_funds_alert_settings(env: dict[str, str] | None = None) -> FundsAlertSettings:
    values = env if env is not None else os.environ
    quote = values.get("HERMES_FUNDS_ALERT_QUOTE", "USDT").strip().upper() or "USDT"
    return FundsAlertSettings(
        enabled=_truthy(values.get("HERMES_FUNDS_ALERT_ENABLED")),
        quote=quote,
        min_available=_float_at_least(
            f"HERMES_FUNDS_MIN_AVAILABLE_{quote}",
            values.get(f"HERMES_FUNDS_MIN_AVAILABLE_{quote}"),
            "20",
            0.0,
        ),
        min_order_multiple=_float_at_least(
            "HERMES_FUNDS_MIN_ORDER_MULTIPLE",
            values.get("HERMES_FUNDS_MIN_ORDER_MULTIPLE"),
            "3",
            0.0,
        ),
        buffer_pct=_float_at_least(
            "HERMES_FUNDS_BUFFER_PCT",
            values.get("HERMES_FUNDS_BUFFER_PCT"),
            "0.10",
            0.0,
        ),
        cooldown_seconds=_int_at_least(
            "HERMES_FUNDS_ALERT_COOLDOWN_SECONDS",
            values.get("HERMES_FUNDS_ALERT_COOLDOWN_SECONDS"),
            "21600",
            0,
        ),
        resolve_multiple=_float_at_least(
            "HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE",
            values.get("HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE"),
            "1.20",
            1.0,
        ),
        mention=values.get("HERMES_FUNDS_ALERT_MENTION", "").strip(),
    )


def _order_budget(order_budget: float | None = None) -> float:
    if order_budget is not None:
        return float(order_budget)
    return _float_at_least("HERMES_TRADING_MAX_ORDER_USDT", os.getenv("HERMES_TRADING_MAX_ORDER_USDT"), "5", 0.01)


def calculate_low_limit(settings: FundsAlertSettings, order_budget: float | None = None) -> float:
    budget = _order_budget(order_budget)
    dynamic_limit = budget * settings.min_order_multiple * (1.0 + settings.buffer_pct)
    return max(settings.min_available, dynamic_limit)


def calculate_critical_limit(
    settings: FundsAlertSettings,
    exchange_min_cost: float | None = None,
    order_budget: float | None = None,
) -> float:
    budget = _order_budget(order_budget)
    if exchange_min_cost is None:
        return budget
    return max(budget, float(exchange_min_cost))


FundsSeverity = Literal["ok", "warning", "critical"]


@dataclass(frozen=True)
class FundsSnapshot:
    quote: str
    available: float
    low_limit: float
    critical_limit: float
    resolve_limit: float
    order_budget: float
    severity: FundsSeverity


def build_funds_snapshot(
    settings: FundsAlertSettings,
    available: float,
    exchange_min_cost: float | None = None,
    order_budget: float | None = None,
) -> FundsSnapshot:
    budget = _order_budget(order_budget)
    low_limit = calculate_low_limit(settings, budget)
    critical_limit = calculate_critical_limit(settings, exchange_min_cost, budget)
    available_value = float(available)
    if available_value <= critical_limit:
        severity: FundsSeverity = "critical"
    elif available_value <= low_limit:
        severity = "warning"
    else:
        severity = "ok"
    return FundsSnapshot(
        quote=settings.quote,
        available=available_value,
        low_limit=low_limit,
        critical_limit=critical_limit,
        resolve_limit=low_limit * settings.resolve_multiple,
        order_budget=budget,
        severity=severity,
    )


def _money(value: float) -> str:
    return f"{value:.2f}"


def format_funds_alert(snapshot: FundsSnapshot, alert_type: str, mention: str = "") -> str:
    prefix = [mention] if mention else []
    if alert_type == "critical":
        heading = "[HERMES CRITICAL] FUNDS TOO LOW"
        action = "Action: pause live trading or add funds now"
    elif alert_type == "warning":
        heading = "[HERMES WARNING] LOW FUNDS"
        action = "Action: add funds or reduce order size"
    elif alert_type == "resolved":
        return "\n".join(
            [
                *prefix,
                "[HERMES RESOLVED] FUNDS BACK ABOVE LIMIT",
                "",
                f"Quote: {snapshot.quote}",
                f"Available: {_money(snapshot.available)}",
                f"Resolve limit: {_money(snapshot.resolve_limit)}",
            ]
        )
    else:
        raise FundsAlertConfigError(f"unsupported funds alert type={alert_type!r}")
    return "\n".join(
        [
            *prefix,
            heading,
            "",
            f"Quote: {snapshot.quote}",
            f"Available: {_money(snapshot.available)}",
            f"Low limit: {_money(snapshot.low_limit)}",
            f"Critical limit: {_money(snapshot.critical_limit)}",
            "",
            action,
        ]
    )


_SEVERITY_RANK = {"ok": 0, "warning": 1, "critical": 2}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _load_alert_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _write_alert_state(path: Path, snapshot: FundsSnapshot, status: str, now: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "status": status,
                "last_alert_at": now.isoformat(),
                "last_available": snapshot.available,
                "low_limit": snapshot.low_limit,
                "critical_limit": snapshot.critical_limit,
                "resolve_limit": snapshot.resolve_limit,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _cooldown_elapsed(state: dict[str, Any], now: datetime, cooldown_seconds: int) -> bool:
    raw = state.get("last_alert_at")
    if not raw:
        return True
    try:
        last_alert_at = _ensure_utc(datetime.fromisoformat(str(raw)))
    except ValueError:
        return True
    return (now - last_alert_at).total_seconds() >= cooldown_seconds


def build_funds_alert_message(
    state_path: Path,
    settings: FundsAlertSettings,
    snapshot: FundsSnapshot,
    now: datetime | None = None,
) -> str | None:
    if not settings.enabled:
        return None
    current_time = _ensure_utc(now or _utc_now())
    state = _load_alert_state(state_path)
    previous_status = str(state.get("status") or "ok")

    if snapshot.severity == "ok":
        if previous_status in {"warning", "critical"} and snapshot.available >= snapshot.resolve_limit:
            _write_alert_state(state_path, snapshot, "ok", current_time)
            return format_funds_alert(snapshot, "resolved", mention="")
        return None

    if _SEVERITY_RANK[snapshot.severity] > _SEVERITY_RANK.get(previous_status, 0):
        _write_alert_state(state_path, snapshot, snapshot.severity, current_time)
        return format_funds_alert(snapshot, snapshot.severity, settings.mention)

    if previous_status == snapshot.severity and _cooldown_elapsed(state, current_time, settings.cooldown_seconds):
        _write_alert_state(state_path, snapshot, snapshot.severity, current_time)
        return format_funds_alert(snapshot, snapshot.severity, settings.mention)

    if previous_status == "ok":
        _write_alert_state(state_path, snapshot, snapshot.severity, current_time)
        return format_funds_alert(snapshot, snapshot.severity, settings.mention)

    return None


def extract_available_funds(balance: dict[str, Any], quote: str) -> float:
    free = balance.get("free") or {}
    if quote in free and free[quote] is not None:
        return float(free[quote])

    result = ((balance.get("info") or {}).get("result") or {})
    entry = result.get(quote)
    if isinstance(entry, dict):
        total = float(entry.get("balance", 0.0) or 0.0)
        credit = float(entry.get("credit", 0.0) or 0.0)
        credit_used = float(entry.get("credit_used", 0.0) or 0.0)
        hold_trade = float(entry.get("hold_trade", 0.0) or 0.0)
        return total + credit - credit_used - hold_trade

    return 0.0


async def fetch_available_funds(settings: FundsAlertSettings, exchange_factory=None) -> float:
    exchange = exchange_factory() if exchange_factory else _default_exchange()
    try:
        balance = await exchange.fetch_balance()
        return extract_available_funds(balance, settings.quote)
    finally:
        await exchange.close()


async def build_due_funds_alert(
    state_dir: Path,
    settings: FundsAlertSettings,
    now: datetime | None = None,
    exchange_factory=None,
) -> str | None:
    if not settings.enabled:
        return None
    available = await fetch_available_funds(settings, exchange_factory=exchange_factory)
    snapshot = build_funds_snapshot(settings, available=available)
    return build_funds_alert_message(state_dir / "funds_alert.json", settings, snapshot, now)


def build_due_funds_alert_from_balances(
    state_dir: Path,
    settings: FundsAlertSettings,
    balances: Mapping[str, Decimal],
    now: datetime | None = None,
    *,
    exchange_min_cost: Decimal | None = None,
) -> str | None:
    """Build an alert from the cycle's canonical balance snapshot.

    Live orchestration must not perform a second ad-hoc balance request merely
    to notify. Missing quote data is left visible to the risk gate rather than
    being silently converted to zero.
    """
    if not settings.enabled:
        return None
    available = balances.get(settings.quote)
    if available is None:
        return None
    snapshot = build_funds_snapshot(
        settings,
        available=float(available),
        exchange_min_cost=(float(exchange_min_cost) if exchange_min_cost is not None else None),
    )
    return build_funds_alert_message(
        state_dir / "funds_alert.json", settings, snapshot, now
    )
