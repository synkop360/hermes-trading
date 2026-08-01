from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.domain import Decision, OrderStatus, Position, PositionStatus
from hermes_trading.costs import CostLedger
from hermes_trading.execution import ExecutionService, client_order_id
from hermes_trading.exchange import ExchangeOrder, KrakenGateway
from hermes_trading.persistence import StateRepository
from hermes_trading.risk import RiskDecision
from tests.fakes.kraken import FakeKrakenExchange


def sample_signal(*, action: str = "enter", candle_id: str = "btc-1h-20260730T200000Z") -> Decision:
    return Decision(
        decision_id=f"decision-{action}-{candle_id}",
        asset="BTC/USDT",
        action=action,
        reason="rsi_entry",
        candle_id=candle_id,
        strategy_version="69",
        reference_price=Decimal("65000"),
        decided_at="2026-07-30T20:00:00+00:00",
    )


def sample_risk_decision(*, allowed: bool = True) -> RiskDecision:
    return RiskDecision(
        risk_decision_id="risk-decision-1",
        allowed=allowed,
        reason_code="authorized" if allowed else "spread_limit",
        notional=Decimal("5") if allowed else None,
        decided_at="2026-07-30T20:00:00+00:00",
    )


def sample_position() -> Position:
    return Position(
        position_id="position-btc-1",
        asset="BTC/USDT",
        status=PositionStatus.OPEN,
        quantity=Decimal("0.00007692307692307692307692307692"),
        entry_price=Decimal("65000"),
        stop_price=Decimal("64675"),
        strategy_version="69",
        opened_at="2026-07-30T20:00:00+00:00",
    )


class InspectingGateway:
    def __init__(self, assert_before_call) -> None:
        self._assert_before_call = assert_before_call
        self.market_calls = 0
        self.stop_calls = 0

    async def create_market_order(self, *, asset, side, amount, client_order_id):
        self._assert_before_call(client_order_id)
        self.market_calls += 1
        return _order(client_order_id=client_order_id, asset=asset, side=side, amount=amount)

    async def create_stop_order(self, *, asset, side, amount, stop_price, client_order_id):
        self._assert_before_call(client_order_id)
        self.stop_calls += 1
        order = _order(
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            amount=amount,
            order_type="stop-loss",
        )
        return ExchangeOrder(**{**order.__dict__, "trigger_price": stop_price})


class UnknownStatusGateway(InspectingGateway):
    async def create_market_order(self, *, asset, side, amount, client_order_id):
        self._assert_before_call(client_order_id)
        self.market_calls += 1
        return _order(
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            amount=amount,
            status="unknown",
        )


class RejectedMarketGateway(InspectingGateway):
    async def create_market_order(self, *, asset, side, amount, client_order_id):
        self._assert_before_call(client_order_id)
        self.market_calls += 1
        return _order(
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            amount=amount,
            status=OrderStatus.REJECTED,
        )


class RejectedStopGateway(InspectingGateway):
    async def create_stop_order(self, *, asset, side, amount, stop_price, client_order_id):
        self._assert_before_call(client_order_id)
        self.stop_calls += 1
        order = _order(
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            amount=amount,
            order_type="stop-loss",
            status=OrderStatus.REJECTED,
        )
        return ExchangeOrder(**{**order.__dict__, "trigger_price": stop_price})


def _order(
    *,
    client_order_id: str,
    asset: str,
    side: str,
    amount: Decimal,
    order_type: str = "market",
    status: str = "pending",
    filled: Decimal | None = None,
    average_price: Decimal | None = None,
    fee: Decimal = Decimal("0"),
) -> ExchangeOrder:
    timestamp = datetime(2026, 7, 30, 20, 0, tzinfo=UTC).isoformat()
    return ExchangeOrder(
        exchange_order_id="KRAKEN-1",
        client_order_id=client_order_id,
        asset=asset,
        side=side,
        order_type=order_type,
        status=status,
        amount=amount,
        filled=filled if filled is not None else Decimal("0"),
        remaining=amount - (filled if filled is not None else Decimal("0")),
        average_price=average_price,
        fee=fee,
        fee_currency="USDT" if fee else None,
        created_at=timestamp,
        updated_at=timestamp,
    )


class ExecutionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = StateRepository.open(Path(self.temporary_directory.name) / "trading.db")
        self.repository.migrate()
        self.service = ExecutionService(self.repository, allow_submission=True)

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def test_client_order_id_is_stable_ascii_and_kraken_safe_length(self) -> None:
        identifier = client_order_id("BTC/USDT", "buy", "btc-1h-20260730T200000Z", "69")

        self.assertEqual(identifier, "ht-3db38bc9117ba016")
        self.assertTrue(identifier.isascii())
        self.assertLessEqual(len(identifier), 32)

    def test_client_order_id_rejects_non_ascii_input(self) -> None:
        with self.assertRaises(ValueError):
            client_order_id("BTC/USDT", "buy", "bougie-" + chr(233), "69")

    async def test_entry_intent_exists_before_exchange_call(self) -> None:
        gateway = InspectingGateway(
            lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))
        )

        result = await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)

        self.assertEqual(result.status, "submitted")
        self.assertEqual(gateway.market_calls, 1)
        self.assertEqual(self.repository.export_canonical_tables()["orders"][0]["status"], "submitted")

    async def test_filled_order_records_fee_and_slippage_costs(self) -> None:
        ledger = CostLedger(Path(self.temporary_directory.name))
        service = ExecutionService(self.repository, allow_submission=True, cost_ledger=ledger)

        class FilledGateway(InspectingGateway):
            async def create_market_order(self, *, asset, side, amount, client_order_id):
                self._assert_before_call(client_order_id)
                return _order(
                    client_order_id=client_order_id,
                    asset=asset,
                    side=side,
                    amount=amount,
                    status=OrderStatus.FILLED,
                    filled=amount,
                    average_price=Decimal("65100"),
                    fee=Decimal("0.01"),
                )

        result = await service.submit_entry(
            sample_signal(),
            sample_risk_decision(),
            FilledGateway(lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))),
        )

        self.assertEqual(result.status, "submitted")
        totals = ledger.costs_between()
        self.assertEqual(totals["kraken_fee"], Decimal("0.01"))
        self.assertEqual(totals["slippage"], Decimal("0.007692307692307692307692307692"))

    async def test_default_service_is_inert_without_an_explicit_submission_guard(self) -> None:
        gateway = InspectingGateway(lambda identifier: self.fail("exchange must not be called"))
        service = ExecutionService(self.repository)

        result = await service.submit_entry(sample_signal(), sample_risk_decision(), gateway)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "submission_disabled")
        self.assertEqual(self.repository.export_canonical_tables()["order_intents"], [])

    async def test_timeout_requires_reconciliation_without_blind_resubmission(self) -> None:
        fake = FakeKrakenExchange()
        fake.queue_create_order(TimeoutError("lost response"))
        gateway = KrakenGateway(lambda: fake)

        first = await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)
        second = await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)

        self.assertEqual(first.status, "reconciliation_required")
        self.assertEqual(second.status, "reconciliation_required")
        self.assertEqual(fake.create_order_calls, 1)

    async def test_unknown_exchange_status_requires_reconciliation(self) -> None:
        gateway = UnknownStatusGateway(
            lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))
        )

        result = await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)

        self.assertEqual(result.status, "reconciliation_required")
        self.assertEqual(gateway.market_calls, 1)
        self.assertEqual(self.repository.export_canonical_tables()["orders"][0]["status"], "error")

    async def test_explicit_market_rejection_is_durable_and_terminal(self) -> None:
        gateway = RejectedMarketGateway(
            lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))
        )

        result = await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "exchange_rejected")
        tables = self.repository.export_canonical_tables()
        self.assertEqual(tables["orders"][0]["status"], "rejected")
        self.assertEqual(tables["incidents"][0]["reason"], "exchange_rejected")

    async def test_explicit_protective_stop_rejection_emergency_closes_and_halts_entries(self) -> None:
        gateway = RejectedStopGateway(
            lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))
        )

        result = await self.service.ensure_protective_stop(
            sample_signal(action="protective_stop"), sample_position(), gateway
        )

        self.assertEqual(result.status, "emergency_closed")
        tables = self.repository.export_canonical_tables()
        self.assertIn("stop-loss", [intent["order_type"] for intent in tables["order_intents"]])
        self.assertIn("exchange_rejected", [incident["reason"] for incident in tables["incidents"]])
        self.assertTrue(self.repository.risk_state().kill_switch)
        self.assertEqual(gateway.market_calls, 1)

    async def test_rejected_risk_decision_never_submits_an_order(self) -> None:
        gateway = InspectingGateway(lambda identifier: self.fail("exchange must not be called"))

        result = await self.service.submit_entry(sample_signal(), sample_risk_decision(allowed=False), gateway)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(gateway.market_calls, 0)

    async def test_exit_persists_sell_intent_before_submission(self) -> None:
        gateway = InspectingGateway(
            lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))
        )

        result = await self.service.submit_exit(sample_signal(action="exit"), sample_position(), gateway)

        self.assertEqual(result.status, "submitted")
        self.assertEqual(gateway.market_calls, 1)

    async def test_protective_stop_persists_stop_intent_before_submission(self) -> None:
        gateway = InspectingGateway(
            lambda identifier: self.assertIsNotNone(self.repository.intent_by_client_id(identifier))
        )

        result = await self.service.ensure_protective_stop(sample_signal(action="protective_stop"), sample_position(), gateway)

        self.assertEqual(result.status, "submitted")
        self.assertEqual(gateway.stop_calls, 1)
