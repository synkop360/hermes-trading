from __future__ import annotations

import asyncio
from decimal import Decimal
import unittest

from hermes_trading.domain import OrderStatus
from hermes_trading.exchange import (
    ApiKeyInfo,
    KrakenGateway,
    UnsafeApiKeyPermissions,
    validate_api_key_permissions,
)
from tests.fakes.kraken import FakeKrakenExchange


class KrakenGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_fill_is_normalized_with_decimal_values(self) -> None:
        fake = FakeKrakenExchange.partial_fill()

        order = await KrakenGateway(lambda: fake).fetch_order("KRAKEN-1", "BTC/USDT")

        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(order.filled, Decimal("0.00005"))
        self.assertEqual(order.fee, Decimal("0.01"))
        self.assertEqual(order.created_at, "2026-07-30T12:00:00+00:00")

    def test_permissions_reject_withdrawal_access(self) -> None:
        info = ApiKeyInfo(
            permissions=frozenset({"query-funds", "modify-trades", "withdraw-funds"})
        )

        with self.assertRaises(UnsafeApiKeyPermissions):
            validate_api_key_permissions(info)

    async def test_market_order_preserves_client_id_for_reconciliation(self) -> None:
        fake = FakeKrakenExchange()
        gateway = KrakenGateway(lambda: fake)

        order = await gateway.create_market_order(
            asset="BTC/USDT",
            side="buy",
            amount=Decimal("0.0001"),
            client_order_id="ht-btc-entry",
        )

        self.assertEqual(order.client_order_id, "ht-btc-entry")
        self.assertEqual(fake.create_order_calls, 1)
        self.assertEqual(fake.create_order_requests[0]["params"], {"cl_ord_id": "ht-btc-entry"})

    async def test_lost_response_can_be_found_without_a_second_submission(self) -> None:
        fake = FakeKrakenExchange()
        fake.queue_create_order(TimeoutError("response lost"))
        fake.seed_order(
            client_order_id="ht-abc",
            status="closed",
            filled="0.0001",
            exchange_order_id="KRAKEN-9",
        )
        gateway = KrakenGateway(lambda: fake)

        with self.assertRaises(TimeoutError):
            await gateway.create_market_order(
                asset="BTC/USDT",
                side="buy",
                amount=Decimal("0.0001"),
                client_order_id="ht-abc",
            )
        recovery_gateway = KrakenGateway(lambda: fake)
        order = await recovery_gateway.fetch_order_by_client_id("ht-abc", "BTC/USDT")

        self.assertIsNotNone(order)
        self.assertEqual(order.exchange_order_id, "KRAKEN-9")
        self.assertEqual(fake.create_order_calls, 1)

    async def test_rules_and_balances_are_normalized_to_decimal(self) -> None:
        fake = FakeKrakenExchange()
        gateway = KrakenGateway(lambda: fake)

        rules = await gateway.load_rules("BTC/USDT")
        balances = await gateway.fetch_balances()

        self.assertEqual(rules.min_amount, Decimal("0.00001"))
        self.assertEqual(rules.min_cost, Decimal("5"))
        self.assertEqual(balances["USDT"], Decimal("14.82"))

    async def test_context_manager_closes_exchange_after_cancellation(self) -> None:
        fake = FakeKrakenExchange()
        gateway = KrakenGateway(lambda: fake)

        with self.assertRaises(asyncio.CancelledError):
            async with gateway:
                raise asyncio.CancelledError()

        self.assertTrue(fake.closed)

    async def test_cancelled_remote_request_closes_the_cycle_client(self) -> None:
        fake = FakeKrakenExchange()

        async def cancelled_fetch_orders(symbol: str | None = None) -> list[dict[str, object]]:
            raise asyncio.CancelledError()

        fake.fetch_orders = cancelled_fetch_orders
        gateway = KrakenGateway(lambda: fake)

        with self.assertRaises(asyncio.CancelledError):
            await gateway.fetch_orders("BTC/USDT")

        self.assertTrue(fake.closed)
