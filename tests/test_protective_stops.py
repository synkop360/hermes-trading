from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.domain import Decision, OrderIntent, OrderRecord, OrderStatus, Position, PositionStatus
from hermes_trading.execution import ExecutionService
from hermes_trading.exchange import ExchangeOrder, MarketRules
from hermes_trading.persistence import StateRepository
from hermes_trading.reconciliation import ReconciliationService


def protective_decision(*, candle_id: str = "btc-1h-20260730T200000Z") -> Decision:
    return Decision(
        decision_id=f"decision-stop-{candle_id}",
        asset="BTC/USDT",
        action="protective_stop",
        reason="filled_entry_requires_stop",
        candle_id=candle_id,
        strategy_version="69",
        reference_price=Decimal("65000"),
        decided_at="2026-07-30T20:00:00+00:00",
    )


def position(*, quantity: str = "0.00007", stop_price: str = "64675") -> Position:
    return Position(
        position_id="position-btc-1",
        asset="BTC/USDT",
        status=PositionStatus.OPEN,
        quantity=Decimal(quantity),
        entry_price=Decimal("65000"),
        stop_price=Decimal(stop_price),
        strategy_version="69",
        opened_at="2026-07-30T20:00:00+00:00",
    )


class StopGateway:
    """A stateful exchange boundary fake for protective-order behavior."""

    def __init__(self) -> None:
        self.stop_error: BaseException | None = None
        self.fetch_error: BaseException | None = None
        self.stop_response_overrides: dict[str, object] = {}
        self.amount_precision = 8
        self.last_stop: ExchangeOrder | None = None
        self.market_sell_calls = 0
        self.cancelled_ids: list[str] = []
        self.orders: dict[str, ExchangeOrder] = {}
        self._sequence = 0

    async def load_rules(self, asset: str) -> MarketRules:
        return MarketRules(asset, Decimal("0.00001"), Decimal("5"), self.amount_precision, 2)

    async def fetch_orders(self, asset: str | None = None) -> tuple[ExchangeOrder, ...]:
        if self.fetch_error is not None:
            raise self.fetch_error
        return tuple(order for order in self.orders.values() if asset is None or order.asset == asset)

    async def create_stop_order(self, *, asset: str, side: str, amount: Decimal, stop_price: Decimal, client_order_id: str) -> ExchangeOrder:
        if self.stop_error is not None:
            raise self.stop_error
        order = self._order(asset, side, "stop-loss", amount, client_order_id, stop_price)
        if self.stop_response_overrides:
            order = ExchangeOrder(**{**order.__dict__, **self.stop_response_overrides})
        self.last_stop = order
        self.orders[order.exchange_order_id] = order
        return order

    async def create_market_order(self, *, asset: str, side: str, amount: Decimal, client_order_id: str) -> ExchangeOrder:
        if side == "sell":
            self.market_sell_calls += 1
        order = self._order(asset, side, "market", amount, client_order_id)
        self.orders[order.exchange_order_id] = order
        return order

    async def cancel_order(self, exchange_order_id: str, asset: str) -> ExchangeOrder:
        self.cancelled_ids.append(exchange_order_id)
        previous = self.orders[exchange_order_id]
        cancelled = ExchangeOrder(
            **{**previous.__dict__, "status": "cancelled", "updated_at": "2026-07-30T20:01:00+00:00"}
        )
        self.orders[exchange_order_id] = cancelled
        return cancelled

    def _order(self, asset: str, side: str, order_type: str, amount: Decimal, client_order_id: str, trigger_price: Decimal | None = None) -> ExchangeOrder:
        self._sequence += 1
        timestamp = datetime(2026, 7, 30, 20, 0, tzinfo=UTC).isoformat()
        return ExchangeOrder(
            exchange_order_id=f"KRAKEN-{self._sequence}",
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            order_type=order_type,
            status="pending",
            amount=amount,
            filled=Decimal("0"),
            remaining=amount,
            average_price=None,
            fee=Decimal("0"),
            fee_currency="USDT",
            created_at=timestamp,
            updated_at=timestamp,
            trigger_price=trigger_price,
        )


class ProtectiveStopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "trading.db"
        self.repository = StateRepository.open(self.database_path)
        self.repository.migrate()
        self.service = ExecutionService(self.repository, allow_submission=True)
        self.gateway = StopGateway()

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    async def test_stop_uses_actual_filled_quantity(self) -> None:
        await self.service.ensure_protective_stop(protective_decision(), position(), self.gateway)

        self.assertEqual(self.gateway.last_stop.amount, Decimal("0.00007"))

    async def test_stop_failure_closes_and_activates_kill_switch(self) -> None:
        self.gateway.stop_error = RuntimeError("stop rejected")

        result = await self.service.ensure_protective_stop(protective_decision(), position(), self.gateway)

        self.assertEqual(result.status, "emergency_closed")
        self.assertTrue(self.repository.risk_state().kill_switch)
        self.assertEqual(self.gateway.market_sell_calls, 1)
        incidents = self.repository.export_canonical_tables()["incidents"]
        self.assertEqual(incidents[0]["severity"], "critical")

    async def test_wrong_stop_response_is_not_accepted_as_protection(self) -> None:
        self.gateway.stop_response_overrides = {"side": "buy"}

        result = await self.service.ensure_protective_stop(
            protective_decision(), position(), self.gateway
        )

        self.assertEqual(result.status, "emergency_closed")
        self.assertTrue(self.repository.risk_state().kill_switch)
        self.assertEqual(self.gateway.market_sell_calls, 1)

    async def test_stop_response_with_wrong_trigger_is_not_accepted_as_protection(self) -> None:
        self.gateway.stop_response_overrides = {"trigger_price": Decimal("64674")}

        result = await self.service.ensure_protective_stop(
            protective_decision(), position(), self.gateway
        )

        self.assertEqual(result.status, "emergency_closed")
        self.assertTrue(self.repository.risk_state().kill_switch)

    async def test_precision_dust_fails_closed_instead_of_reporting_a_partial_stop(self) -> None:
        self.gateway.amount_precision = 5

        result = await self.service.ensure_protective_stop(
            protective_decision(), position(quantity="0.000076"), self.gateway
        )

        self.assertEqual(result.status, "emergency_closed")
        self.assertIsNone(self.gateway.last_stop)
        self.assertTrue(self.repository.risk_state().kill_switch)

    async def test_startup_order_lookup_failure_halts_and_emergency_closes(self) -> None:
        self.repository.save_position(position())
        self.gateway.fetch_error = TimeoutError("exchange unavailable")

        report = await ReconciliationService(self.repository).verify_protective_stops(
            self.gateway, self.service
        )

        self.assertFalse(report.safe_for_new_entries)
        self.assertTrue(self.repository.risk_state().kill_switch)
        self.assertEqual(self.gateway.market_sell_calls, 1)
        self.assertIn(
            "protective_stop_lookup_failed",
            [issue.code for issue in report.issues],
        )

    async def test_changed_stop_cancels_previous_remote_stop_before_replacing_it(self) -> None:
        old = position(stop_price="64680")
        await self.service.ensure_protective_stop(protective_decision(candle_id="first"), old, self.gateway)
        old_stop_id = self.gateway.last_stop.exchange_order_id

        await self.service.ensure_protective_stop(protective_decision(candle_id="second"), position(), self.gateway)

        self.assertEqual(self.gateway.cancelled_ids, [old_stop_id])
        self.assertIn(
            Decimal("64675"),
            [intent.stop_price for intent in self.repository.order_intents()],
        )
        active_stops = [order for order in self.gateway.orders.values() if order.order_type == "stop-loss" and order.status == "pending"]
        self.assertEqual(len(active_stops), 1)

    async def test_startup_missing_stop_emergency_closes_before_entries_are_safe(self) -> None:
        self.repository.save_position(position())

        report = await ReconciliationService(self.repository).verify_protective_stops(self.gateway, self.service)

        self.assertFalse(report.safe_for_new_entries)
        self.assertTrue(self.repository.risk_state().kill_switch)
        self.assertEqual(self.gateway.market_sell_calls, 1)

    async def test_repeated_startup_check_does_not_resubmit_emergency_market_sell(self) -> None:
        self.repository.save_position(position())
        reconciliation = ReconciliationService(self.repository)

        await reconciliation.verify_protective_stops(self.gateway, self.service)
        await reconciliation.verify_protective_stops(self.gateway, self.service)

        self.assertEqual(self.gateway.market_sell_calls, 1)

    async def test_startup_accepts_a_matching_active_stop(self) -> None:
        current = position()
        self.repository.save_position(current)
        await self.service.ensure_protective_stop(protective_decision(), current, self.gateway)

        report = await ReconciliationService(self.repository).verify_protective_stops(
            self.gateway, self.service
        )

        self.assertTrue(report.safe_for_new_entries)
        self.assertEqual(self.gateway.market_sell_calls, 0)

    async def test_restart_accepts_tick_normalized_remote_stop_for_raw_strategy_price(self) -> None:
        current = position(stop_price="64675.123")
        self.repository.save_position(current)
        await self.service.ensure_protective_stop(protective_decision(), current, self.gateway)

        self.repository.close()
        self.repository = StateRepository.open(self.database_path)
        self.repository.migrate()
        restarted = ExecutionService(self.repository, allow_submission=True)
        report = await ReconciliationService(self.repository).verify_protective_stops(
            self.gateway, restarted
        )

        self.assertTrue(report.safe_for_new_entries)
        self.assertFalse(self.repository.risk_state().kill_switch)
        self.assertEqual(self.gateway.market_sell_calls, 0)

    async def test_matching_stop_cancels_any_additional_active_stop(self) -> None:
        current = position()
        await self.service.ensure_protective_stop(protective_decision(), current, self.gateway)
        stale_decision = protective_decision(candle_id="stale")
        stale_remote = await self.gateway.create_stop_order(
            asset="BTC/USDT",
            side="sell",
            amount=current.quantity,
            stop_price=Decimal("64680"),
            client_order_id="ht-stale-stop",
        )
        self.repository.persist_execution_intent(
            stale_decision,
            OrderIntent(
                "ht-stale-stop", stale_decision.decision_id, "BTC/USDT", "sell",
                "stop-loss", current.quantity, Decimal("65000"),
                stale_decision.decided_at, current.position_id, Decimal("64680"),
            ),
        )
        self.repository.upsert_order(
            OrderRecord(
                f"kraken:{stale_remote.exchange_order_id}", "ht-stale-stop",
                "BTC/USDT", "sell", "stop-loss", OrderStatus.SUBMITTED,
                current.quantity, Decimal("0"), None, Decimal("0"),
                stale_remote.created_at, stale_remote.updated_at, stale_remote.exchange_order_id,
            )
        )

        await self.service.ensure_protective_stop(
            protective_decision(candle_id="repeat"), current, self.gateway
        )

        self.assertEqual(self.gateway.cancelled_ids, [stale_remote.exchange_order_id])


if __name__ == "__main__":
    unittest.main()
