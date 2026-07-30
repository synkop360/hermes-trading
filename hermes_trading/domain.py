from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


class PositionStatus(str, Enum):
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


class OrderStatus(str, Enum):
    INTENT = "intent"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    ERROR = "error"


def decimal_from(value: str | int | Decimal) -> Decimal:
    if isinstance(value, float):
        raise TypeError("binary float is forbidden for financial values")
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class Decision:
    decision_id: str
    asset: str
    action: str
    reason: str
    candle_id: str
    strategy_version: str
    reference_price: Decimal
    decided_at: str


@dataclass(frozen=True)
class OrderIntent:
    client_order_id: str
    decision_id: str
    asset: str
    side: str
    order_type: str
    quantity: Decimal
    reference_price: Decimal
    created_at: str
    position_id: str | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    client_order_id: str
    asset: str
    side: str
    order_type: str
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    fees: Decimal
    created_at: str
    updated_at: str
    exchange_order_id: str | None = None


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    asset: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    filled_at: str


@dataclass(frozen=True)
class Position:
    position_id: str
    asset: str
    status: PositionStatus
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    strategy_version: str
    opened_at: str
    closed_at: str | None = None
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")


def json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value
