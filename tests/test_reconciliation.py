from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from hermes_trading.domain import Decision, OrderIntent, OrderStatus
from hermes_trading.exchange import KrakenGateway
from hermes_trading.persistence import StateRepository
from hermes_trading.reconciliation import ReconciliationService
from tests.fakes.kraken import FakeKrakenExchange


def pending_intent(client_order_id: str) -> tuple[Decision, OrderIntent]:
    decision = Decision(
        decision_id=f"decision-{client_order_id}",
        asset="BTC/USDT",
        action="enter",
        reason="recovery",
        candle_id=f"BTC/USDT:1h:{client_order_id}",
        strategy_version="69",
        reference_price=Decimal("65000"),
        decided_at="2026-07-30T12:00:00+00:00",
    )
    return decision, OrderIntent(
        client_order_id=client_order_id,
        decision_id=decision.decision_id,
        asset="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.0001"),
        reference_price=Decimal("65000"),
        created_at="2026-07-30T12:00:00+00:00",
        position_id=f"position-{client_order_id}",
        stop_price=Decimal("64000"),
    )


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary_directory.name)
        self.db_path = self.state_dir / "trading.db"
        self.repository = StateRepository.open(self.db_path)
        self.repository.migrate()

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    async def test_lost_response_is_found_by_client_id_without_resubmit(self) -> None:
        decision, intent = pending_intent("ht-known")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        fake.seed_order(client_order_id="ht-known", status="closed", filled="0.0001")

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertTrue(report.safe_for_new_entries)
        self.assertEqual(self.repository.order_by_client_id("ht-known").status, OrderStatus.FILLED)
        self.assertEqual(self.repository.open_position("BTC/USDT").position_id, "position-ht-known")
        self.assertEqual(self.repository.open_position("BTC/USDT").strategy_version, "69")
        self.assertEqual(fake.create_order_calls, 0)

    async def test_missing_balance_for_a_reconstructed_position_blocks_entries(self) -> None:
        decision, intent = pending_intent("ht-missing-balance")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        fake.seed_order(client_order_id="ht-missing-balance", status="closed", filled="0.0001")
        fake._balances = {"free": {"USDT": "14.82"}}

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "missing_balance")

    async def test_unknown_remote_order_freezes_entries_and_is_durable(self) -> None:
        fake = FakeKrakenExchange()
        fake.seed_unmatched_open_order(asset="BTC/USDT")

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "unknown_remote_order")
        self.assertTrue(self.repository.has_unresolved_incidents())

    async def test_critical_unknown_order_remains_blocking_after_it_disappears(self) -> None:
        fake = FakeKrakenExchange()
        fake.seed_unmatched_open_order(asset="BTC/USDT")
        service = ReconciliationService(self.repository)

        await service.run(KrakenGateway(lambda: fake))
        fake._orders.clear()
        report = await service.run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertTrue(self.repository.has_unresolved_incidents())

    async def test_restart_reconstructs_one_position_exactly_once(self) -> None:
        decision, intent = pending_intent("ht-restart")
        self.repository.persist_execution_intent(decision, intent)
        self.repository.close()
        self.repository = StateRepository.open(self.db_path)
        self.repository.migrate()
        fake = FakeKrakenExchange()
        fake.seed_order(client_order_id="ht-restart", status="closed", filled="0.0001")
        service = ReconciliationService(self.repository)

        await service.run(KrakenGateway(lambda: fake))
        report = await service.run(KrakenGateway(lambda: fake))

        positions = self.repository.export_canonical_tables()["positions"]
        self.assertTrue(report.safe_for_new_entries)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["position_id"], "position-ht-restart")

    async def test_partial_fill_blocks_entries_and_requests_fast_follow_up(self) -> None:
        decision, intent = pending_intent("ht-partial")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        fake.seed_order(client_order_id="ht-partial", status="open", filled="0.00005")

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.next_reconciliation_seconds, 5)
        self.assertEqual(report.issues[0].code, "transient_remote_order")

    async def test_transient_cadence_backs_off_then_returns_to_normal_after_fill(self) -> None:
        decision, intent = pending_intent("ht-cadence")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        order_id = fake.seed_order(client_order_id="ht-cadence", status="open", filled="0.00005")
        service = ReconciliationService(self.repository)

        first = await service.run(KrakenGateway(lambda: fake))
        second = await service.run(KrakenGateway(lambda: fake))
        fake._orders[order_id].update(status="closed", filled="0.0001", remaining="0")
        terminal = await service.run(KrakenGateway(lambda: fake))

        self.assertEqual(first.next_reconciliation_seconds, 5)
        self.assertEqual(second.next_reconciliation_seconds, 10)
        self.assertTrue(terminal.safe_for_new_entries)
        self.assertEqual(terminal.next_reconciliation_seconds, 60)

    async def test_remote_order_with_a_different_side_is_a_critical_mismatch(self) -> None:
        decision, intent = pending_intent("ht-wrong-side")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        fake.seed_order(
            client_order_id="ht-wrong-side",
            side="sell",
            status="closed",
            filled="0.0001",
        )

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "remote_order_mismatch")
        self.assertEqual(report.issues[0].severity, "critical")

    async def test_quantity_mismatch_never_constructs_a_local_position(self) -> None:
        decision, intent = pending_intent("ht-wrong-quantity")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        fake.seed_order(
            client_order_id="ht-wrong-quantity",
            status="closed",
            amount="0.0002",
            filled="0.0002",
        )

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "remote_order_mismatch")
        self.assertIsNone(self.repository.open_position("BTC/USDT"))

    async def test_duplicate_remote_client_id_is_ambiguous_and_blocks_entries(self) -> None:
        decision, intent = pending_intent("ht-duplicate")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        fake.seed_order(
            client_order_id="ht-duplicate",
            exchange_order_id="KRAKEN-duplicate-1",
            status="closed",
            filled="0.0001",
        )
        fake.seed_order(
            client_order_id="ht-duplicate",
            exchange_order_id="KRAKEN-duplicate-2",
            status="closed",
            filled="0.0001",
        )

        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "duplicate_remote_client_id")

    async def test_terminal_fee_difference_is_not_silently_overwritten(self) -> None:
        decision, intent = pending_intent("ht-fee-change")
        self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange()
        order_id = fake.seed_order(client_order_id="ht-fee-change", status="closed", filled="0.0001")
        service = ReconciliationService(self.repository)
        await service.run(KrakenGateway(lambda: fake))
        fake._orders[order_id]["fee"]["cost"] = "0.01"

        report = await service.run(KrakenGateway(lambda: fake))

        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "remote_order_mismatch")

    async def test_report_is_projected_as_human_readable_system_status(self) -> None:
        fake = FakeKrakenExchange()
        fake.seed_unmatched_open_order(asset="BTC/USDT")

        await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))

        status = json.loads((self.state_dir / "system_status.json").read_text(encoding="utf-8"))
        self.assertFalse(status["reconciliation"]["safe_for_new_entries"])
        self.assertEqual(status["reconciliation"]["issues"][0]["code"], "unknown_remote_order")
