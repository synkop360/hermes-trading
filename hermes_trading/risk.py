from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import os
from typing import Callable, Mapping, Protocol

from hermes_trading.config import ExecutionQualityLimits, resolve_execution_quality_limits
from hermes_trading.domain import json_value
from hermes_trading.persistence.repository import StateRepository


class RiskProjector(Protocol):
    def append_event(self, name: str, payload: object) -> None: ...

    def write_snapshot(self, name: str, payload: object) -> None: ...


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: Decimal = Decimal("0.0025")
    max_symbol_exposure_pct: Decimal = Decimal("0.05")
    max_total_exposure_pct: Decimal = Decimal("0.10")
    max_daily_loss_pct: Decimal = Decimal("0.005")
    max_drawdown_pct: Decimal = Decimal("0.03")
    max_order_quote: Decimal = Decimal("5")
    max_spread_age_seconds: int = 300

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "max_spread_age_seconds":
                continue
            _positive_fraction(value, name, allow_one=name == "max_order_quote")
        if self.max_order_quote <= 0:
            raise ValueError("max_order_quote must be positive")
        if self.max_spread_age_seconds <= 0:
            raise ValueError("max_spread_age_seconds must be positive")


@dataclass(frozen=True)
class RiskContext:
    symbol: str
    equity: Decimal | None
    free_quote: Decimal | None
    symbol_exposure: Decimal | None
    total_exposure: Decimal | None
    daily_pnl: Decimal | None
    drawdown: Decimal | None
    spread_bps: Decimal | None
    spread_available: bool
    spread_observed_at: datetime | None
    active_position: bool = False

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        for name in (
            "equity", "free_quote", "symbol_exposure", "total_exposure", "daily_pnl", "drawdown", "spread_bps",
        ):
            value = getattr(self, name)
            if value is not None:
                _finite_decimal(value, name)
        if self.spread_observed_at is not None and self.spread_observed_at.tzinfo is None:
            raise ValueError("spread_observed_at must include a timezone")


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason_code: str
    notional: Decimal | None
    decided_at: str

    def __post_init__(self) -> None:
        if self.notional is not None:
            _finite_decimal(self.notional, "notional")


