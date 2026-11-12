from __future__ import annotations

from collections import deque
from copy import deepcopy
from decimal import Decimal
from typing import Any


class FakeKrakenExchange:
    """Small stateful CCXT substitute for exchange-boundary tests."""

    def __init__(self) -> None:
        self.closed = False
        self.create_order_calls = 0
        self.create_order_requests: list[dict[str, Any]] = []
        self._create_outcomes: deque[object] = deque()
        self._orders: dict[str, dict[str, Any]] = {}
        self._next_order_number = 1
        self._markets = {
            "BTC/USDT": {
                "limits": {"amount": {"min": "0.00001"}, "cost": {"min": "5"}},
                "precision": {"amount": 8, "price": 2},
            }
        }
        self._balances = {"free": {"USDT": "14.82", "BTC": "0"}}

    @classmethod
    def partial_fill(cls) -> "FakeKrakenExchange":
        fake = cls()
        fake.seed_order(
            exchange_order_id="KRAKEN-1",
            client_order_id="ht-partial",
            status="open",
            amount="0.0001",
            filled="0.00005",
            fee="0.01",
            timestamp="2026-07-30T12:00:00Z",
        )
        return fake

    def queue_create_order(self, outcome: object) -> None:
        self._create_outcomes.append(outcome)

    def seed_order(
        self,
        *,
        client_order_id: str | None = None,
        status: str = "open",
        filled: str = "0",
        exchange_order_id: str | None = None,
        asset: str = "BTC/USDT",
        side: str = "buy",
        order_type: str = "market",
        amount: str = "0.0001",
        fee: str = "0",
        stop_price: str | None = None,
        timestamp: str = "2026-07-30T12:00:00Z",
    ) -> str:
        order_id = exchange_order_id or self._next_order_id()
        self._orders[order_id] = {
            "id": order_id,
            "clientOrderId": client_order_id,
            "symbol": asset,
            "type": order_type,
            "side": side,
            "status": status,
            "amount": amount,
            "filled": filled,
            "remaining": str(max(Decimal(amount) - Decimal(filled), Decimal("0"))),
            "average": "65000" if filled != "0" else None,
            "fee": {"cost": fee, "currency": "USDT"},
            "datetime": timestamp,
            "lastTradeDatetime": timestamp,
            "info": {"cl_ord_id": client_order_id, "stopLossPrice": stop_price},
        }
        base = asset.split("/", 1)[0]
        if Decimal(filled) > 0:
            available = Decimal(self._balances["free"].get(base, "0"))
            change = Decimal(filled) if side == "buy" else -Decimal(filled)
            self._balances["free"][base] = str(max(Decimal("0"), available + change))
        return order_id

    def seed_unmatched_open_order(self, *, asset: str = "BTC/USDT") -> str:
        return self.seed_order(client_order_id=None, asset=asset, status="open")

    async def load_markets(self) -> dict[str, Any]:
        return deepcopy(self._markets)

    async def fetch_balance(self) -> dict[str, Any]:
        return deepcopy(self._balances)

    async def private_post_get_api_key_info(self) -> dict[str, Any]:
        return {"result": {"permissions": ["query-funds", "query-open-trades", "modify-trades"]}}

    async def fetch_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [
            deepcopy(order)
            for order in self._orders.values()
            if symbol is None or order["symbol"] == symbol
        ]

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [
            deepcopy(order) for order in self._orders.values()
            if (symbol is None or order["symbol"] == symbol)
            and order["status"] in {"open", "partially_filled"}
        ]

    async def fetch_closed_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [
            deepcopy(order) for order in self._orders.values()
            if (symbol is None or order["symbol"] == symbol)
            and order["status"] not in {"open", "partially_filled"}
        ]

    async def fetch_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        return deepcopy(self._orders[order_id])

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: str,
        price: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.create_order_calls += 1
        request = {
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": dict(params or {}),
        }
        self.create_order_requests.append(request)
        if self._create_outcomes:
            outcome = self._create_outcomes.popleft()
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, dict):
                return deepcopy(outcome)
        client_order_id = request["params"].get("cl_ord_id")
        order_id = self.seed_order(
            client_order_id=client_order_id,
            exchange_order_id=self._next_order_id(),
            asset=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            stop_price=request["params"].get("stopLossPrice"),
        )
        return deepcopy(self._orders[order_id])

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        self._orders[order_id]["status"] = "canceled"
        return deepcopy(self._orders[order_id])

    async def close(self) -> None:
        self.closed = True

    def _next_order_id(self) -> str:
        while f"KRAKEN-{self._next_order_number}" in self._orders:
            self._next_order_number += 1
        order_id = f"KRAKEN-{self._next_order_number}"
        self._next_order_number += 1
        return order_id
