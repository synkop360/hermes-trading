from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hermes_trading.domain import Decision, Fill, OrderIntent, OrderRecord, OrderStatus, Position, PositionStatus
from hermes_trading.market_data import MarketSnapshot
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import ProjectionItem, StateRepository
from hermes_trading.strategy import StrategySignal, _decimal_setting, evaluate_strategy


@dataclass(frozen=True)
class SimulationSettings:
    quote_notional: Decimal = Decimal("5")
    taker_fee_rate: Decimal = Decimal("0.004")
    maker_fee_rate: Decimal = Decimal("0.0025")
    slippage_rate: Decimal = Decimal("0.001")

    @classmethod
    def from_strategy(cls, strategy: dict[str, Any]) -> SimulationSettings:
        source = strategy.get("simulation", {})
        if not isinstance(source, dict):
            raise ValueError("simulation must be a mapping")
        values = {
            "quote_notional": _decimal_setting(source.get("quote_notional", "5"), "quote_notional"),
            "taker_fee_rate": _decimal_setting(source.get("taker_fee_rate", "0.004"), "taker_fee_rate"),
            "maker_fee_rate": _decimal_setting(source.get("maker_fee_rate", "0.0025"), "maker_fee_rate"),
            "slippage_rate": _decimal_setting(source.get("slippage_rate", "0.001"), "slippage_rate"),
        }
        if values["quote_notional"] <= 0:
            raise ValueError("quote_notional must be greater than zero")
        for name in ("taker_fee_rate", "maker_fee_rate", "slippage_rate"):
            if values[name] < 0 or values[name] >= 1:
                raise ValueError(f"{name} must be in [0, 1)")
        return cls(**values)


@dataclass(frozen=True)
class PositionProcessResult:
    processed: bool
    reason: str
    position: Position | None
    closed_positions: tuple[Position, ...] = ()
    decision: Decision | None = None
    orders: tuple[OrderRecord, ...] = ()
    projection_error: str | None = None


