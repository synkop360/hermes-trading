from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from hermes_trading.profitability import ProfitabilityReport, render_profitability_markdown
from hermes_trading.qualification_runner import QualificationReport


class NotificationConfigError(RuntimeError):
    """Raised when notifications are enabled but incomplete."""


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool
    service: str
    webhook_url: str | None
    username: str
    mode: str
    notify_trades: bool
    notify_orders: bool
    notify_errors: bool
    notify_heartbeat: bool
    summary_every_cycles: int
    summary_frequencies: tuple[str, ...] = ()
    profitability_frequencies: tuple[str, ...] = ()


PostJson = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class PnlBucket:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    flat: int = 0
    pnl: float = 0.0

    def add(self, pnl: float) -> None:
        self.trades += 1
        self.pnl += pnl
        if pnl > 0:
            self.wins += 1
        elif pnl < 0:
            self.losses += 1
        else:
            self.flat += 1


@dataclass
class PnlSummaryCounter:
    settings: NotificationSettings
    cycles: int = 0
    total: PnlBucket = field(default_factory=PnlBucket)
    by_asset: dict[str, PnlBucket] = field(default_factory=dict)

    def record_cycle(self, trades: list[dict[str, Any]]) -> str | None:
        if self.settings.summary_every_cycles <= 0:
            return None
        self.cycles += 1
        for trade in trades:
            asset = str(trade.get("asset"))
            pnl = float(trade.get("realized_pnl_pct", 0.0) or 0.0)
            self.total.add(pnl)
            self.by_asset.setdefault(asset, PnlBucket()).add(pnl)
        if self.cycles < self.settings.summary_every_cycles:
            return None
        message = self._message()
        self.reset()
        return message

    def reset(self) -> None:
        self.cycles = 0
        self.total = PnlBucket()
        self.by_asset = {}

    def _message(self) -> str:
        asset_parts = []
        for asset in sorted(self.by_asset):
            bucket = self.by_asset[asset]
            asset_parts.append(
                f"{asset}: trades={bucket.trades} wins={bucket.wins} "
                f"losses={bucket.losses} flat={bucket.flat} pnl={_fmt_float(bucket.pnl)}"
            )
        detail = "; ".join(asset_parts) if asset_parts else "no trades"
        return (
            f"Hermes Trading | P&L summary {self.cycles} cycles "
            f"trades={self.total.trades} wins={self.total.wins} losses={self.total.losses} "
            f"flat={self.total.flat} total_pnl={_fmt_float(self.total.pnl)} | {detail}"
        )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _fmt_float(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


def _fmt_percent(value: float) -> str:
    return f"{value * 100:.4f}%"


def _normalize_mode(value: str | None) -> str:
    mode = (value or "verbose").strip().lower().replace("-", "_")
    aliases = {
        "verbose": "verbose",
        "all": "verbose",
        "order_only": "orders_only",
        "orders_only": "orders_only",
        "orders": "orders_only",
        "gain_loss_only": "pnl_only",
        "gain_losse_only": "pnl_only",
        "gain_losses_only": "pnl_only",
        "gains_losses_only": "pnl_only",
        "pnl_only": "pnl_only",
        "pnl": "pnl_only",
    }
    if mode not in aliases:
        raise NotificationConfigError("HERMES_NOTIFY_MODE must be verbose, orders_only, or pnl_only")
    return aliases[mode]


def _int_at_least_zero(value: str | None) -> int:
    parsed = int(value or "0")
    if parsed < 0:
        raise NotificationConfigError("HERMES_NOTIFY_SUMMARY_EVERY_CYCLES must be 0 or greater")
    return parsed


def _normalize_summary_frequencies(value: str | None) -> tuple[str, ...]:
    raw = (value or "").strip().lower()
    if raw in {"", "0", "false", "off", "none", "disabled"}:
        return ()
    aliases = {
        "hourly": "1h",
        "hour": "1h",
        "daily": "1d",
        "day": "1d",
        "weekly": "1w",
        "week": "1w",
        "monthly": "1mo",
        "month": "1mo",
        "1month": "1mo",
    }
    normalized_raw = raw.strip("[]()")
    parts = [part for part in re.split(r"[\s,;]+", normalized_raw) if part]
    frequencies = []
    seen = set()
    for part in parts:
        normalized = aliases.get(part, part)
        match = re.fullmatch(r"([1-9]\d*)(h|d|w|mo)", normalized)
        if not match:
            raise NotificationConfigError(
                "HERMES_NOTIFY_SUMMARY_FREQUENCIES must use values like 12h, 1d, 1w, or 1mo"
            )
        frequency = f"{int(match.group(1))}{match.group(2)}"
        if frequency not in seen:
            frequencies.append(frequency)
            seen.add(frequency)
    return tuple(frequencies)


def resolve_notification_settings(env: dict[str, str] | None = None) -> NotificationSettings:
    values = env if env is not None else os.environ
    enabled = _truthy(values.get("HERMES_NOTIFY_ENABLED"))
    service = values.get("HERMES_NOTIFY_SERVICE", "discord").strip().lower() or "discord"
    webhook_url = values.get("DISCORD_WEBHOOK_URL") or values.get("HERMES_DISCORD_WEBHOOK_URL")
    username = values.get("HERMES_NOTIFY_USERNAME", "Hermes Trading").strip() or "Hermes Trading"
    mode = _normalize_mode(values.get("HERMES_NOTIFY_MODE"))

    if service != "discord":
        raise NotificationConfigError("only discord notifications are supported in this version")
    if enabled and not webhook_url:
        raise NotificationConfigError("discord notifications require DISCORD_WEBHOOK_URL")

    return NotificationSettings(
        enabled=enabled,
        service=service,
        webhook_url=webhook_url,
        username=username,
        mode=mode,
        notify_trades=_truthy(values.get("HERMES_NOTIFY_ON_TRADES", "true")),
        notify_orders=_truthy(values.get("HERMES_NOTIFY_ON_ORDERS", "true")),
        notify_errors=_truthy(values.get("HERMES_NOTIFY_ON_ERRORS", "true")),
        notify_heartbeat=_truthy(values.get("HERMES_NOTIFY_ON_HEARTBEAT")),
        summary_every_cycles=_int_at_least_zero(values.get("HERMES_NOTIFY_SUMMARY_EVERY_CYCLES")),
        summary_frequencies=_normalize_summary_frequencies(values.get("HERMES_NOTIFY_SUMMARY_FREQUENCIES")),
        profitability_frequencies=_normalize_summary_frequencies(
            values.get("HERMES_NOTIFY_PROFITABILITY_FREQUENCIES")
        ),
    )


async def _httpx_post_json(url: str, payload: dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def send_notification(
    settings: NotificationSettings,
    content: str,
    post_json: PostJson | None = None,
) -> bool:
    if not settings.enabled:
        return False
    if not settings.webhook_url:
        raise NotificationConfigError("discord notifications require DISCORD_WEBHOOK_URL")

    poster = post_json or _httpx_post_json
    await poster(settings.webhook_url, {"content": content, "username": settings.username})
    return True


def build_cycle_notifications(
    trades: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    settings: NotificationSettings | None = None,
) -> list[str]:
    effective = settings or resolve_notification_settings({})
    messages = []
    include_trades = effective.notify_trades and effective.mode in {"verbose", "pnl_only"}
    include_orders = effective.notify_orders and effective.mode in {"verbose", "orders_only"}
    if include_trades:
        for trade in trades:
            pnl = float(trade.get("realized_pnl_pct", 0.0) or 0.0)
            if effective.mode == "pnl_only" and pnl == 0.0:
                continue
            messages.append(_format_trade_message(trade))
    if include_orders:
        for order in orders:
            messages.append(_format_order_message(order))
    return messages


def _format_trade_message(trade: dict[str, Any]) -> str:
    pnl = float(trade.get("realized_pnl_pct", 0.0) or 0.0)
    outcome = "GAIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT"
    details = [
        f"strategy={trade.get('strategy_version')}",
        f"mode={trade.get('mode')}",
    ]
    if trade.get("entry_value") is not None:
        details.append(f"rsi={trade.get('entry_value')}")
    if trade.get("threshold") is not None:
        details.append(f"threshold={trade.get('threshold')}")
    return (
        f"Hermes Trading | P&L {outcome} {trade.get('asset')} "
        f"{_fmt_percent(pnl)} (raw={_fmt_float(pnl)}) " + " ".join(details)
    )


def _format_order_message(order: dict[str, Any]) -> str:
    status = order.get("status")
    side = order.get("side")
    asset = order.get("asset")
    if status == "simulated":
        details = [f"reference_price={order.get('reference_price')}"]
        if order.get("position_size_r") is not None:
            details.append(f"position_size_r={order.get('position_size_r')}")
        return f"Hermes Trading | Virtual order simulated {side} {asset} " + " ".join(details)

    details = []
    if order.get("amount") is not None:
        details.append(f"amount={order.get('amount')}")
    if order.get("validate_only") is not None:
        details.append(f"validate_only={order.get('validate_only')}")
    suffix = " " + " ".join(details) if details else ""
    return f"Hermes Trading | Order {status} {side} {asset}{suffix}"


def build_error_notification(error: Exception, consecutive_failures: int) -> str:
    return f"Hermes Trading | Worker error {consecutive_failures}/5: {error}"


def build_profitability_notification(report: ProfitabilityReport) -> str:
    return render_profitability_markdown(report)


def build_qualification_notification(report: QualificationReport) -> str:
    promotable = "true" if report.snapshot.promotable else "false"
    return "\n".join(
        [
            report.markdown,
            f"Run: {report.run_id}",
            f"Promotable: {promotable}",
        ]
    )


def build_worker_feature_update(feature: str, detail: str, *, status: str = "available") -> str:
    return f"Hermes Trading | Worker feature {status} {feature}: {detail}"


class WorkerFeatureUpdateLedger:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "worker_feature_updates.json"

    def record_once(self, feature: str, detail: str, *, now: datetime) -> str | None:
        markers = _load_summary_markers(self._path)
        if markers.get(feature):
            return None
        markers[feature] = now.isoformat()
        self._path.write_text(json.dumps(markers, indent=2, sort_keys=True), encoding="utf-8")
        return build_worker_feature_update(feature, detail)


def build_due_profitability_notifications(
    state_dir: Path,
    report: ProfitabilityReport,
    settings: NotificationSettings | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Build one economic snapshot for each completed, not-yet-notified period."""
    effective = settings or resolve_notification_settings({})
    if not effective.enabled or not effective.profitability_frequencies:
        return []

    current_time = _ensure_utc(now or datetime.now(timezone.utc))
    marker_path = state_dir / "notification_profitability_summaries.json"
    markers = _load_summary_markers(marker_path)
    messages = []
    changed = False
    for frequency in effective.profitability_frequencies:
        period_start, period_end = _previous_completed_period(frequency, current_time)
        marker_key = f"{frequency}:{period_start.isoformat()}"
        if markers.get(frequency) == marker_key:
            continue
        messages.append(
            "\n".join(
                [
                    f"Hermes Trading | Profitability snapshot {frequency}",
                    f"As of: {period_end.isoformat()}",
                    "----------------------------------------",
                    build_profitability_notification(report),
                ]
            )
        )
        markers[frequency] = marker_key
        changed = True

    if changed:
        marker_path.write_text(json.dumps(markers, indent=2, sort_keys=True), encoding="utf-8")
    return messages


def build_due_periodic_summaries(
    state_dir: Path,
    settings: NotificationSettings | None = None,
    now: datetime | None = None,
) -> list[str]:
    effective = settings or resolve_notification_settings({})
    if not effective.summary_frequencies:
        return []

    current_time = _ensure_utc(now or datetime.now(timezone.utc))
    marker_path = state_dir / "notification_summaries.json"
    markers = _load_summary_markers(marker_path)
    messages = []
    changed = False
    for frequency in effective.summary_frequencies:
        period_start, period_end = _previous_completed_period(frequency, current_time)
        marker_key = f"{frequency}:{period_start.isoformat()}"
        if markers.get(frequency) == marker_key:
            continue
        messages.append(_build_periodic_summary_message(state_dir, frequency, period_start, period_end))
        markers[frequency] = marker_key
        changed = True

    if changed:
        marker_path.write_text(json.dumps(markers, indent=2, sort_keys=True), encoding="utf-8")
    return messages


def build_summary_notifications(
    state_dir: Path,
    trades: list[dict[str, Any]],
    settings: NotificationSettings,
    pnl_summary: PnlSummaryCounter,
    now: datetime | None = None,
) -> list[str]:
    messages = []
    cycle_summary = pnl_summary.record_cycle(trades)
    if cycle_summary:
        messages.append(cycle_summary)
    if settings.enabled:
        messages.extend(build_due_periodic_summaries(state_dir, settings, now))
    return messages


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _previous_completed_period(frequency: str, now: datetime) -> tuple[datetime, datetime]:
    current_start = _current_period_start(frequency, now)
    previous_start = _shift_period_start(frequency, current_start, -1)
    return previous_start, current_start


def _current_period_start(frequency: str, now: datetime) -> datetime:
    match = re.fullmatch(r"([1-9]\d*)(h|d|w|mo)", frequency)
    if not match:
        raise NotificationConfigError("invalid summary frequency")
    count = int(match.group(1))
    unit = match.group(2)
    base = now.replace(minute=0, second=0, microsecond=0)
    if unit == "h":
        hour = (base.hour // count) * count
        return base.replace(hour=hour)
    if unit == "d":
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        days = (base.date() - epoch.date()).days
        start_days = days - (days % count)
        return epoch + timedelta(days=start_days)
    if unit == "w":
        week_start = (base - timedelta(days=base.weekday())).replace(hour=0)
        if count == 1:
            return week_start
        epoch_monday = datetime(1970, 1, 5, tzinfo=timezone.utc)
        weeks = (week_start.date() - epoch_monday.date()).days // 7
        return epoch_monday + timedelta(weeks=weeks - (weeks % count))
    month_index = base.year * 12 + base.month - 1
    start_month_index = month_index - (month_index % count)
    year = start_month_index // 12
    month = start_month_index % 12 + 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _shift_period_start(frequency: str, start: datetime, direction: int) -> datetime:
    match = re.fullmatch(r"([1-9]\d*)(h|d|w|mo)", frequency)
    if not match:
        raise NotificationConfigError("invalid summary frequency")
    count = int(match.group(1)) * direction
    unit = match.group(2)
    if unit == "h":
        return start + timedelta(hours=count)
    if unit == "d":
        return start + timedelta(days=count)
    if unit == "w":
        return start + timedelta(weeks=count)
    month_index = start.year * 12 + start.month - 1 + count
    year = month_index // 12
    month = month_index % 12 + 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _load_summary_markers(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _read_jsonl_between(path: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            timestamp_value = next(
                (
                    record.get(field)
                    for field in ("timestamp", "decided_at", "created_at", "filled_at", "closed_at", "opened_at")
                    if record.get(field) is not None
                ),
                None,
            )
            timestamp = _ensure_utc(datetime.fromisoformat(str(timestamp_value)))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if start <= timestamp < end:
            records.append(record)
    return records


def _build_periodic_summary_message(state_dir: Path, frequency: str, start: datetime, end: datetime) -> str:
    decisions = _read_jsonl_between(state_dir / "decisions.jsonl", start, end)
    trades = _read_jsonl_between(state_dir / "trades.jsonl", start, end)
    orders = _read_jsonl_between(state_dir / "orders.jsonl", start, end)
    trade_bucket = PnlBucket()
    by_asset: dict[str, PnlBucket] = {}
    for trade in trades:
        asset = str(trade.get("asset"))
        pnl = _canonical_pnl_pct(trade)
        trade_bucket.add(pnl)
        by_asset.setdefault(asset, PnlBucket()).add(pnl)

    no_trade = sum(
        1
        for decision in decisions
        if decision.get("action") == "no_trade"
        or ("action" not in decision and not decision.get("trade_closed"))
    )
    order_statuses: dict[str, int] = {}
    for order in orders:
        status = str(order.get("status") or "unknown")
        order_statuses[status] = order_statuses.get(status, 0) + 1

    order_detail = (
        ",".join(f"{status}:{count}" for status, count in sorted(order_statuses.items()))
        if order_statuses
        else "none"
    )
    symbol_lines = [
        f"{asset} | trades={bucket.trades} wins={bucket.wins} losses={bucket.losses} "
        f"flat={bucket.flat} pnl={_fmt_float(bucket.pnl)}"
        for asset, bucket in sorted(by_asset.items())
    ] or ["No closed trades"]
    return "\n".join(
        [
            f"Hermes Trading | Periodic summary {frequency}",
            f"Window: {start.isoformat()} -> {end.isoformat()}",
            "----------------------------------------",
            f"Decisions: total={len(decisions)} no_trade={no_trade}",
            f"Trades: total={trade_bucket.trades} wins={trade_bucket.wins} "
            f"losses={trade_bucket.losses} flat={trade_bucket.flat} total_pnl={_fmt_float(trade_bucket.pnl)}",
            f"Orders: total={len(orders)} by_status={order_detail}",
            "----------------------------------------",
            "By symbol:",
            *symbol_lines,
        ]
    )


def _canonical_pnl_pct(trade: dict[str, Any]) -> float:
    if trade.get("realized_pnl_pct") is not None:
        return float(trade["realized_pnl_pct"] or 0)
    try:
        cost = Decimal(str(trade["quantity"])) * Decimal(str(trade["entry_price"]))
        return float(Decimal(str(trade["realized_pnl"])) / cost) if cost else 0.0
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return 0.0
