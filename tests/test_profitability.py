import tempfile
import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from hermes_trading.costs import CostEvent, CostLedger
from hermes_trading.profitability import (
    ProfitabilityService,
    economic_gate,
    profitability_gate_snapshot,
    render_profitability_markdown,
    update_profitability_artifacts,
)
from hermes_trading.notifications import build_profitability_notification
from hermes_trading.loop import _attach_profitability_gate, _update_profitability_best_effort


NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)


class ProfitabilityTests(unittest.TestCase):
    def test_net_pnl_subtracts_all_cost_categories(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("20"),
            costs={
                "kraken_fee": Decimal("2"),
                "slippage": Decimal("1"),
                "railway_worker": Decimal("5"),
                "openai": Decimal("1"),
            },
            capital=Decimal("100"),
            days=Decimal("10"),
        )

        self.assertEqual(report.net_pnl, Decimal("11"))
        self.assertEqual(report.trading_costs, Decimal("3"))
        self.assertEqual(report.operating_costs, Decimal("6"))

    def test_missing_cost_blocks_extended_live(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("20"),
            costs={"kraken_fee": Decimal("2")},
            missing_required_costs=("railway_worker",),
            capital=Decimal("100"),
            days=Decimal("10"),
        )

        gate = economic_gate(report)

        self.assertFalse(gate.passed)
        self.assertIn("railway_worker cost missing", gate.reasons)

    def test_unknown_provider_cost_blocks_extended_live(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = CostLedger(Path(temp_dir))
            ledger.record(CostEvent("openai", None, "USD", "missing", "responses_api", NOW))
            report = ProfitabilityService(ledger).calculate(
                [], capital=Decimal("100"), days=Decimal("10")
            )

        self.assertIn("openai cost missing", economic_gate(report).reasons)

    def test_stress_reduces_pnl_and_increases_costs(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("100"),
            costs={"railway_worker": Decimal("60")},
            capital=Decimal("1000"),
            days=Decimal("10"),
        )

        self.assertEqual(report.stressed_net_pnl, Decimal("5"))

    def test_service_calculates_from_closed_positions_and_cost_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = CostLedger(Path(temp_dir))
            ledger.record(CostEvent("railway_worker", Decimal("5"), "USD", "estimated", "configured", NOW))
            positions = [{"status": "closed", "realized_pnl": "20"}, {"status": "open", "realized_pnl": "999"}]

            report = ProfitabilityService(ledger).calculate(positions, capital=Decimal("100"), days=Decimal("10"))

            self.assertEqual(report.gross_pnl, Decimal("20"))
            self.assertEqual(report.net_pnl, Decimal("15"))

    def test_markdown_summary_is_compact_and_human_visible(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("20"),
            costs={"kraken_fee": Decimal("2"), "railway_worker": Decimal("5")},
            capital=Decimal("100"),
            days=Decimal("10"),
        )

        message = render_profitability_markdown(report)

        self.assertIn("Hermes Trading | Profitability", message)
        self.assertIn("Gross: 20 USD", message)
        self.assertIn("Net: 13 USD", message)
        self.assertIn("Stress:", message)

    def test_notification_uses_profitability_renderer(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("20"),
            costs={"railway_worker": Decimal("5")},
            capital=Decimal("100"),
            days=Decimal("10"),
        )

        self.assertEqual(build_profitability_notification(report), render_profitability_markdown(report))

    def test_gate_snapshot_is_compact_and_explains_blocking_reason(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("0"),
            costs={"railway_worker": Decimal("0.04")},
            capital=Decimal("100"),
            days=Decimal("30"),
        )

        snapshot = profitability_gate_snapshot(report)

        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(snapshot["net_pnl_usd"], "-0.04")
        self.assertIn("projected net 30d is not positive", snapshot["reasons"])

    def test_update_profitability_artifacts_reads_state_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "positions.json").write_text(
                json.dumps([
                    {"status": "closed", "realized_pnl": "20"},
                    {"status": "open", "realized_pnl": "999"},
                ]),
                encoding="utf-8",
            )
            ledger = CostLedger(state)
            ledger.record(CostEvent("railway_worker", Decimal("5"), "USD", "estimated", "configured", NOW))

            report = update_profitability_artifacts(state, capital=Decimal("100"), days=Decimal("10"))

            self.assertEqual(report.net_pnl, Decimal("15"))
            self.assertTrue((state / "profitability.json").exists())
            self.assertIn("Hermes Trading | Profitability", (state / "profitability_report.md").read_text())

    def test_update_marks_railway_cost_missing_when_no_cost_is_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = update_profitability_artifacts(
                Path(temp_dir), capital=Decimal("100"), days=Decimal("30")
            )

        self.assertIn("railway_worker", report.missing_required_costs)
        self.assertFalse(economic_gate(report).passed)


class ProfitabilityLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_best_effort_update_returns_report_for_notification_scheduling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            report = await _update_profitability_best_effort(state)

        self.assertIsNotNone(report)
        self.assertEqual(report.gross_pnl, Decimal("0"))

    async def test_heartbeat_includes_economic_gate_snapshot(self):
        report = ProfitabilityService.calculate_from_values(
            gross_pnl=Decimal("0"),
            costs={"railway_worker": Decimal("0.04")},
            capital=Decimal("100"),
            days=Decimal("30"),
        )

        heartbeat = _attach_profitability_gate({"status": "ok"}, report)

        self.assertEqual(heartbeat["status"], "ok")
        self.assertEqual(heartbeat["economic_gate"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
