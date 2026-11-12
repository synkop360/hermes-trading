from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
from decimal import ROUND_DOWN
from typing import Literal

from hermes_trading.domain import (
    Decision,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Position,
    PositionStatus,
)
from hermes_trading.costs import CostLedger, kraken_fee_event, slippage_cost_event
from hermes_trading.exchange import ExchangeGateway, ExchangeOrder
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.risk import RiskDecision


ExecutionStatus = Literal[
    "submitted", "reconciliation_required", "rejected", "emergency_closed"
]


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


class StopFillRequiresReconciliation(RuntimeError):
    """Stop fills changed while an exit or replacement was being prepared."""


class ExecutionService:
    """Crash-safe order intent boundary.

    The service is deliberately dormant until a later loop migration supplies
    a gateway.  Its only submission paths are exercised with test gateways in
    this task; production execution remains guarded by the existing facade.
    """

    def __init__(
        self,
        repository: StateRepository,
        *,
        allow_submission: bool = False,
        cost_ledger: CostLedger | None = None,
    ) -> None:
        self.repository = repository
        self._allow_submission = allow_submission
        self.cost_ledger = cost_ledger

    async def submit_entry(
        self,
        signal: Decision,
        risk_decision: RiskDecision,
        gateway: ExchangeGateway,
        *, stop_price: Decimal | None = None,
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
            stop_price=stop_price,
        )

    async def submit_exit(
        self, signal: Decision, position: Position, gateway: ExchangeGateway,
        *, fraction: Decimal = Decimal("1"),
    ) -> ExecutionResult:
        if signal.action != "exit":
            raise ValueError("exit submission requires an exit decision")
        self._validate_position(signal, position)
        if not self._allow_submission:
            return ExecutionResult("rejected", None, "submission_disabled")
        if not fraction.is_finite() or not 0 < fraction <= 1:
            raise ValueError("exit fraction must be in (0, 1]")
        quantity = position.quantity * fraction
        load_rules = getattr(gateway, "load_rules", None)
        if fraction < 1 and callable(load_rules):
            rules = await load_rules(position.asset)
            quantity = _normalize_down(quantity, rules.amount_precision)
            remaining = position.quantity - quantity
            minimum = rules.min_cost or Decimal("0")
            min_amount = rules.min_amount or Decimal("0")
            if (quantity < min_amount or remaining < min_amount
                    or quantity * signal.reference_price < minimum
                    or remaining * signal.reference_price < minimum):
                # Keep dust protected instead of sending an unplaceable partial.
                return ExecutionResult("rejected", None, "partial_below_exchange_minimum")
        # Release the hosted stop's reservation before reducing the position.
        # The caller reconciles and immediately protects the remainder.
        try:
            await self._cancel_replaced_stops(position, Decimal("0"), Decimal("0"), gateway)
        except Exception as error:
            return ExecutionResult("reconciliation_required", None, f"exit_stop_cancel:{type(error).__name__}")
        return await self._submit_market(
            signal, gateway, side="sell",
            quantity=_positive_decimal(quantity, "exit quantity"),
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
        try:
            quantity, stop_price = await self._normalized_stop(position, gateway)
            replacement = await self._cancel_replaced_stops(
                position, quantity, stop_price, gateway
            )
        except StopFillRequiresReconciliation:
            return ExecutionResult("reconciliation_required", None, "protective_stop_fill_changed")
        except Exception as error:
            return await self.emergency_close(
                position,
                gateway,
                signal=signal,
                reason=f"protective_stop_unavailable:{type(error).__name__}",
            )
        if replacement is not None:
            return replacement
        identifier = client_order_id(
            signal.asset,
            "sell",
            f"{signal.candle_id}:protective-stop:{quantity}:{stop_price}",
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
            existing = self.repository.order_by_client_id(identifier)
            if existing is not None and existing.status in {
                OrderStatus.SUBMITTED,
                OrderStatus.OPEN,
                OrderStatus.PARTIALLY_FILLED,
            }:
                return ExecutionResult("submitted", identifier, "existing_active_stop")
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
            return await self.emergency_close(
                position,
                gateway,
                signal=signal,
                reason=f"protective_stop_submission_failed:{type(error).__name__}",
            )
        try:
            status = _canonical_order_status(order.status)
        except Exception as error:
            return await self.emergency_close(
                position,
                gateway,
                signal=signal,
                reason=f"protective_stop_unconfirmed:{type(error).__name__}",
            )
        if status is OrderStatus.REJECTED:
            self._record_exchange_result(identifier, order)
            return await self.emergency_close(
                position,
                gateway,
                signal=signal,
                reason="protective_stop_rejected:exchange_rejected",
            )
        confirmation_error = _stop_confirmation_error(
            order, identifier, position.asset, quantity, stop_price
        )
        if confirmation_error is not None:
            return await self.emergency_close(
                position,
                gateway,
                signal=signal,
                reason=f"protective_stop_unconfirmed:{confirmation_error}",
            )
        result = self._record_exchange_result(identifier, order)
        if result.status == "submitted":
            return result
        return await self.emergency_close(
            position,
            gateway,
            signal=signal,
            reason=f"protective_stop_rejected:{result.reason or result.status}",
        )

    async def emergency_close(
        self,
        position: Position,
        gateway: ExchangeGateway,
        *,
        signal: Decision | None = None,
        reason: str = "unprotected_position",
    ) -> ExecutionResult:
        """Freeze entries and submit exactly one market sell for an unprotected position."""
        if not self._allow_submission:
            return ExecutionResult("rejected", None, "submission_disabled")
        emergency_signal = signal or _emergency_signal(position)
        decision = Decision(
            decision_id=f"{emergency_signal.decision_id}:emergency-close",
            asset=position.asset,
            action="emergency_exit",
            reason=reason,
            candle_id=f"{emergency_signal.candle_id}:emergency-close:{position.position_id}",
            strategy_version=position.strategy_version,
            reference_price=position.entry_price,
            decided_at=emergency_signal.decided_at,
        )
        self.repository.set_risk_state(
            kill_switch=True,
            reason=reason,
            updated_at=datetime.fromisoformat(decision.decided_at),
        )
        self.repository.open_incident(
            f"protective-stop:{position.position_id}",
            reason,
            severity="critical",
            opened_at=datetime.fromisoformat(decision.decided_at),
        )
        result = await self._submit_market(
            decision,
            gateway,
            side="sell",
            quantity=_positive_decimal(position.quantity, "position quantity"),
            position_id=position.position_id,
        )
        if result.status == "submitted":
            return ExecutionResult(
                "emergency_closed",
                result.client_order_id,
                reason,
                result.exchange_order,
            )
        return result

    async def _normalized_stop(
        self, position: Position, gateway: ExchangeGateway
    ) -> tuple[Decimal, Decimal]:
        quantity = _positive_decimal(position.quantity, "position quantity")
        stop_price = _positive_decimal(position.stop_price, "stop price")
        load_rules = getattr(gateway, "load_rules", None)
        if not callable(load_rules):
            return quantity, stop_price
        rules = await load_rules(position.asset)
        normalized_quantity = _normalize_down(quantity, rules.amount_precision)
        normalized_stop_price = _normalize_down(stop_price, rules.price_precision)
        if normalized_quantity != quantity:
            raise ValueError("stop precision leaves an uncovered position remainder")
        return (
            _positive_decimal(normalized_quantity, "normalized stop quantity"),
            _positive_decimal(normalized_stop_price, "normalized stop price"),
        )

    async def _cancel_replaced_stops(
        self,
        position: Position,
        quantity: Decimal,
        stop_price: Decimal,
        gateway: ExchangeGateway,
    ) -> ExecutionResult | None:
        candidates = [
            intent
            for intent in self.repository.order_intents()
            if intent.position_id == position.position_id
            and intent.side == "sell"
            and intent.order_type == "stop-loss"
        ]
        active = [
            intent
            for intent in candidates
            if (order := self.repository.order_by_client_id(intent.client_order_id)) is not None
            and order.status in {OrderStatus.SUBMITTED, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
        ]
        if not active:
            return None
        fetch_orders = getattr(gateway, "fetch_orders", None)
        if not callable(fetch_orders):
            raise RuntimeError("exchange cannot verify existing protective stop")
        remote_by_client_id = {
            order.client_order_id: order
            for order in await fetch_orders(position.asset)
            if order.client_order_id is not None
        }
        for intent in active:
            observed = remote_by_client_id.get(intent.client_order_id)
            local = self.repository.order_by_client_id(intent.client_order_id)
            if observed is not None and local is not None and observed.filled > local.filled_quantity:
                self._record_exchange_result(intent.client_order_id, observed)
                raise StopFillRequiresReconciliation()
        desired = [
            intent
            for intent in active
            if intent.quantity == quantity and intent.stop_price == stop_price
            and (remote := remote_by_client_id.get(intent.client_order_id)) is not None
            and _is_active_stop(remote, position.asset, quantity, stop_price)
        ]
        keep = desired[0] if desired else None
        if keep is not None and len(active) == 1:
            return ExecutionResult("submitted", keep.client_order_id, "existing_active_stop")
        for intent in active:
            if keep is not None and intent.client_order_id == keep.client_order_id:
                continue
            remote = remote_by_client_id.get(intent.client_order_id)
            if remote is None or not _is_active_stop(
                remote, position.asset, intent.quantity, intent.stop_price
            ):
                raise RuntimeError("existing protective stop is not active remotely")
            cancelled = await gateway.cancel_order(remote.exchange_order_id, position.asset)
            recorded = self._record_exchange_result(intent.client_order_id, cancelled)
            if cancelled.filled > remote.filled:
                raise StopFillRequiresReconciliation()
            if recorded.status != "submitted" or _canonical_order_status(cancelled.status) is not OrderStatus.CANCELED:
                raise RuntimeError("protective stop cancellation was not confirmed")
        if keep is not None:
            return ExecutionResult("submitted", keep.client_order_id, "existing_active_stop")
        return None

    async def _submit_market(
        self,
        signal: Decision,
        gateway: ExchangeGateway,
        *,
        side: str,
        quantity: Decimal,
        position_id: str | None,
        stop_price: Decimal | None = None,
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
            stop_price=stop_price,
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
            self._record_costs(identifier, exchange_order, status)
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

    def _record_costs(self, identifier: str, exchange_order: ExchangeOrder, status: OrderStatus) -> None:
        if self.cost_ledger is None or status is not OrderStatus.FILLED:
            return
        occurred_at = datetime.fromisoformat(exchange_order.updated_at)
        if exchange_order.fee > 0:
            self.cost_ledger.record(
                kraken_fee_event(
                    amount=exchange_order.fee,
                    currency=exchange_order.fee_currency,
                    asset=exchange_order.asset,
                    order_id=exchange_order.exchange_order_id,
                    occurred_at=occurred_at,
                )
            )
        intent = self.repository.intent_by_client_id(identifier)
        if intent is None or exchange_order.average_price is None or exchange_order.filled <= 0:
            return
        self.cost_ledger.record(
            slippage_cost_event(
                side=exchange_order.side,
                quantity=exchange_order.filled,
                reference_price=intent.reference_price,
                average_price=exchange_order.average_price,
                asset=exchange_order.asset,
                order_id=exchange_order.exchange_order_id,
                occurred_at=occurred_at,
            )
        )


def _positive_decimal(value: Decimal | None, name: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive finite Decimal")
    return value


def _normalize_down(value: Decimal, precision: int | None) -> Decimal:
    if precision is None:
        return value
    if precision < 0:
        raise ValueError("market precision must be non-negative")
    quantum = Decimal(1).scaleb(-precision)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _is_active_stop(
    order: ExchangeOrder,
    asset: str,
    quantity: Decimal,
    trigger_price: Decimal | None = None,
) -> bool:
    return (
        order.asset == asset
        and order.side == "sell"
        and order.order_type == "stop-loss"
        and order.amount == quantity
        and (trigger_price is None or order.trigger_price == trigger_price)
        and _canonical_order_status(order.status)
        in {OrderStatus.SUBMITTED, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
    )


def _stop_confirmation_error(
    order: ExchangeOrder,
    client_order_id: str,
    asset: str,
    quantity: Decimal,
    trigger_price: Decimal,
) -> str | None:
    if order.client_order_id != client_order_id:
        return "client_order_id_mismatch"
    if not _is_active_stop(order, asset, quantity, trigger_price):
        return "identity_quantity_or_trigger_mismatch"
    return None


def _emergency_signal(position: Position) -> Decision:
    return Decision(
        decision_id=f"position:{position.position_id}:protection",
        asset=position.asset,
        action="protective_stop",
        reason="startup_protection_check",
        candle_id=f"position:{position.position_id}:{position.opened_at}",
        strategy_version=position.strategy_version,
        reference_price=position.entry_price,
        decided_at=position.opened_at,
    )


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
