from decimal import Decimal
import unittest

from hermes_trading.domain import Position, PositionStatus, decimal_from, json_value


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
