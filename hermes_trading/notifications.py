from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from hermes_trading.funds import _ensure_utc
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


def build_inprocess_reflection_notification(before_version: str, after_strategy: dict[str, Any]) -> str:
    after_version = str(after_strategy.get("version", before_version))
    if after_version == before_version:
        return f"Hermes Trading | In-process reflection: no change (version {before_version})"
    return f"Hermes Trading | In-process reflection: strategy v{before_version} -> v{after_version}"


def build_worker_feature_update(feature: str, detail: str, *, status: str = "available") -> str:
    return f"Hermes Trading | Worker feature {status} {feature}: {detail}"


class WorkerFeatureUpdateLedger:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "worker_feature_updates.json"

    def pending_message(self, feature: str, detail: str) -> str | None:
        markers = _load_summary_markers(self._path)
        if markers.get(feature):
            return None
        return build_worker_feature_update(feature, detail)

    def record_delivered(self, feature: str, *, now: datetime) -> None:
        markers = _load_summary_markers(self._path)
        if markers.get(feature):
            return
        markers[feature] = now.isoformat()
        self._path.write_text(json.dumps(markers, indent=2, sort_keys=True), encoding="utf-8")


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


_PAPER_ACCOUNTS = tuple(
    f"{arm}_{size}" for size in (50, 100, 150, 200) for arm in ("adaptive", "control")
)
_PAPER_IDENTITY_FIELDS = ("experiment_id", "opening_at", "opening_hash", "manifest_hash")


