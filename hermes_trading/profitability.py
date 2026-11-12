from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from hermes_trading.costs import CostEvent, CostLedger, RailwayCostSource


TRADING_COST_CATEGORIES = {"kraken_fee", "slippage"}
OPERATING_COST_CATEGORIES = {"railway_worker", "railway_bot", "hermes", "openai"}


@dataclass(frozen=True)
class ProfitabilityReport:
    gross_pnl: Decimal
    trading_costs: Decimal
    operating_costs: Decimal
    net_pnl: Decimal
    capital: Decimal
    days: Decimal
    projected_net_30d: Decimal
    break_even_capital: Decimal | None
    operating_coverage: Decimal
    stressed_net_pnl: Decimal
    missing_required_costs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EconomicGate:
    passed: bool
    reasons: tuple[str, ...]


class ProfitabilityService:
    def __init__(self, ledger: CostLedger | None = None) -> None:
        self.ledger = ledger

    def calculate(
        self,
        closed_positions: Iterable[dict[str, Any]],
        *,
        capital: Decimal,
        days: Decimal,
        supplemental_costs: Iterable[CostEvent] = (),
    ) -> ProfitabilityReport:
        if self.ledger is None:
            raise ValueError("profitability calculation requires a cost ledger")
        gross_pnl = sum(
            (
                Decimal(str(position.get("realized_pnl", "0")))
                for position in closed_positions
                if str(position.get("status", "closed")) == "closed"
            ),
            Decimal("0"),
        )
        costs = self.ledger.costs_between()
        missing = set(self.ledger.missing_categories())
        for event in supplemental_costs:
            if event.currency == "USD" and event.amount is not None:
                costs[event.category] = costs.get(event.category, Decimal("0")) + event.amount
            if event.amount is None or event.quality == "missing":
                missing.add(event.category)
        return self.calculate_from_values(
            gross_pnl=gross_pnl,
            costs=costs,
            missing_required_costs=tuple(missing),
            capital=capital,
            days=days,
        )

    @staticmethod
    def calculate_from_values(
        *,
        gross_pnl: Decimal,
        costs: dict[str, Decimal],
        capital: Decimal,
        days: Decimal,
        missing_required_costs: tuple[str, ...] = (),
    ) -> ProfitabilityReport:
        trading_costs = _sum_categories(costs, TRADING_COST_CATEGORIES)
        operating_costs = _sum_categories(costs, OPERATING_COST_CATEGORIES)
        net_pnl = gross_pnl - trading_costs - operating_costs
        projected_net_30d = net_pnl * Decimal("30") / days if days > 0 else Decimal("0")
        break_even_capital = _break_even_capital(capital, gross_pnl, operating_costs)
        operating_coverage = _ratio(gross_pnl - trading_costs, operating_costs)
        stressed_gross = gross_pnl * Decimal("0.8")
        stressed_costs = (trading_costs + operating_costs) * Decimal("1.25")
        stressed_net_pnl = stressed_gross - stressed_costs
        return ProfitabilityReport(
            gross_pnl=gross_pnl,
            trading_costs=trading_costs,
            operating_costs=operating_costs,
            net_pnl=net_pnl,
            capital=capital,
            days=days,
            projected_net_30d=projected_net_30d,
            break_even_capital=break_even_capital,
            operating_coverage=operating_coverage,
            stressed_net_pnl=stressed_net_pnl,
            missing_required_costs=tuple(sorted(missing_required_costs)),
        )


def economic_gate(report: ProfitabilityReport) -> EconomicGate:
    reasons: list[str] = []
    for category in report.missing_required_costs:
        reasons.append(f"{category} cost missing")
    if report.projected_net_30d <= 0:
        reasons.append("projected net 30d is not positive")
    if report.operating_coverage < Decimal("1.5"):
        reasons.append("operating coverage is below 1.5")
    if report.stressed_net_pnl <= 0:
        reasons.append("stress net pnl is not positive")
    return EconomicGate(not reasons, tuple(reasons))