class RiskEngine:
    """Fail-closed pre-trade authorization with a durable portfolio switch.

    ``stop_loss_pct`` is a fractional loss (``0.01`` means 1%).  Training
    strategy YAML currently stores percent points, so its caller must divide
    that value by 100 before invoking this boundary.
    """

    def __init__(
        self,
        repository: StateRepository,
        projector: RiskProjector | None = None,
        *,
        limits: RiskLimits | None = None,
        quality_limits: Callable[[str], ExecutionQualityLimits] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.projector = projector
        self.limits = limits or RiskLimits()
        self._quality_limits = quality_limits or (lambda symbol: resolve_execution_quality_limits(symbol=symbol))
        self._now = now or (lambda: datetime.now(UTC))

    def authorize_entry(
        self,
        *,
        context: RiskContext,
        stop_loss_pct: Decimal,
        position_size_r: Decimal,
        exchange_min_cost: Decimal,
    ) -> RiskDecision:
        now = self._utc_now()
        reason = self._entry_block_reason(
            context, stop_loss_pct, position_size_r, exchange_min_cost, now
        )
        if reason is not None:
            if reason in {"daily_loss_limit", "drawdown_limit"}:
                self.activate_kill_switch(reason)
            return self._record_decision(False, reason, None, now)

        assert context.equity is not None
        assert context.free_quote is not None
        assert context.symbol_exposure is not None
        assert context.total_exposure is not None
        risk_budget = context.equity * self.limits.risk_per_trade_pct * position_size_r
        risk_sized = risk_budget / stop_loss_pct
        notional = min(
            risk_sized,
            self.limits.max_order_quote,
            context.equity * self.limits.max_symbol_exposure_pct - context.symbol_exposure,
            context.equity * self.limits.max_total_exposure_pct - context.total_exposure,
            context.free_quote,
        )
        if notional <= 0:
            return self._record_decision(False, "exposure_limit", None, now)
        if notional < exchange_min_cost:
            return self._record_decision(False, "below_exchange_minimum", None, now)
        return self._record_decision(True, "authorized", notional, now)

    def activate_kill_switch(self, reason: str) -> None:
        self.repository.set_risk_state(
            kill_switch=True,
            reason=reason,
            updated_at=self._utc_now(),
        )
        self._project_status(None)

    def resume(self, *, authorized: bool) -> bool:
        now = self._utc_now()
        state = self.repository.risk_state()
        since = _timestamp_or_epoch(state.updated_at)
        may_resume = (
            authorized
            and not self.repository.has_unresolved_incidents()
            and self.repository.has_fresh_reconciliation(
                since=since,
                now=now,
                max_age_seconds=self.limits.max_spread_age_seconds,
            )
        )
        if not may_resume:
            self._project_status(None)
            return False
        self.repository.set_risk_state(kill_switch=False, reason="resumed", updated_at=now)
        self._project_status(None)
        return True

    def _entry_block_reason(
        self,
        context: RiskContext,
        stop_loss_pct: Decimal,
        position_size_r: Decimal,
        exchange_min_cost: Decimal,
        now: datetime,
    ) -> str | None:
        if not _valid_positive(stop_loss_pct):
            return "invalid_stop_loss"
        if not _valid_positive(position_size_r) or position_size_r > Decimal("1"):
            return "invalid_position_size_r"
        if not _valid_positive(exchange_min_cost):
            return "invalid_exchange_minimum"
        balances = (
            context.equity,
            context.free_quote,
            context.symbol_exposure,
            context.total_exposure,
            context.daily_pnl,
            context.drawdown,
        )
        if any(value is None for value in balances):
            return "unknown_balance"
        non_negative_balances = (
            context.equity,
            context.free_quote,
            context.symbol_exposure,
            context.total_exposure,
            context.drawdown,
        )
        if any(value < 0 for value in non_negative_balances if value is not None):
            return "invalid_balance"
        if context.active_position:
            return "active_position"
        if self.repository.risk_state().kill_switch:
            return "kill_switch_active"
        if self.repository.has_unresolved_incidents():
            return "unresolved_incident"
        assert context.equity is not None and context.daily_pnl is not None and context.drawdown is not None
        if context.daily_pnl <= -(context.equity * self.limits.max_daily_loss_pct):
            return "daily_loss_limit"
        if context.drawdown >= self.limits.max_drawdown_pct:
            return "drawdown_limit"
        if not context.spread_available or context.spread_bps is None:
            return "spread_unavailable"
        if context.spread_observed_at is None:
            return "spread_stale"
        observed_at = context.spread_observed_at.astimezone(UTC)
        if now - observed_at > timedelta(seconds=self.limits.max_spread_age_seconds):
            return "spread_stale"
        quality = self._quality_limits(context.symbol)
        if context.spread_bps > quality.max_spread_bps:
            return "spread_limit"
        return None

    def _record_decision(
        self, allowed: bool, reason_code: str, notional: Decimal | None, now: datetime
    ) -> RiskDecision:
        decision = RiskDecision(allowed, reason_code, notional, now.isoformat())
        self._project_status(decision)
        return decision

    def _project_status(self, decision: RiskDecision | None) -> None:
        if self.projector is None:
            return
        if decision is not None:
            self.projector.append_event("risk_decisions.jsonl", decision)
        state = self.repository.risk_state()
        self.projector.write_snapshot(
            "risk_status.json",
            {
                "state": {
                    "kill_switch": state.kill_switch,
                    "reason": state.reason,
                    "revision": state.revision,
                    "updated_at": state.updated_at,
                },
                "open_incident_count": int(self.repository.has_unresolved_incidents()),
                "last_decision": json_value(decision) if decision is not None else None,
            },
        )

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("risk clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def resolve_risk_limits(env: Mapping[str, str] | None = None) -> RiskLimits:
    values = os.environ if env is None else env
    return RiskLimits(
        risk_per_trade_pct=_env_decimal(values, "HERMES_TRADING_RISK_PER_TRADE_PCT", Decimal("0.0025")),
        max_symbol_exposure_pct=_env_decimal(values, "HERMES_TRADING_MAX_SYMBOL_EXPOSURE_PCT", Decimal("0.05")),
        max_total_exposure_pct=_env_decimal(values, "HERMES_TRADING_MAX_TOTAL_EXPOSURE_PCT", Decimal("0.10")),
        max_daily_loss_pct=_env_decimal(values, "HERMES_TRADING_MAX_DAILY_LOSS_PCT", Decimal("0.005")),
        max_drawdown_pct=_env_decimal(values, "HERMES_TRADING_MAX_DRAWDOWN_PCT", Decimal("0.03")),
        max_order_quote=_env_decimal(values, "HERMES_TRADING_MAX_ORDER_USDT", Decimal("5")),
    )


def _env_decimal(values: Mapping[str, str], name: str, default: Decimal) -> Decimal:
    raw = values.get(name)
    if raw is None:
        return default
    if isinstance(raw, float):
        raise ValueError(f"{name} must not be a binary float")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite Decimal") from error
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _positive_fraction(value: Decimal, name: str, *, allow_one: bool) -> None:
    _finite_decimal(value, name)
    if value <= 0 or (not allow_one and value >= 1):
        raise ValueError(f"{name} must be greater than zero and less than one")


def _finite_decimal(value: Decimal, name: str) -> None:
    if isinstance(value, float) or not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _valid_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _timestamp_or_epoch(value: str | None) -> datetime:
    if value is None:
        return datetime(1970, 1, 1, tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["RiskContext", "RiskDecision", "RiskEngine", "RiskLimits", "resolve_risk_limits"]
