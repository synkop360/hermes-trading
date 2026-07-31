from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from typing import Literal

from hermes_trading.domain import (
    Decision,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Position,
    PositionStatus,
)
from hermes_trading.exchange import ExchangeGateway, ExchangeOrder
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.risk import RiskDecision


ExecutionStatus = Literal["submitted", "reconciliation_required", "rejected"]


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    client_order_id: str | None
    reason: str | None = None
    exchange_order: ExchangeOrder | None = None


def client_order_id(asset: str, side: str, candle_id: str, strategy_version: str) -> str:
    """Build the stable, ASCII client id used to reconcile one order intent."""
    fields = (asset, side, candle_id, strategy_version)
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise ValueError("client order id fields must be non-empty strings")
    try:
        encoded = f"{asset}|{side}|{candle_id}|{strategy_version}".encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("client order id fields must be ASCII") from error
    digest = hashlib.blake2s(encoded, digest_size=8).hexdigest()
    return f"ht-{digest}"


class ExecutionService:
    """Crash-safe order intent boundary.

    The service is deliberately dormant until a later loop migration supplies
    a gateway.  Its only submission paths are exercised with test gateways in
    this task; production execution remains guarded by the existing facade.
    """

    def __init__(self, repository: StateRepository, *, allow_submission: bool = False) -> None:
        self.repository = repository
        self._allow_submission = allow_submission

    async def submit_entry(
        self,
        signal: Decision,
        risk_decision: RiskDecision,
        gateway: ExchangeGateway,
    ) -> ExecutionResult:
        if signal.action != "enter":
            raise ValueError("entry submission requires an enter decision")
        if not risk_decision.allowed:
            return ExecutionResult("rejected", None, risk_decision.reason_code)
        if not self._allow_submission:
            return ExecutionResult("rejected", None, "submission_disabled")
        notional = _positive_decimal(risk_decision.notional, "risk notional")
        reference_price = _positive_decimal(signal.reference_price, "reference price")
        return await self._submit_market(
            signal,
            gateway,
            side="buy",
            quantity=notional / reference_price,
            position_id=None,
        )

    async def submit_exit(
        self, signal: Decision, position: Position, gateway: ExchangeGateway
    ) -> ExecutionResult:
        if signal.action != "exit":
            raise ValueError("exit submission requires an exit decision")
        self._validate_position(signal, position)
        if not self._allow_submission:
            return ExecutionResult("rejected", None, "submission_disabled")
        return await self._submit_market(
            signal,
            gateway,
            side="sell",
            quantity=_positive_decimal(position.quantity, "position quantity"),
            position_id=position.position_id,
        )

    async def ensure_protective_stop(
        self, signal: Decision, position: Position, gateway: ExchangeGateway
    ) -> ExecutionResult:
        if signal.action != "protective_stop":
            raise ValueError("stop submission requires a protective_stop decision")
        self._validate_position(signal, position)
        if not self._allow_submission:
            return ExecutionResult("rejected", None, "submission_disabled")
        quantity = _positive_decimal(position.quantity, "position quantity")
        stop_price = _positive_decimal(position.stop_price, "stop price")
        identifier = client_order_id(
            signal.asset,
            "sell",
            f"{signal.candle_id}:protective-stop",
            signal.strategy_version,
        )
        intent = self._persist_intent(
            signal,
            identifier=identifier,
            side="sell",
            order_type="stop-loss",
            quantity=quantity,
            position_id=position.position_id,
            stop_price=stop_price,
        )
        if intent is None:
            return ExecutionResult("reconciliation_required", identifier, "existing_intent")
        try:
            order = await gateway.create_stop_order(
                asset=signal.asset,
                side="sell",
                amount=quantity,
                stop_price=stop_price,
                client_order_id=identifier,
            )
        except Exception as error:
            return ExecutionResult("reconciliation_required", identifier, type(error).__name__)
        return self._record_exchange_result(identifier, order)

    async def _submit_market(
        self,
        signal: Decision,
        gateway: ExchangeGateway,
        *,
        side: str,
        quantity: Decimal,
        position_id: str | None,
    ) -> ExecutionResult:
        normalized_quantity = _positive_decimal(quantity, "order quantity")
        identifier = client_order_id(signal.asset, side, signal.candle_id, signal.strategy_version)
        intent = self._persist_intent(
            signal,
            identifier=identifier,
            side=side,
            order_type="market",
            quantity=normalized_quantity,
            position_id=position_id,
            stop_price=None,
        )
        if intent is None:
            return ExecutionResult("reconciliation_required", identifier, "existing_intent")
        try:
            order = await gateway.create_market_order(
                asset=signal.asset,
                side=side,
                amount=normalized_quantity,
                client_order_id=identifier,
            )
        except Exception as error:
            return ExecutionResult("reconciliation_required", identifier, type(error).__name__)
        return self._record_exchange_result(identifier, order)

    def _persist_intent(
        self,
        signal: Decision,
        *,
        identifier: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        position_id: str | None,
        stop_price: Decimal | None,
    ) -> OrderIntent | None:
        intent = OrderIntent(
            client_order_id=identifier,
            decision_id=signal.decision_id,
            asset=signal.asset,
            side=side,
            order_type=order_type,
            quantity=quantity,
            reference_price=_positive_decimal(signal.reference_price, "reference price"),
            created_at=signal.decided_at,
            position_id=position_id,
            stop_price=stop_price,
        )
        return intent if self.repository.persist_execution_intent(signal, intent) else None

    def _record_exchange_result(
        self, identifier: str, exchange_order: ExchangeOrder
    ) -> ExecutionResult:
        status = _canonical_order_status(exchange_order.status)
        try:
            self.repository.upsert_order(
                OrderRecord(
                    order_id=f"kraken:{exchange_order.exchange_order_id}",
                    client_order_id=identifier,
                    exchange_order_id=exchange_order.exchange_order_id,
                    asset=exchange_order.asset,
                    side=exchange_order.side,
                    order_type=exchange_order.order_type,
                    status=status,
                    requested_quantity=exchange_order.amount,
                    filled_quantity=exchange_order.filled,
                    average_fill_price=exchange_order.average_price,
                    fees=exchange_order.fee,
                    created_at=exchange_order.created_at,
                    updated_at=exchange_order.updated_at,
                )
            )
            if status is OrderStatus.REJECTED:
                self.repository.open_incident(
                    f"execution:{identifier}",
                    "exchange_rejected",
                    severity="critical" if exchange_order.order_type == "stop-loss" else "warning",
                )
        except Exception as error:
            return ExecutionResult("reconciliation_required", identifier, type(error).__name__, exchange_order)
        if status is OrderStatus.REJECTED:
            return ExecutionResult("rejected", identifier, "exchange_rejected", exchange_order)
        if exchange_order.status == "unknown":
            return ExecutionResult("reconciliation_required", identifier, "unknown_exchange_status", exchange_order)
        return ExecutionResult("submitted", identifier, exchange_order=exchange_order)

    @staticmethod
    def _validate_position(signal: Decision, position: Position) -> None:
        if position.asset != signal.asset:
            raise ValueError("position asset must match execution decision")
        if position.status not in {PositionStatus.OPENING, PositionStatus.OPEN, PositionStatus.CLOSING}:
            raise ValueError("execution requires an active position")


def _positive_decimal(value: Decimal | None, name: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive finite Decimal")
    return value


def _canonical_order_status(status: object) -> OrderStatus:
    if isinstance(status, OrderStatus):
        return status
    if status == "pending":
        return OrderStatus.SUBMITTED
    if status == "cancelled":
        return OrderStatus.CANCELED
    if status == "unknown":
        return OrderStatus.ERROR
    raise ValueError(f"unsupported exchange order status {status!r}")


__all__ = ["ExecutionResult", "ExecutionService", "client_order_id"]