class PositionEngine:
    def __init__(self, state: StateRepository, projector: ArtifactProjector | None = None) -> None:
        self.state = state
        self.projector = projector

    def process_candle(
        self,
        snapshot: MarketSnapshot,
        strategy: dict[str, Any],
        *,
        recovery_error: str | None = None,
        recover_pending: bool = True,
    ) -> PositionProcessResult:
        resume_error = self.recover_pending_projections() if recover_pending else recovery_error
        candle = snapshot.candle
        if candle is None:
            return PositionProcessResult(False, snapshot.reason, None, projection_error=resume_error)
        identity = snapshot.candle_identity
        if self.state.is_candle_processed(identity.asset, identity.timeframe, identity.close_timestamp.isoformat()):
            return PositionProcessResult(False, "duplicate_candle", self.state.open_position(snapshot.asset), projection_error=resume_error)

        settings = SimulationSettings.from_strategy(strategy)
        existing = self.state.open_position(snapshot.asset)
        if existing is not None and identity.close_timestamp.isoformat() <= existing.opened_at:
            signal = StrategySignal("no_trade", snapshot.asset, "out_of_order_candle", candle.close, identity.candle_id)
        else:
            signal = evaluate_strategy(snapshot, strategy, existing)
        now = identity.close_timestamp.isoformat()
        decision = Decision(
            decision_id=_identifier("decision", signal.candle_id, signal.action),
            asset=snapshot.asset,
            action=signal.action,
            reason=signal.reason,
            candle_id=signal.candle_id,
            strategy_version=str(strategy.get("version", "01")),
            reference_price=signal.reference_price,
            decided_at=now,
        )
        intents: list[OrderIntent] = []
        orders: list[OrderRecord] = []
        fills: list[Fill] = []
        positions: list[Position] = []
        closed: tuple[Position, ...] = ()
        current = existing
        if signal.action == "enter":
            current, intent, order, fill = self._entry(snapshot, decision, strategy, settings)
            positions.append(current)
            intents.append(intent)
            orders.append(order)
            fills.append(fill)
        elif signal.action == "exit" and existing is not None:
            current, intent, order, fill = self._exit(snapshot, decision, strategy, settings, existing, signal)
            positions.append(current)
            intents.append(intent)
            orders.append(order)
            fills.append(fill)
            closed = (current,)

        committed = self.state.persist_candle_outcome(
            identity.asset,
            identity.timeframe,
            identity.close_timestamp.isoformat(),
            decision,
            intents=intents,
            orders=orders,
            fills=fills,
            positions=positions,
        )
        if not committed:
            return PositionProcessResult(False, "duplicate_candle", self.state.open_position(snapshot.asset), projection_error=resume_error)
        projection_error = self._project_pending_item(decision.decision_id)
        projection_error = resume_error or projection_error
        return PositionProcessResult(True, signal.reason, current, closed, decision, tuple(orders), projection_error)

    def recover_pending_projections(self) -> str | None:
        """Retry a bounded batch of durable artifact work before market handling."""
        items = self.state.pending_projection_items()
        if not items:
            return None
        if self.projector is None:
            return "no artifact projector is configured"
        for item in items:
            error = self._project_item(item)
            if error is not None:
                return error
        if self.state.has_pending_projection_items():
            return "projection backlog remains after bounded recovery"
        return None

    def _project_pending_item(self, decision_id: str) -> str | None:
        return self._project_item_id(f"projection:item:{decision_id}")

    def _project_item_id(self, item_id: str) -> str | None:
        item = self.state.pending_projection_item(item_id)
        return self._project_item(item) if item is not None else None

    def _project_item(self, item: ProjectionItem) -> str | None:
        if self.projector is None:
            return "no artifact projector is configured"
        try:
            self.projector.project_pending_item(self.state, item)
            self.state.acknowledge_projection_item(item.item_id)
        except Exception as error:
            return str(error)
        return None

    def _entry(self, snapshot: MarketSnapshot, decision: Decision, strategy: dict[str, Any], settings: SimulationSettings) -> tuple[Position, OrderIntent, OrderRecord, Fill]:
        candle = snapshot.candle
        fill_price = candle.close * (Decimal("1") + settings.slippage_rate)
        quantity = settings.quote_notional / fill_price
        _positive_finite(fill_price, "entry fill price")
        _positive_finite(quantity, "entry quantity")
        fee = quantity * fill_price * settings.taker_fee_rate
        stop_pct = _decimal_setting(strategy.get("stop_loss_pct"), "stop_loss_pct")
        if stop_pct <= 0 or stop_pct >= 100:
            raise ValueError("stop_loss_pct must be greater than zero and less than 100")
        stop_price = fill_price * (Decimal("1") - stop_pct / Decimal("100"))
        _positive_finite(stop_price, "stop price")
        return self._records(decision, "buy", quantity, fill_price, fee, stop_price, strategy, PositionStatus.OPEN, order_type="market")

    def _exit(self, snapshot: MarketSnapshot, decision: Decision, strategy: dict[str, Any], settings: SimulationSettings, position: Position, signal: StrategySignal) -> tuple[Position, OrderIntent, OrderRecord, Fill]:
        candle = snapshot.candle
        base_price = min(candle.open, position.stop_price) if signal.reason == "stop_loss" else candle.close
        fill_price = base_price * (Decimal("1") - settings.slippage_rate)
        exit_rule = strategy.get("exit")
        maker = signal.reason == "explicit_exit" and isinstance(exit_rule, dict) and str(exit_rule.get("fee", "taker")).lower() == "maker"
        fee = position.quantity * fill_price * (settings.maker_fee_rate if maker else settings.taker_fee_rate)
        _positive_finite(fill_price, "exit fill price")
        _positive_finite(position.quantity, "exit quantity")
        closed, intent, order, fill = self._records(decision, "sell", position.quantity, fill_price, fee, position.stop_price, strategy, PositionStatus.CLOSED, previous=position, order_type="limit" if maker else "market")
        return closed, intent, order, fill

    def _records(self, decision: Decision, side: str, quantity: Decimal, fill_price: Decimal, fee: Decimal, stop_price: Decimal, strategy: dict[str, Any], status: PositionStatus, previous: Position | None = None, *, order_type: str) -> tuple[Position, OrderIntent, OrderRecord, Fill]:
        marker = f"{decision.candle_id}:{side}"
        position_id = previous.position_id if previous is not None else _identifier("position", decision.asset, decision.candle_id)
        client_order_id = _identifier("intent", marker)
        order_id = _identifier("order", marker)
        now = decision.decided_at
        intent = OrderIntent(client_order_id, decision.decision_id, decision.asset, side, order_type, quantity, fill_price, now, position_id, stop_price)
        order = OrderRecord(order_id, client_order_id, decision.asset, side, order_type, OrderStatus.FILLED, quantity, quantity, fill_price, fee, now, now)
        fill = Fill(_identifier("fill", marker), order_id, decision.asset, side, quantity, fill_price, fee, now)
        if previous is None:
            position = Position(position_id, decision.asset, status, quantity, fill_price, stop_price, decision.strategy_version, now, fees=fee)
        else:
            total_fees = previous.fees + fee
            pnl = previous.quantity * (fill_price - previous.entry_price) - total_fees
            position = Position(position_id, previous.asset, status, previous.quantity, previous.entry_price, previous.stop_price, previous.strategy_version, previous.opened_at, now, pnl, total_fees)
        return position, intent, order, fill

def _identifier(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "hermes-trading:" + ":".join(parts)))


def _positive_finite(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


__all__ = ["PositionEngine", "PositionProcessResult", "SimulationSettings"]