def _paper_text(value: Any, limit: int = 150) -> str:
    """Bound untrusted strings and make mentions inert even outside Discord."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=True, default=str)
    return str(value).replace("@", "@\u200b").replace("\n", " ").replace("\r", " ")[:limit]


def _paper_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and len(str(value)) <= 40 and -40 <= result.adjusted() <= 40 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _paper_money(value: Any) -> str:
    result = _paper_decimal(value)
    return "unknown" if result is None else f"EUR {result}"


class PaperDiscordReporter:
    """Optional, bounded observer of copied native paper settlement data.

    ``offer`` only touches memory. All delivery and metadata IO belongs to
    ``run``; no repository, strategy, financial lease or history is opened here.
    Injected posters have the same trusted completion contract as PostJson.
    """

    def __init__(self, state_dir, identity, settings, *, post_json=None, clock=None,
                 sleep=asyncio.sleep, attempt_timeout=10):
        self.settings = settings
        self._identity = ({k: identity.get(k) for k in (*_PAPER_IDENTITY_FIELDS, "primary_wallet_eur")}
                          | {"account_hashes": dict(identity.get("account_hashes", {}))}) if isinstance(identity, dict) and isinstance(identity.get("account_hashes"), dict) else {}
        self._path = Path(state_dir) / "paper_discord_reporter.json"
        self._post_json = post_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._timeout = min(10.0, max(.001, float(attempt_timeout)))
        self._events = deque()
        self._pending_batches = deque()
        self._offered_batches = deque(maxlen=128)
        self._batch_fingerprints = {}
        self._latest = None
        self._settled_latest = None
        self._snapshot = None
        self._current = None
        self._offered_state = None
        self._wake = asyncio.Event()
        self.counters = {name: 0 for name in (
            "coalesced", "dropped", "invalid", "oversized", "delivery_unknown",
            "gaps", "repeated_states", "omitted_events", "restarts")}
        self.status = "disabled"
        self.enabled = False
        self._state = {}
        self._io_task = None
        try:
            valid_identity = (
                all(isinstance(self._identity.get(k), str) and 0 < len(self._identity[k]) <= 200 for k in _PAPER_IDENTITY_FIELDS)
                and self._identity.get("primary_wallet_eur") == 200
                and set(self._identity.get("account_hashes", {})) == set(_PAPER_ACCOUNTS)
                and all(isinstance(v, str) and 0 < len(v) <= 128 for v in self._identity["account_hashes"].values())
            )
            url = urlsplit(settings.webhook_url or "")
            valid_url = (url.scheme == "https" and url.hostname in {"discord.com", "discordapp.com"}
                         and url.port in {None, 443} and not url.username and not url.password
                         and re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[^/]+", url.path))
            valid_settings = (settings.service == "discord" and settings.mode in {"verbose", "pnl_only", "orders_only"}
                              and type(settings.summary_every_cycles) is int and settings.summary_every_cycles >= 0)
            for frequency in (*settings.summary_frequencies, *settings.profitability_frequencies):
                _current_period_start(frequency, datetime.now(timezone.utc))
            self.enabled = bool(settings.enabled and valid_settings and valid_identity and valid_url)
            self.status = "ready" if self.enabled else "configuration_invalid" if settings.enabled else "disabled"
        except (AttributeError, ValueError, TypeError, NotificationConfigError, OverflowError):
            self.status = "configuration_invalid"

    def offer(self, envelope):
        """Copy at most 128KiB; never wait, touch disk or start a task."""
        if not self.enabled:
            return
        try:
            chunks, size = [], 0
            for chunk in json.JSONEncoder(ensure_ascii=True, allow_nan=False).iterencode(envelope):
                size += len(chunk)
                if size > 128 * 1024:
                    self.counters["oversized"] += 1
                    self.counters["gaps"] += 1
                    return
                chunks.append(chunk)
            value = json.loads("".join(chunks))
            if not self._valid_envelope(value):
                self.counters["invalid"] += 1
                return
            settled = value["kind"] == "settled"
            if settled:
                batch = value["batch_id"]
                fingerprint = self._tag([value.get(k) for k in ("input_hash", "observed_at", "completed_batches")] + [value["accounts"]])
                if batch in self._batch_fingerprints and self._batch_fingerprints[batch] != fingerprint:
                    self.counters["invalid"] += 1
                    self.counters["gaps"] += 1
                    return
                if batch not in self._offered_batches:
                    if len(self._offered_batches) == 128:
                        self._batch_fingerprints.pop(self._offered_batches[0], None)
                    self._offered_batches.append(batch)
                    self._batch_fingerprints[batch] = fingerprint
                    if len(self._pending_batches) == 128:
                        self._pending_batches.popleft()
                        self.counters["gaps"] += 1
                    self._pending_batches.append((batch, value["completed_batches"]))
                self._settled_latest = value
            state = self._state_signature(value)
            has_events = settled and any(row.get("closes") or row.get("orders") for row in value["accounts"].values() if isinstance(row, dict))
            if has_events or (self._offered_state is not None and state != self._offered_state):
                if len(self._events) == 16:
                    self._events.popleft()
                    self.counters["dropped"] += 1
                    self.counters["gaps"] += 1
                self._events.append(value)
            elif state == self._offered_state:
                self.counters["repeated_states"] += 1
            self._offered_state = state
            if self._latest is not None:
                self.counters["coalesced"] += 1
            self._latest = value
            self._wake.set()
        except (ValueError, TypeError, RecursionError, OverflowError, AttributeError):
            self.counters["invalid"] += 1

    def _valid_envelope(self, value):
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("kind") not in {"settled", "operational"}:
            return False
        if any(value.get(k) != self._identity[k] for k in _PAPER_IDENTITY_FIELDS):
            return False
        try:
            at = datetime.fromisoformat(value["at"])
            if at.tzinfo is None:
                return False
        except (ValueError, TypeError, KeyError):
            return False
        accounts = value.get("accounts")
        if not isinstance(accounts, dict):
            return False
        if value["kind"] == "operational":
            return not accounts or set(accounts) == set(_PAPER_ACCOUNTS)
        try:
            observed = datetime.fromisoformat(value["observed_at"])
            if observed.tzinfo is None or observed > at:
                return False
        except (ValueError, TypeError, KeyError):
            return False
        return (set(accounts) == set(_PAPER_ACCOUNTS)
                and isinstance(value.get("batch_id"), str) and 0 < len(value["batch_id"]) <= 200
                and isinstance(value.get("input_hash"), str) and 0 < len(value["input_hash"]) <= 200
                and type(value.get("completed_batches")) is int and value["completed_batches"] > 0)

    @staticmethod
    def _state_signature(value):
        data = json.dumps([value.get(k) for k in ("status", "entry_admission", "model_ready", "strategy_version", "error")], sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def _load(self):
        initial = {"schema_version": 1, "identity": self._identity, "delivered": [], "events": [],
                   "abandoned": [], "cadences": {}, "new_batches": 0, "last_completed": 0,
                   "startup": False, "pending": None, "last_observed_at": None, "state_signature": None}
        try:
            if self._path.exists():
                if self._path.stat().st_size > 64 * 1024:
                    raise ValueError("oversized metadata")
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("identity") != self._identity:
                    raise ValueError("metadata identity")
                if set(data) != set(initial) | {"counters"} or type(data.get("startup")) is not bool:
                    raise ValueError("metadata shape")
                if any(not isinstance(data.get(k), list) or len(data[k]) > 256 or any(not isinstance(v, str) or len(v) > 200 for v in data[k]) for k in ("delivered", "events", "abandoned")):
                    raise ValueError("metadata markers")
                if any(type(data.get(k)) is not int or data[k] < 0 for k in ("last_completed", "new_batches")):
                    raise ValueError("metadata counters")
                if not isinstance(data.get("cadences"), dict) or not isinstance(data.get("counters"), dict):
                    raise ValueError("metadata schedules")
                if set(data["counters"]) != set(self.counters) or any(type(v) is not int or v < 0 for v in data["counters"].values()):
                    raise ValueError("metadata counters")
                if data["pending"] is not None and (not isinstance(data["pending"], str) or not re.fullmatch(r"[0-9a-f]{24}:[0-3]", data["pending"])):
                    raise ValueError("metadata pending")
                if data["state_signature"] is not None and (not isinstance(data["state_signature"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["state_signature"])):
                    raise ValueError("metadata state")
                for key, value in data["cadences"].items():
                    if not isinstance(key, str) or not isinstance(value, str):
                        raise ValueError("metadata schedule")
                    if key in {"cycle", "heartbeat"}:
                        if not value.isdecimal() or len(value) > 20:
                            raise ValueError("metadata schedule count")
                    elif re.fullmatch(r"(?:operational|financial):[1-9]\d*(?:h|d|w|mo)", key):
                        if datetime.fromisoformat(value).tzinfo is None:
                            raise ValueError("metadata schedule timezone")
                    else:
                        raise ValueError("metadata schedule key")
                if data["last_observed_at"] is not None and datetime.fromisoformat(data["last_observed_at"]).tzinfo is None:
                    raise ValueError("metadata observation")
                initial.update(data)
                for key in self.counters:
                    self.counters[key] += data["counters"].get(key, 0)
                self.counters["restarts"] += 1
                self.counters["gaps"] += 1
                if initial["pending"]:
                    self.counters["delivery_unknown"] += 1
                self.status = "resumed_coverage_unknown"
            self._state = initial
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.enabled = False
            self.status = "metadata_invalid"
            return False

    def _persist(self):
        try:
            self._state["counters"] = dict(self.counters)
            for key in ("delivered", "events", "abandoned"):
                self._state[key] = self._state[key][-256:]
            encoded = json.dumps(self._state, ensure_ascii=True, sort_keys=True)
            if len(encoded.encode("utf-8")) > 64 * 1024:
                raise ValueError("metadata bound")
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(encoded, encoding="utf-8")
            tmp.replace(self._path)
            return True
        except (OSError, ValueError, TypeError):
            self.enabled = False
            self.status = "metadata_unavailable"
            return False

    async def _save(self):
        if self._io_task is not None and not self._io_task.done():
            # An OS write cannot be cancelled. Never start a competing writer.
            self.enabled = False
            self.status = "metadata_io_pending"
            return False
        self._io_task = asyncio.create_task(asyncio.to_thread(self._persist))
        return await asyncio.shield(self._io_task)

    def _consume_batches(self):
        while self._pending_batches:
            _, ordinal = self._pending_batches.popleft()
            if ordinal > self._state["last_completed"]:
                if self._state["last_completed"] and ordinal > self._state["last_completed"] + 1:
                    self.counters["gaps"] += ordinal - self._state["last_completed"] - 1
                self._state["last_completed"] = ordinal
                self._state["new_batches"] += 1

    async def run(self):
        if not self.enabled or not await asyncio.to_thread(self._load):
            return
        try:
            while self.enabled:
                try:
                    async with asyncio.timeout(.5):
                        await self._wake.wait()
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                latest, self._latest = self._latest, None
                if latest is not None:
                    self._current = latest
                    if self._settled_latest is not None:
                        self._snapshot = self._settled_latest
                    self._consume_batches()
                while self.enabled and self._events:
                    value = self._events.popleft()
                    await self._deliver_events(value)
                if self.enabled and self._current is not None:
                    await self._due_summary()
                if self.enabled and latest is not None:
                    self._state["last_observed_at"] = self._current.get("at")
                    await self._save()
        except asyncio.CancelledError:
            if self._state.get("pending"):
                self.counters["delivery_unknown"] += 1
                self.counters["gaps"] += 1
                self.status = "delivery_unknown"
            try:
                async with asyncio.timeout(.4):
                    if self._io_task is not None and not self._io_task.done():
                        await asyncio.shield(self._io_task)
                    self._consume_batches()
                    await self._save()
            except TimeoutError:
                pass
            raise
        except Exception as exc:
            # Notification-only containment. Never expose a webhook/response body.
            self.status = "reporter_error:" + type(exc).__name__
            self.enabled = False
            await self._save()

    def _valid_row(self, name, value):
        row = value.get("accounts", {}).get(name)
        if not isinstance(row, dict):
            return None
        observation = row.get("observation")
        if isinstance(observation, dict) and (observation.get("account_config_hash") != row.get("account_hash")
                                             or observation.get("at") != row.get("observed_at")):
            return None
        return row if (row.get("account_hash") == self._identity["account_hashes"][name]
                       and row.get("finalized") is True and row.get("observed_at") == value.get("observed_at")) else None

    def _header(self, value, label):
        return [f"Hermes paper/simulated | {label}", f"Observed at: {_paper_text(value.get('at'))}",
                f"Last settled: {_paper_text((self._snapshot or {}).get('observed_at', 'unavailable'))}",
                f"Observed interval: {_paper_text(self._state.get('last_observed_at') or 'unknown')} -> {_paper_text(value.get('at'))}; coverage unknown",
                f"status={_paper_text(value.get('status'))} entry_admission={_paper_text(value.get('entry_admission'))}",
                f"model readiness/access={_paper_text(value.get('model_ready'))}; review audit unavailable (healthy can mean not_due)",
                f"strategy_version={_paper_text(value.get('strategy_version'))}",
                "Coverage unknown; gaps=" + str(self.counters["gaps"]) +
                f" coalesced={self.counters['coalesced']} dropped={self.counters['dropped']}"
                f" delivery_unknown={self.counters['delivery_unknown']} repeats={self.counters['repeated_states']}",
                "Retries may duplicate; stable tags identify parts/events."]

    def _summary(self, labels):
        lines = self._header(self._current, ", ".join(labels))
        lines.append("current financial snapshot; period-end valuation unavailable; interval P&L/Sharpe unavailable.")
        lines.append("primary EUR200; all amounts native EUR net of recorded costs (no extra fee subtraction).")
        financial = self._snapshot or {}
        equities = {}
        for name in _PAPER_ACCOUNTS:
            row = self._valid_row(name, financial)
            obs = row.get("observation") if row else None
            if not isinstance(obs, dict) or obs.get("currency") != "EUR":
                lines.append(f"{name}: unavailable")
                continue
            equities[name] = _paper_decimal(obs.get("net_equity"))
            lines.append(f"{name}: " + " ".join(f"{k}={_paper_money(obs.get(k))}" for k in (
                "net_equity", "closed_net_pnl", "open_basis", "open_liquidation_value", "open_entry_fees", "known_accrued_costs")))
            decisions = row.get("decisions") if isinstance(row.get("decisions"), list) else []
            reasons = {}
            for decision in decisions[:128]:
                if isinstance(decision, dict):
                    key = _paper_text(decision.get("action"), 30) + ":" + _paper_text(decision.get("reason"), 45)
                    reasons[key] = reasons.get(key, 0) + 1
            lines.append(f" costs={_paper_text(obs.get('cost_provenance', 'unknown'), 64)} missing={_paper_text(obs.get('missing', 'unknown'), 80)}"
                         f" halted={_paper_text(row.get('halted'), 8)} liquidation_pending={_paper_text(row.get('liquidation_pending'), 8)}"
                         f" events={_paper_text(row.get('event_source_status', 'unavailable'), 24)} decisions={len(decisions)} reasons={_paper_text(reasons, 80)}")
        for size in (50, 100, 150, 200):
            a, c = equities.get(f"adaptive_{size}"), equities.get(f"control_{size}")
            lines.append(f"adaptive minus control {size}: " + (_paper_money(a - c) if a is not None and c is not None else "unavailable"))
        if self._current.get("error"):
            # Source error strings can contain credentials. Publish only its existence.
            lines.append("Worker error present; details withheld.")
        return lines

    async def _due_summary(self):
        now = _ensure_utc(self._clock())
        reasons, changes = [], {}
        cadence = self._state["cadences"]
        if not self._state["startup"]:
            reasons.append("startup current snapshot")
        cycles = self.settings.summary_every_cycles
        cycle = self._state["new_batches"] // cycles if cycles else 0
        if cycle and cycle > int(cadence.get("cycle", "0")):
            reasons.append(f"{cycles} batches")
            changes["cycle"] = str(cycle)
        for label, frequencies in (("operational", self.settings.summary_frequencies), ("financial", self.settings.profitability_frequencies)):
            for frequency in frequencies:
                key = f"{label}:{frequency}"
                period = _current_period_start(frequency, now).isoformat()
                if key in cadence and cadence[key] != period:
                    reasons.append(f"{label} {frequency}")
                    changes[key] = period
                elif key not in cadence:
                    # Installation starts from today's observation, never historical replay.
                    cadence[key] = period
        has_cadence = cycles or self.settings.summary_frequencies or self.settings.profitability_frequencies
        if self.settings.notify_heartbeat and not has_cadence:
            period = str(int(now.timestamp()) // 900)
            if "heartbeat" in cadence and cadence["heartbeat"] != period:
                reasons.append("15-minute liveness")
                changes["heartbeat"] = period
            elif "heartbeat" not in cadence:
                cadence["heartbeat"] = period
        if not reasons:
            return
        startup_identity = [self._current.get(k) for k in ("kind", "batch_id", "input_hash", "at")]
        key = "summary:" + ("startup:" + self._tag(startup_identity) if not self._state["startup"] else self._tag(changes))
        if await self._deliver(key, self._summary(reasons)):
            self._state["startup"] = True
            cadence.update(changes)
            self._state["last_observed_at"] = self._current.get("at")
            self._state["state_signature"] = self._state_signature(self._current)
            await self._save()

    @staticmethod
    def _tag(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:24]

    async def _deliver_events(self, value):
        entries, identities = [], []
        include_closes = self.settings.notify_trades and self.settings.mode in {"pnl_only", "verbose"}
        include_orders = self.settings.notify_orders and self.settings.mode in {"orders_only", "verbose"}
        for name in _PAPER_ACCOUNTS:
            row = self._valid_row(name, value)
            if not row or row.get("event_source_status") != "observed":
                continue
            for kind, enabled in (("closes", include_closes), ("orders", include_orders)):
                events = row.get(kind)
                if not enabled or not isinstance(events, list):
                    continue
                for event in events[:256]:
                    if not isinstance(event, dict):
                        continue
                    if kind == "closes":
                        fields = [name, event.get("asset"), event.get("position_id"), event.get("closed_at")]
                        if event.get("account", name) != name or not all(isinstance(v, str) and v for v in fields):
                            continue
                        if _paper_decimal(event.get("quantity")) is None or _paper_decimal(event.get("realized_pnl_eur")) is None:
                            continue
                        content = (f"Close {name} {_paper_text(event['asset'], 35)} position={_paper_text(event['position_id'], 65)}"
                                   f" closed_at={_paper_text(event['closed_at'], 40)} quantity={_paper_text(event['quantity'], 45)}"
                                   f" realized_pnl={_paper_money(event['realized_pnl_eur'])}")
                    else:
                        canonical = event.get("order_id") or event.get("canonical_id")
                        if not isinstance(canonical, str) or not canonical:
                            continue
                        fields = [name, canonical]
                        content = (f"Order {_paper_text(event.get('status'), 30)} {name} {_paper_text(event.get('side'), 15)}"
                                   f" {_paper_text(event.get('asset'), 35)} quantity={_paper_text(event.get('quantity'), 45)}")
                    tag = self._tag([kind, fields])
                    if tag not in identities and tag not in self._state["events"]:
                        identities.append(tag)
                        entries.append(content + f" [event:{tag}]")
                if len(events) > 256:
                    self.counters["omitted_events"] += len(events) - 256
                    self.counters["gaps"] += 1
        signature = self._state_signature(value)
        if self._state["state_signature"] is not None and signature != self._state["state_signature"]:
            if not value.get("error") or self.settings.notify_errors:
                entries.insert(0, "Operational state change; " + " ".join(self._header(value, "status change")[3:6]))
                identities.append("state:" + self._tag(signature))
        if not entries:
            return
        key = "events:" + self._tag(identities)
        if await self._deliver(key, self._header(value, "observed events") + entries):
            self._state["events"].extend(identities)
            self._state["state_signature"] = signature
            await self._save()

    def _parts(self, key, lines):
        # Reserve enough room for stable identity and omitted-event disclosure.
        chunks, current, omitted = [], "", 0
        for index, line in enumerate(lines):
            line = _paper_text(line, 1500)
            if len(current) + len(line) + 1 > 1600:
                chunks.append(current)
                current = ""
                if len(chunks) == 4:
                    omitted = len(lines) - index
                    break
            current += ("\n" if current else "") + line
        if current and len(chunks) < 4:
            chunks.append(current)
        if omitted:
            self.counters["omitted_events"] += omitted
            self.counters["gaps"] += 1
            chunks[-1] += f"\nOmitted {omitted} excess lines/events; coverage gap."
        tag = self._tag(key)
        timestamp = next((line for line in lines if line.startswith("Observed at:")), "Observed at: unavailable")
        prefix = "Hermes paper/simulated | " + timestamp + "\n"
        return [(f"{tag}:{i}", prefix + chunk + f"\n[paper:{tag} part:{i + 1}/{len(chunks)}]") for i, chunk in enumerate(chunks)]

    async def _deliver(self, key, lines):
        if key in self._state["abandoned"]:
            return False
        parts = self._parts(key, lines)
        for attempt in range(3):
            try:
                # One timeout covers the entire multipart attempt, including IO.
                async with asyncio.timeout(self._timeout):
                    for marker, content in parts:
                        if marker in self._state["delivered"]:
                            continue
                        self._state["pending"] = marker
                        if not await self._save():
                            return False
                        await send_notification(self.settings, content, post_json=self._confirmed_post)
                        self._state["delivered"].append(marker)
                        self._state["pending"] = None
                        if not await self._save():
                            self.counters["delivery_unknown"] += 1
                            return False
                self.status = "delivered"
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = 2 ** attempt
                if self._io_task is not None and not self._io_task.done():
                    self.enabled = False
                    self.status = "metadata_io_pending"
                    self.counters["gaps"] += 1
                    return False
                if isinstance(exc, httpx.HTTPStatusError):
                    code = exc.response.status_code
                    # A non-success response confirms rejection, not dispatch ambiguity.
                    self._state["pending"] = None
                    if code in {401, 403, 404}:
                        self.enabled = False
                        self.status = f"delivery_disabled_http_{code}"
                        self.counters["gaps"] += 1
                        await self._save()
                        return False
                    if code == 429:
                        try:
                            delay = float(exc.response.headers.get("Retry-After") or exc.response.json().get("retry_after", delay))
                            if not 0 <= delay <= 3600:
                                delay = 2 ** attempt
                        except (ValueError, TypeError, AttributeError):
                            pass
                if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or self._state["pending"]:
                    self.counters["delivery_unknown"] += 1
                    self.status = "delivery_unknown"
                else:
                    self.status = "delivery_failed:" + type(exc).__name__
                self.counters["gaps"] += 1
                await self._save()
                if not self.enabled:
                    return False
                if attempt < 2:
                    await self._sleep(delay)
        self._state["abandoned"].append(key)
        await self._save()
        return False

    async def _confirmed_post(self, url, payload):
        parsed = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "wait"]
        query.append(("wait", "true"))
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
        safe_payload = dict(payload, allowed_mentions={"parse": []})
        if self._post_json is not None:
            await self._post_json(target, safe_payload)
            return
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            response = await client.post(target, json=safe_payload)
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise NotificationConfigError("unconfirmed Discord response") from exc
            if not isinstance(body, dict) or not isinstance(body.get("id"), str) or not body["id"]:
                raise NotificationConfigError("unconfirmed Discord response")
