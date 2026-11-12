from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


CostQuality = str


@dataclass(frozen=True)
class CostEvent:
    category: str
    amount: Decimal | None
    currency: str
    quality: CostQuality
    source: str
    occurred_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CostLedger:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.events_path = state_dir / "cost_events.jsonl"
        self.projection_path = state_dir / "costs.json"

    def record(self, event: CostEvent) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_event_json(event), separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        from hermes_trading.paper_publication import _sync_dir
        _sync_dir(self.state_dir)
        self._project()

    def costs_between(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        currency: str = "USD",
    ) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for event in self._events():
            if event.currency != currency or event.amount is None:
                continue
            if start is not None and event.occurred_at < start:
                continue
            if end is not None and event.occurred_at >= end:
                continue
            totals[event.category] = totals.get(event.category, Decimal("0")) + event.amount
        return totals

    def missing_categories(self) -> list[str]:
        return sorted({event.category for event in self._events() if event.amount is None or event.quality == "missing"})

    def _project(self) -> None:
        totals = self.costs_between()
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "currency": "USD",
            "totals": {category: str(amount) for category, amount in sorted(totals.items())},
            "missing": self.missing_categories(),
        }
        self.projection_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _events(self) -> list[CostEvent]:
        if not self.events_path.exists():
            return []
        events: list[CostEvent] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(_event_from_json(json.loads(line)))
        return events


class MonthlyCostApi(Protocol):
    def monthly_cost_usd(self) -> Decimal: ...


class ConfiguredMonthlyCostSource:
    def __init__(self, *, monthly_cost: Decimal | None, allocation_pct: Decimal = Decimal("100"), category: str = "railway_worker") -> None:
        self.monthly_cost = monthly_cost
        self.allocation_pct = allocation_pct
        self.category = category

    def cost_for_window(
        self,
        *,
        days: Decimal,
        month_days: Decimal,
        now: datetime | None = None,
    ) -> CostEvent:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self.monthly_cost is None:
            return CostEvent(self.category, None, "USD", "missing", "configured_monthly", current)
        amount = self.monthly_cost * (self.allocation_pct / Decimal("100")) * (days / month_days)
        return CostEvent(self.category, amount, "USD", "estimated", "configured_monthly", current)


class RailwayCostSource:
    def __init__(self, *, api: MonthlyCostApi | None = None, fallback_monthly: Decimal | None = None, category: str = "railway_worker") -> None:
        self.api = api
        self.fallback_monthly = fallback_monthly
        self.category = category

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, category: str = "railway_worker") -> RailwayCostSource:
        values = dict(os.environ if env is None else env)
        monthly = _optional_decimal(values.get("HERMES_RAILWAY_MONTHLY_COST_USD"))
        allocation = _optional_decimal(values.get("HERMES_RAILWAY_SUBSCRIPTION_ALLOCATION_PCT")) or Decimal("100")
        if monthly is not None:
            monthly = monthly * (allocation / Decimal("100"))
        return cls(fallback_monthly=monthly, category=category)

    def cost_for_window(
        self,
        *,
        days: Decimal,
        month_days: Decimal,
        now: datetime | None = None,
    ) -> CostEvent:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self.api is not None:
            try:
                monthly = self.api.monthly_cost_usd()
                amount = monthly * (days / month_days)
                return CostEvent(self.category, amount, "USD", "actual", "railway_api", current)
            except Exception:
                pass
        return ConfiguredMonthlyCostSource(
            monthly_cost=self.fallback_monthly,
            category=self.category,
        ).cost_for_window(days=days, month_days=month_days, now=current)


def openai_cost_event(
    *,
    input_tokens: int,
    output_tokens: int,
    model: str,
    pricing: dict[str, str],
    occurred_at: datetime | None = None,
) -> CostEvent:
    return openai_usage_event(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        pricing=pricing,
        occurred_at=occurred_at,
    )


