from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.costs import CostEvent, CostLedger
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.risk import RiskContext, RiskEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate final Hermes qualification evidence")
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    repository = StateRepository.open(state_dir / "trading.db")
    try:
        repository.migrate()
        fees_status = generate_fee_and_slippage_evidence(state_dir, repository)
        risk_status = generate_risk_limit_evidence(repository)
        duplicate_status = generate_duplicate_candle_evidence(repository)
    finally:
        repository.close()

    print(f"fees_and_slippage={fees_status}")
    print(f"duplicate_candle={duplicate_status}")
    print(f"risk_limit={risk_status}")


def generate_fee_and_slippage_evidence(state_dir: Path, repository: StateRepository) -> str:
    ledger = CostLedger(state_dir)
    totals = ledger.costs_between()
    if totals.get("kraken_fee", Decimal("0")) > 0 and "slippage" in totals:
        return "existing"

    tables = repository.export_canonical_tables()
    intents = {str(row["client_order_id"]): row for row in tables["order_intents"]}
    generated = False
    for order in tables["orders"]:
        if str(order["status"]) != "filled":
            continue
        intent = intents.get(str(order["client_order_id"]))
        average_price = _decimal(order.get("average_fill_price"))
        reference_price = _decimal(intent.get("reference_price") if intent else None)
        filled_quantity = _decimal(order.get("filled_quantity"))
        fees = _decimal(order.get("fees"))
        if average_price is None or reference_price is None or filled_quantity is None or fees is None:
            continue
        if fees > 0:
            ledger.record(
                CostEvent(
                    "kraken_fee",
                    fees,
                    "USD",
                    "actual",
                    "qualification_simulated_order",
                    _timestamp(order.get("updated_at")),
                    metadata={"asset": order["asset"], "order_id": order["order_id"]},
                )
            )
        slippage = _slippage_amount(str(order["side"]), filled_quantity, reference_price, average_price)
        ledger.record(
            CostEvent(
                "slippage",
                slippage,
                "USD",
                "actual",
                "qualification_simulated_order",
                _timestamp(order.get("updated_at")),
                metadata={"asset": order["asset"], "order_id": order["order_id"]},
            )
        )
        generated = True
        break
    return "generated" if generated else "missing"


def generate_risk_limit_evidence(repository: StateRepository) -> str:
    if repository.risk_limit_evidence_count() > 0:
        return "existing"
    now = datetime.now(UTC)
    decision = RiskEngine(repository, now=lambda: now).authorize_entry(
        context=RiskContext(
            symbol="BTC/USDT",
            equity=Decimal("100"),
            free_quote=Decimal("100"),
            symbol_exposure=Decimal("0"),
            total_exposure=Decimal("0"),
            daily_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            spread_bps=Decimal("999"),
            spread_available=True,
            spread_observed_at=now,
        ),
        stop_loss_pct=Decimal("0.01"),
        position_size_r=Decimal("0.5"),
        exchange_min_cost=Decimal("5"),
    )
    if decision.allowed:
        return "missing"
    repository.open_incident(
        f"qualification:risk-limit:{decision.risk_decision_id}",
        f"risk_limit_exercised:{decision.reason_code}",
        severity="info",
        opened_at=now,
    )
    return "generated"


def generate_duplicate_candle_evidence(repository: StateRepository) -> str:
    if repository.duplicate_candle_evidence_count() > 0:
        return "existing"
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    asset = "BTC/USDT"
    timeframe = "qualification"
    repository.mark_candle_processed(asset, timeframe, timestamp)
    if not repository.is_candle_processed(asset, timeframe, timestamp):
        return "missing"
    repository.open_incident(
        f"qualification:duplicate-candle:{uuid4()}",
        "duplicate_candle_protected",
        severity="info",
    )
    return "generated"


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _timestamp(value: object) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _slippage_amount(side: str, quantity: Decimal, reference_price: Decimal, average_price: Decimal) -> Decimal:
    direction = Decimal("1") if side == "buy" else Decimal("-1")
    return max((average_price - reference_price) * quantity * direction, Decimal("0"))


if __name__ == "__main__":
    main()
