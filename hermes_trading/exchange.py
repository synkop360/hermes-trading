from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol

from hermes_trading.domain import OrderStatus


ExchangeOrderStatus = OrderStatus | Literal["pending", "cancelled", "unknown"]


class ExchangeGateway(Protocol):
    async def load_rules(self, asset: str) -> "MarketRules": ...

    async def fetch_balances(self) -> dict[str, Decimal]: ...

    async def fetch_orders(self, asset: str | None = None) -> tuple["ExchangeOrder", ...]: ...

    async def fetch_order(self, exchange_order_id: str, asset: str) -> "ExchangeOrder": ...

    async def fetch_order_by_client_id(
        self, client_order_id: str, asset: str
    ) -> "ExchangeOrder | None": ...

    async def create_market_order(
        self, *, asset: str, side: str, amount: Decimal, client_order_id: str
    ) -> "ExchangeOrder": ...

    async def create_stop_order(
        self,
        *,
        asset: str,
        side: str,
        amount: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> "ExchangeOrder": ...

    async def cancel_order(self, exchange_order_id: str, asset: str) -> "ExchangeOrder": ...

    async def close(self) -> None: ...


class ExchangePayloadError(ValueError):
    """Raised when a CCXT response cannot be made safe for persistence."""


class UnsafeApiKeyPermissions(ValueError):
    """Raised when a key can withdraw funds."""


@dataclass(frozen=True)
class ApiKeyInfo:
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        normalized = frozenset(permission.strip().lower() for permission in self.permissions)
        if not normalized or "" in normalized:
            raise ValueError("API key permissions must be non-empty strings")
        object.__setattr__(self, "permissions", normalized)


@dataclass(frozen=True)
class MarketRules:
    asset: str
    min_amount: Decimal | None
    min_cost: Decimal | None
    amount_precision: int | None
    price_precision: int | None

    def __post_init__(self) -> None:
        if not self.asset.strip():
            raise ValueError("asset is required")
        for name in ("min_amount", "min_cost"):
            value = getattr(self, name)
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError(f"{name} must be a positive finite Decimal")


@dataclass(frozen=True)
class ExchangeOrder:
    exchange_order_id: str
    client_order_id: str | None
    asset: str
    side: str
    order_type: str
    status: ExchangeOrderStatus
    amount: Decimal
    filled: Decimal
    remaining: Decimal | None
    average_price: Decimal | None
    fee: Decimal
    fee_currency: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not self.exchange_order_id or not self.asset or not self.side or not self.order_type:
            raise ValueError("exchange order identity fields are required")
        for name in ("amount", "filled", "remaining", "average_price", "fee"):
            value = getattr(self, name)
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError(f"{name} must be a non-negative finite Decimal")


@dataclass(frozen=True)
class ExchangeFill:
    exchange_fill_id: str
    exchange_order_id: str
    asset: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str | None
    filled_at: str

    def __post_init__(self) -> None:
        if not self.exchange_fill_id or not self.exchange_order_id or not self.asset or not self.side:
            raise ValueError("exchange fill identity fields are required")
        for name in ("quantity", "price", "fee"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a non-negative finite Decimal")
        _timestamp(self.filled_at, "filled_at")


def validate_api_key_permissions(info: ApiKeyInfo) -> None:
    if any("withdraw" in permission for permission in info.permissions):
        raise UnsafeApiKeyPermissions("Kraken API keys with withdrawal permission are forbidden")


class KrakenGateway:
    """Cycle-scoped CCXT adapter with a narrow, Decimal-only contract."""

    def __init__(self, exchange_factory: Callable[[], Any]) -> None:
        self._exchange_factory = exchange_factory
        self._exchange: Any | None = None
        self._closed = False

    async def __aenter__(self) -> "KrakenGateway":
        self._client()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        exchange = self._exchange
        if exchange is not None:
            await exchange.close()

    async def load_rules(self, asset: str) -> MarketRules:
        markets = await self._remote(lambda: self._client().load_markets())
        if not isinstance(markets, Mapping) or asset not in markets:
            raise ExchangePayloadError(f"market rules unavailable for {asset}")
        market = _mapping(markets[asset], f"market {asset}")
        limits = _mapping(market.get("limits", {}), "market limits")
        amount_limits = _mapping(limits.get("amount", {}), "amount limits")
        cost_limits = _mapping(limits.get("cost", {}), "cost limits")
        precision = _mapping(market.get("precision", {}), "market precision")
        return MarketRules(
            asset=asset,
            min_amount=_optional_positive_decimal(amount_limits.get("min"), "minimum amount"),
            min_cost=_optional_positive_decimal(cost_limits.get("min"), "minimum cost"),
            amount_precision=_optional_precision(precision.get("amount"), "amount precision"),
            price_precision=_optional_precision(precision.get("price"), "price precision"),
        )

    async def fetch_balances(self) -> dict[str, Decimal]:
        payload = _mapping(await self._remote(lambda: self._client().fetch_balance()), "balance response")
        balances = _mapping(payload.get("free", {}), "free balances")
        return {
            str(currency): _decimal(value, f"free balance {currency}")
            for currency, value in balances.items()
            if value is not None
        }

    async def fetch_orders(self, asset: str | None = None) -> tuple[ExchangeOrder, ...]:
        client = self._client()
        payload = await self._remote(
            lambda: client.fetch_orders(asset) if asset is not None else client.fetch_orders()
        )
        if not isinstance(payload, list):
            raise ExchangePayloadError("orders response must be a list")
        return tuple(_order(item, asset) for item in payload)

    async def fetch_order(self, exchange_order_id: str, asset: str) -> ExchangeOrder:
        payload = await self._remote(lambda: self._client().fetch_order(exchange_order_id, asset))
        return _order(payload, asset)

    async def fetch_order_by_client_id(
        self, client_order_id: str, asset: str
    ) -> ExchangeOrder | None:
        client = self._client()
        lookup = getattr(client, "fetch_order_by_client_order_id", None)
        if callable(lookup):
            payload = await self._remote(lambda: lookup(client_order_id, asset))
            return _order(payload, asset) if payload is not None else None
        for order in await self.fetch_orders(asset):
            if order.client_order_id == client_order_id:
                return order
        return None

    async def create_market_order(
        self, *, asset: str, side: str, amount: Decimal, client_order_id: str
    ) -> ExchangeOrder:
        return await self._create_order(
            asset=asset,
            order_type="market",
            side=side,
            amount=amount,
            price=None,
            client_order_id=client_order_id,
            extra_params={},
        )

    async def create_stop_order(
        self,
        *,
        asset: str,
        side: str,
        amount: Decimal,
        stop_price: Decimal,
        client_order_id: str,
    ) -> ExchangeOrder:
        return await self._create_order(
            asset=asset,
            order_type="stop-loss",
            side=side,
            amount=amount,
            price=None,
            client_order_id=client_order_id,
            extra_params={"triggerPrice": str(_positive_decimal(stop_price, "stop price"))},
        )

    async def cancel_order(self, exchange_order_id: str, asset: str) -> ExchangeOrder:
        payload = await self._remote(lambda: self._client().cancel_order(exchange_order_id, asset))
        return _order(payload, asset)

    async def _create_order(
        self,
        *,
        asset: str,
        order_type: str,
        side: str,
        amount: Decimal,
        price: Decimal | None,
        client_order_id: str,
        extra_params: Mapping[str, str],
    ) -> ExchangeOrder:
        normalized_amount = _positive_decimal(amount, "order amount")
        if not client_order_id.strip():
            raise ValueError("client order id is required")
        params = {"cl_ord_id": client_order_id, **extra_params}
        payload = await self._remote(
            lambda: self._client().create_order(
                asset,
                order_type,
                side,
                str(normalized_amount),
                str(price) if price is not None else None,
                params,
            )
        )
        return _order(payload, asset)

    async def _remote(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await operation()
        except BaseException:
            try:
                await self.close()
            except BaseException:
                pass
            raise

    def _client(self) -> Any:
        if self._closed:
            raise RuntimeError("Kraken gateway is closed")
        if self._exchange is None:
            self._exchange = self._exchange_factory()
        return self._exchange


def _order(payload: object, fallback_asset: str | None) -> ExchangeOrder:
    raw = _mapping(payload, "order")
    fee_raw = _mapping(raw.get("fee", {}), "order fee")
    raw_info = raw.get("info")
    info = raw_info if isinstance(raw_info, Mapping) else {}
    amount = _decimal(raw.get("amount"), "order amount")
    filled = _decimal(raw.get("filled", "0"), "order filled quantity")
    created_at = _timestamp(raw.get("datetime", raw.get("timestamp")), "order timestamp")
    updated_at = _timestamp(
        raw.get("lastTradeTimestamp", raw.get("lastTradeDatetime", raw.get("datetime", raw.get("timestamp")))),
        "order update timestamp",
    )
    return ExchangeOrder(
        exchange_order_id=_required_text(raw.get("id"), "exchange order id"),
        client_order_id=_client_order_id(raw, info),
        asset=_required_text(raw.get("symbol", fallback_asset), "order symbol"),
        side=_required_text(raw.get("side"), "order side"),
        order_type=_required_text(raw.get("type"), "order type"),
        status=_order_status(raw.get("status"), amount=amount, filled=filled),
        amount=amount,
        filled=filled,
        remaining=_optional_decimal(raw.get("remaining"), "order remaining quantity"),
        average_price=_optional_decimal(raw.get("average"), "order average price"),
        fee=_decimal(fee_raw.get("cost", "0"), "order fee"),
        fee_currency=_optional_text(fee_raw.get("currency")),
        created_at=created_at,
        updated_at=updated_at,
    )


def _client_order_id(raw: Mapping[str, Any], info: Mapping[str, Any]) -> str | None:
    return _optional_text(
        raw.get("clientOrderId", raw.get("client_order_id", info.get("cl_ord_id", info.get("client_order_id"))))
    )


def _order_status(value: object, *, amount: Decimal, filled: Decimal) -> ExchangeOrderStatus:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized == "open" and Decimal("0") < filled < amount:
        return OrderStatus.PARTIALLY_FILLED
    mapping: dict[str, ExchangeOrderStatus] = {
        "pending": "pending",
        "new": "pending",
        "open": OrderStatus.OPEN,
        "partial": OrderStatus.PARTIALLY_FILLED,
        "partially_filled": OrderStatus.PARTIALLY_FILLED,
        "closed": OrderStatus.FILLED,
        "filled": OrderStatus.FILLED,
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "rejected": OrderStatus.REJECTED,
        "rejected_by_exchange": OrderStatus.REJECTED,
    }
    return mapping.get(normalized, "unknown")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExchangePayloadError(f"{name} must be an object")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ExchangePayloadError(f"{name} is required")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ExchangePayloadError(f"{name} must be a Decimal-compatible value") from error
    if not parsed.is_finite():
        raise ExchangePayloadError(f"{name} must be finite")
    return parsed


def _optional_decimal(value: object, name: str) -> Decimal | None:
    return None if value is None else _decimal(value, name)


def _positive_decimal(value: object, name: str) -> Decimal:
    parsed = _decimal(value, name)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _optional_positive_decimal(value: object, name: str) -> Decimal | None:
    return None if value is None else _positive_decimal(value, name)


def _optional_precision(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExchangePayloadError(f"{name} must be a non-negative integer")
    return value


def _required_text(value: object, name: str) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise ExchangePayloadError(f"{name} is required")
    return normalized


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _timestamp(value: object, name: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ExchangePayloadError(f"{name} is required")
    if isinstance(value, (int, float, Decimal)):
        try:
            parsed = datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise ExchangePayloadError(f"{name} is invalid") from error
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ExchangePayloadError(f"{name} is invalid") from error
        if parsed.tzinfo is None:
            raise ExchangePayloadError(f"{name} must include a timezone")
    else:
        raise ExchangePayloadError(f"{name} is invalid")
    return parsed.astimezone(UTC).isoformat()


__all__ = [
    "ApiKeyInfo",
    "ExchangeFill",
    "ExchangeGateway",
    "ExchangeOrder",
    "ExchangePayloadError",
    "KrakenGateway",
    "MarketRules",
    "UnsafeApiKeyPermissions",
    "validate_api_key_permissions",
]