def openai_usage_event(
    *,
    input_tokens: int,
    output_tokens: int,
    model: str,
    pricing: dict[str, str] | None,
    occurred_at: datetime | None = None,
) -> CostEvent:
    metadata: dict[str, Any] = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if pricing is None:
        return CostEvent(
            "openai",
            None,
            "USD",
            "missing",
            "responses_api",
            (occurred_at or datetime.now(UTC)).astimezone(UTC),
            metadata=metadata,
        )
    input_cost = Decimal(str(input_tokens)) * Decimal(str(pricing["input_per_1m"])) / Decimal("1000000")
    output_cost = Decimal(str(output_tokens)) * Decimal(str(pricing["output_per_1m"])) / Decimal("1000000")
    metadata["pricing_version"] = pricing["version"]
    return CostEvent(
        "openai",
        input_cost + output_cost,
        "USD",
        "actual",
        "responses_api",
        (occurred_at or datetime.now(UTC)).astimezone(UTC),
        metadata=metadata,
    )


def hermes_usage_event(
    *,
    provider: str,
    model: str | None,
    free: bool,
    usage: dict[str, Any],
    occurred_at: datetime | None = None,
) -> CostEvent:
    amount = Decimal("0") if free else None
    return CostEvent(
        "hermes",
        amount,
        "USD",
        "actual" if free else "missing",
        "hermes_usage",
        (occurred_at or datetime.now(UTC)).astimezone(UTC),
        metadata={
            "provider": provider,
            "model": model,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
    )


def kraken_fee_event(
    *,
    amount: Decimal,
    currency: str | None,
    asset: str,
    order_id: str,
    occurred_at: datetime | None = None,
) -> CostEvent:
    original_currency = currency or "USD"
    if original_currency not in {"USD", "USDT", "USDC"}:
        return CostEvent(
            "kraken_fee",
            None,
            "USD",
            "missing",
            "kraken_unconverted_fee",
            (occurred_at or datetime.now(UTC)).astimezone(UTC),
            metadata={
                "asset": asset,
                "order_id": order_id,
                "fee_currency": original_currency,
                "fee_amount": str(amount),
                "reason": "fee currency requires an FX conversion",
            },
        )
    return CostEvent(
        "kraken_fee",
        amount,
        "USD",
        "actual",
        "kraken",
        (occurred_at or datetime.now(UTC)).astimezone(UTC),
        metadata={"asset": asset, "order_id": order_id, "fee_currency": original_currency},
    )


def slippage_cost_event(
    *,
    side: str,
    quantity: Decimal,
    reference_price: Decimal,
    average_price: Decimal,
    asset: str,
    order_id: str,
    occurred_at: datetime | None = None,
) -> CostEvent:
    direction = Decimal("1") if side == "buy" else Decimal("-1")
    raw = (average_price - reference_price) * quantity * direction
    amount = max(raw, Decimal("0"))
    return CostEvent(
        "slippage",
        amount,
        "USD",
        "actual",
        "kraken_fill_vs_decision",
        (occurred_at or datetime.now(UTC)).astimezone(UTC),
        metadata={
            "asset": asset,
            "order_id": order_id,
            "side": side,
            "quantity": str(quantity),
            "reference_price": str(reference_price),
            "average_price": str(average_price),
        },
    )


def _event_json(event: CostEvent) -> dict[str, Any]:
    return {
        "category": event.category,
        "amount": str(event.amount) if event.amount is not None else None,
        "currency": event.currency,
        "quality": event.quality,
        "source": event.source,
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat(),
        "period_start": event.period_start.astimezone(UTC).isoformat() if event.period_start else None,
        "period_end": event.period_end.astimezone(UTC).isoformat() if event.period_end else None,
        "metadata": event.metadata,
    }


def _event_from_json(payload: dict[str, Any]) -> CostEvent:
    return CostEvent(
        category=str(payload["category"]),
        amount=Decimal(str(payload["amount"])) if payload.get("amount") is not None else None,
        currency=str(payload["currency"]),
        quality=str(payload["quality"]),
        source=str(payload["source"]),
        occurred_at=datetime.fromisoformat(str(payload["occurred_at"])).astimezone(UTC),
        period_start=_optional_datetime(payload.get("period_start")),
        period_end=_optional_datetime(payload.get("period_end")),
        metadata=dict(payload.get("metadata") or {}),
    )


def _optional_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)).astimezone(UTC) if value else None


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return Decimal(str(value))


__all__ = [
    "ConfiguredMonthlyCostSource",
    "CostEvent",
    "CostLedger",
    "RailwayCostSource",
    "hermes_usage_event",
    "kraken_fee_event",
    "openai_cost_event",
    "openai_usage_event",
    "slippage_cost_event",
]
