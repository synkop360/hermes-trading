from dataclasses import replace
from decimal import Decimal
import unittest

from hermes_trading.domain import (
    Decision,
    Fill,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Position,
    PositionStatus,
    decimal_from,
    json_value,
)


class DomainTests(unittest.TestCase):
    def test_decimal_from_rejects_binary_float(self):
        with self.assertRaises(TypeError):
            decimal_from(0.1)

    def test_position_serializes_decimals_as_strings(self):
        position = Position(
            position_id="pos-btc-1",
            asset="BTC/USDT",
            status=PositionStatus.OPEN,
            quantity=Decimal("0.00010"),
            entry_price=Decimal("65000.0"),
            stop_price=Decimal("64675.0"),
            strategy_version="69",
            opened_at="2026-07-30T20:00:00+00:00",
        )
        payload = json_value(position)
        self.assertEqual(payload["quantity"], "0.00010")
        self.assertEqual(payload["status"], "open")

    def test_domain_records_reject_binary_float_financial_fields(self):
        records_and_fields = (
            (
                Decision(
                    decision_id="decision-1",
                    asset="BTC/USDT",
                    action="enter",
                    reason="rsi_entry",
                    candle_id="btc-1h-1",
                    strategy_version="69",
                    reference_price=Decimal("65000.0"),
                    decided_at="2026-07-30T20:00:00+00:00",
                ),
                ("reference_price",),
            ),
            (
                OrderIntent(
                    client_order_id="ht-btc-1",
                    decision_id="decision-1",
                    asset="BTC/USDT",
                    side="buy",
                    order_type="market",
                    quantity=Decimal("0.00010"),
                    reference_price=Decimal("65000.0"),
                    created_at="2026-07-30T20:00:01+00:00",
                    stop_price=Decimal("64675.0"),
                ),
                ("quantity", "reference_price", "stop_price"),
            ),
            (
                OrderRecord(
                    order_id="order-1",
                    client_order_id="ht-btc-1",
                    asset="BTC/USDT",
                    side="buy",
                    order_type="market",
                    status=OrderStatus.PARTIALLY_FILLED,
                    requested_quantity=Decimal("0.00010"),
                    filled_quantity=Decimal("0.00005"),
                    average_fill_price=Decimal("65001.0"),
                    fees=Decimal("0.01"),
                    created_at="2026-07-30T20:00:01+00:00",
                    updated_at="2026-07-30T20:00:02+00:00",
                ),
                (
                    "requested_quantity",
                    "filled_quantity",
                    "average_fill_price",
                    "fees",
                ),
            ),
            (
                Fill(
                    fill_id="fill-1",
                    order_id="order-1",
                    asset="BTC/USDT",
                    side="buy",
                    quantity=Decimal("0.00005"),
                    price=Decimal("65001.0"),
                    fee=Decimal("0.01"),
                    filled_at="2026-07-30T20:00:02+00:00",
                ),
                ("quantity", "price", "fee"),
            ),
            (
                Position(
                    position_id="pos-btc-1",
                    asset="BTC/USDT",
                    status=PositionStatus.OPEN,
                    quantity=Decimal("0.00010"),
                    entry_price=Decimal("65000.0"),
                    stop_price=Decimal("64675.0"),
                    strategy_version="69",
                    opened_at="2026-07-30T20:00:00+00:00",
                    realized_pnl=Decimal("0"),
                    fees=Decimal("0.01"),
                ),
                ("quantity", "entry_price", "stop_price", "realized_pnl", "fees"),
            ),
        )

        for record, fields in records_and_fields:
            for field in fields:
                with self.subTest(record=type(record).__name__, field=field):
                    with self.assertRaises(TypeError):
                        replace(record, **{field: 0.1})

    def test_json_value_rejects_binary_float_at_any_depth(self):
        payload = {
            "orders": [
                {
                    "status": OrderStatus.PARTIALLY_FILLED,
                    "fills": (Decimal("0.00005"), {"fee": 0.1}),
                }
            ]
        }

        with self.assertRaises(TypeError):
            json_value(payload)

    def test_json_value_recursively_serializes_order_status_and_decimals(self):
        payload = {
            "orders": [
                {
                    "status": OrderStatus.PARTIALLY_FILLED,
                    "fills": (Decimal("0.00005"),),
                }
            ]
        }

        self.assertEqual(
            json_value(payload),
            {
                "orders": [
                    {
                        "status": "partially_filled",
                        "fills": ["0.00005"],
                    }
                ]
            },
        )
