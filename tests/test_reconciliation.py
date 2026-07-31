from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from hermes_trading.domain import Decision, OrderIntent, OrderRecord, OrderStatus, Position, PositionStatus
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
        fake._balances["free"]["BTC"] = "0.0001"
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

    async def test_filled_sell_closes_position_once_after_restart(self) -> None:
        decision, entry = pending_intent("ht-entry")
        self.repository.persist_execution_intent(decision, entry)
        self.repository.save_position(Position("position-ht-entry", "BTC/USDT", PositionStatus.OPEN, Decimal("0.0001"), Decimal("65000"), Decimal("64000"), "69", "2026-07-30T12:00:00+00:00", fees=Decimal("0.01")))
        exit_decision = Decision("decision-exit", "BTC/USDT", "exit", "exit", "exit-candle", "69", Decimal("66000"), "2026-07-30T13:00:00+00:00")
        self.repository.persist_execution_intent(exit_decision, OrderIntent("ht-exit", "decision-exit", "BTC/USDT", "sell", "market", Decimal("0.0001"), Decimal("66000"), "2026-07-30T13:00:00+00:00", "position-ht-entry"))
        self.repository.close(); self.repository = StateRepository.open(self.db_path); self.repository.migrate()
        fake = FakeKrakenExchange(); fake.seed_order(client_order_id="ht-exit", side="sell", status="closed", filled="0.0001", fee="0.02")
        next(iter(fake._orders.values()))["average"] = "66000"
        fake._balances = {"free": {"USDT": "21.40", "BTC": "0"}}
        service = ReconciliationService(self.repository)
        await service.run(KrakenGateway(lambda: fake)); await service.run(KrakenGateway(lambda: fake))
        position = self.repository.export_canonical_tables()["positions"][0]
        self.assertEqual(position["status"], "closed")
        self.assertEqual(position["realized_pnl"], "0.0700")

    async def test_cancelled_order_is_terminal_and_returns_normal_cadence(self) -> None:
        decision, intent = pending_intent("ht-cancelled"); self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange(); fake.seed_order(client_order_id="ht-cancelled", status="canceled")
        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        self.assertTrue(report.safe_for_new_entries); self.assertEqual(report.next_reconciliation_seconds, 60)

    async def test_balance_shortfall_for_open_position_blocks_entries(self) -> None:
        decision, intent = pending_intent("ht-shortfall"); self.repository.persist_execution_intent(decision, intent)
        self.repository.save_position(Position("position-ht-shortfall", "BTC/USDT", PositionStatus.OPEN, Decimal("0.0001"), Decimal("65000"), Decimal("64000"), "69", "2026-07-30T12:00:00+00:00"))
        fake = FakeKrakenExchange(); fake.seed_order(client_order_id="ht-shortfall", status="closed", filled="0.0001"); fake._balances = {"free": {"USDT": "14.82", "BTC": "0.00001"}}
        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        self.assertFalse(report.safe_for_new_entries); self.assertEqual(report.issues[0].code, "balance_shortfall")

    async def test_duplicate_remote_client_id_uses_fast_backoff(self) -> None:
        decision, intent = pending_intent("ht-ambiguous"); self.repository.persist_execution_intent(decision, intent)
        fake = FakeKrakenExchange(); fake.seed_order(client_order_id="ht-ambiguous", exchange_order_id="K1"); fake.seed_order(client_order_id="ht-ambiguous", exchange_order_id="K2")
        service = ReconciliationService(self.repository)
        first = await service.run(KrakenGateway(lambda: fake)); second = await service.run(KrakenGateway(lambda: fake))
        self.assertEqual(first.next_reconciliation_seconds, 5); self.assertEqual(second.next_reconciliation_seconds, 10)

    async def test_repeated_partial_sell_snapshot_applies_only_its_fill_delta_after_restart(self) -> None:
        decision, entry = pending_intent("ht-partial-entry"); self.repository.persist_execution_intent(decision, entry)
        self.repository.save_position(Position("position-ht-partial-entry", "BTC/USDT", PositionStatus.OPEN, Decimal("0.0001"), Decimal("65000"), Decimal("64000"), "69", "2026-07-30T12:00:00+00:00", fees=Decimal("0.01")))
        exit_decision = Decision("decision-partial-exit", "BTC/USDT", "exit", "exit", "partial-exit", "69", Decimal("66000"), "2026-07-30T13:00:00+00:00")
        self.repository.persist_execution_intent(exit_decision, OrderIntent("ht-partial-exit", exit_decision.decision_id, "BTC/USDT", "sell", "market", Decimal("0.0001"), Decimal("66000"), "2026-07-30T13:00:00+00:00", "position-ht-partial-entry"))
        fake = FakeKrakenExchange(); fake.seed_order(client_order_id="ht-partial-exit", side="sell", status="open", filled="0.00005", fee="0.01"); next(iter(fake._orders.values()))["average"] = "66000"
        fake._balances = {"free": {"USDT": "18", "BTC": "0.00005"}}
        await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        self.repository.close(); self.repository = StateRepository.open(self.db_path); self.repository.migrate()
        await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        position = self.repository.position_by_id("position-ht-partial-entry")
        self.assertEqual(position.quantity, Decimal("0.00005")); self.assertEqual(position.realized_pnl, Decimal("0.035"))

    async def test_absent_terminal_buy_without_position_is_critical_even_with_zero_base_balance(self) -> None:
        decision, intent = pending_intent("ht-lost-position"); self.repository.persist_execution_intent(decision, intent)
        self.repository.upsert_order(OrderRecord("kraken:lost", "ht-lost-position", "BTC/USDT", "buy", "market", OrderStatus.FILLED, Decimal("0.0001"), Decimal("0.0001"), Decimal("65000"), Decimal("0"), "2026-07-30T12:00:00+00:00", "2026-07-30T12:00:00+00:00", "lost"))
        fake = FakeKrakenExchange(); fake._balances = {"free": {"USDT": "14", "BTC": "0"}}
        report = await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        self.assertFalse(report.safe_for_new_entries); self.assertEqual(report.issues[0].code, "missing_expected_position")

    async def test_partial_then_final_exit_keeps_total_fees_after_restart(self) -> None:
        decision, entry = pending_intent("ht-fee-entry"); self.repository.persist_execution_intent(decision, entry)
        self.repository.upsert_order(OrderRecord("kraken:entry", "ht-fee-entry", "BTC/USDT", "buy", "market", OrderStatus.FILLED, Decimal("0.0001"), Decimal("0.0001"), Decimal("65000"), Decimal("0.01"), "2026-07-30T12:00:00+00:00", "2026-07-30T12:00:00+00:00", "entry"))
        self.repository.save_position(Position("position-ht-fee-entry", "BTC/USDT", PositionStatus.OPEN, Decimal("0.0001"), Decimal("65000"), Decimal("64000"), "69", "2026-07-30T12:00:00+00:00", fees=Decimal("0.01")))
        exit_decision = Decision("decision-fee-exit", "BTC/USDT", "exit", "exit", "fee-exit", "69", Decimal("66000"), "2026-07-30T13:00:00+00:00")
        self.repository.persist_execution_intent(exit_decision, OrderIntent("ht-fee-exit", exit_decision.decision_id, "BTC/USDT", "sell", "market", Decimal("0.0001"), Decimal("66000"), "2026-07-30T13:00:00+00:00", "position-ht-fee-entry"))
        fake = FakeKrakenExchange(); order_id=fake.seed_order(client_order_id="ht-fee-exit", side="sell", status="open", filled="0.00005", fee="0.01"); fake._orders[order_id]["average"]="66000"; fake._balances={"free":{"USDT":"18","BTC":"0.00005"}}
        await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        self.repository.close(); self.repository=StateRepository.open(self.db_path); self.repository.migrate()
        fake._orders[order_id].update(status="closed", filled="0.0001", remaining="0"); fake._orders[order_id]["fee"]["cost"]="0.02"; fake._balances["free"]["BTC"]="0"
        await ReconciliationService(self.repository).run(KrakenGateway(lambda: fake))
        position=self.repository.position_by_id("position-ht-fee-entry")
        self.assertEqual(position.status, PositionStatus.CLOSED, repr(position)); self.assertEqual(position.quantity, Decimal("0.0001")); self.assertEqual(position.realized_pnl, Decimal("0.070")); self.assertEqual(position.fees, Decimal("0.03"))