def profitability_gate_snapshot(report: ProfitabilityReport) -> dict[str, Any]:
    """Return the concise economic decision included in the worker heartbeat."""
    gate = economic_gate(report)
    return {
        "status": "passed" if gate.passed else "blocked",
        "reasons": list(gate.reasons),
        "net_pnl_usd": _money(report.net_pnl),
        "projected_net_30d_usd": _money(report.projected_net_30d),
        "stressed_net_pnl_usd": _money(report.stressed_net_pnl),
    }


def render_profitability_markdown(report: ProfitabilityReport) -> str:
    gate = economic_gate(report)
    verdict = "PASS" if gate.passed else "BLOCKED"
    reasons = "; ".join(gate.reasons) if gate.reasons else "economic gate passed"
    return (
        "Hermes Trading | Profitability\n"
        f"Gross: {_money(report.gross_pnl)} USD | Trading costs: {_money(report.trading_costs)} USD | "
        f"Ops+AI: {_money(report.operating_costs)} USD\n"
        f"Net: {_money(report.net_pnl)} USD | 30d proj: {_money(report.projected_net_30d)} USD | "
        f"Coverage: {_money(report.operating_coverage)}x\n"
        f"Stress: {_money(report.stressed_net_pnl)} USD | Gate: {verdict} | {reasons}"
    )


def write_profitability_artifacts(state_dir: Path, report: ProfitabilityReport) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "profitability.json").write_text(
        json.dumps(_report_json(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (state_dir / "profitability_report.md").write_text(
        render_profitability_markdown(report) + "\n",
        encoding="utf-8",
    )


def update_profitability_artifacts(
    state_dir: Path,
    *,
    capital: Decimal,
    days: Decimal,
) -> ProfitabilityReport:
    positions_path = state_dir / "positions.json"
    if positions_path.exists():
        positions = json.loads(positions_path.read_text(encoding="utf-8") or "[]")
    else:
        positions = []
    if not isinstance(positions, list):
        positions = []
    railway_cost = RailwayCostSource.from_env().cost_for_window(
        days=days,
        month_days=Decimal("30"),
    )
    report = ProfitabilityService(CostLedger(state_dir)).calculate(
        positions,
        capital=capital,
        days=days,
        supplemental_costs=(railway_cost,),
    )
    write_profitability_artifacts(state_dir, report)
    return report


def _sum_categories(costs: dict[str, Decimal], categories: set[str]) -> Decimal:
    return sum((Decimal(str(amount)) for category, amount in costs.items() if category in categories), Decimal("0"))


def _break_even_capital(capital: Decimal, gross_pnl: Decimal, operating_costs: Decimal) -> Decimal | None:
    if operating_costs <= 0:
        return Decimal("0")
    try:
        gross_return = gross_pnl / capital
    except (DivisionByZero, InvalidOperation):
        return None
    if gross_return <= 0:
        return None
    return operating_costs / gross_return


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("999")
    return numerator / denominator


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}".rstrip("0").rstrip(".") or "0"


def _report_json(report: ProfitabilityReport) -> dict[str, Any]:
    return {
        "gross_pnl": str(report.gross_pnl),
        "trading_costs": str(report.trading_costs),
        "operating_costs": str(report.operating_costs),
        "net_pnl": str(report.net_pnl),
        "capital": str(report.capital),
        "days": str(report.days),
        "projected_net_30d": str(report.projected_net_30d),
        "break_even_capital": str(report.break_even_capital) if report.break_even_capital is not None else None,
        "operating_coverage": str(report.operating_coverage),
        "stressed_net_pnl": str(report.stressed_net_pnl),
        "missing_required_costs": list(report.missing_required_costs),
    }


__all__ = [
    "EconomicGate",
    "ProfitabilityReport",
    "ProfitabilityService",
    "economic_gate",
    "profitability_gate_snapshot",
    "render_profitability_markdown",
    "update_profitability_artifacts",
    "write_profitability_artifacts",
]
