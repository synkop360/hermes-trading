import json
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from hermes_trading.costs import (
    ConfiguredMonthlyCostSource,
    CostEvent,
    CostLedger,
    RailwayCostSource,
    kraken_fee_event,
    openai_cost_event,
    openai_usage_event,
    slippage_cost_event,
)


NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)


class FailingApi:
    def monthly_cost_usd(self):
        raise PermissionError("unauthorized")


class WorkingApi:
    def monthly_cost_usd(self):
        return Decimal("12")


class CostLedgerTests(unittest.TestCase):
    def test_unknown_railway_cost_uses_configured_monthly_fallback(self):
        source = RailwayCostSource(api=FailingApi(), fallback_monthly=Decimal("8"))

        cost = source.cost_for_window(days=Decimal("15"), month_days=Decimal("30"), now=NOW)

        self.assertEqual(cost.amount, Decimal("4"))
        self.assertEqual(cost.quality, "estimated")
        self.assertEqual(cost.category, "railway_worker")

    def test_missing_real_and_fallback_cost_is_incomplete_not_zero(self):
        source = RailwayCostSource(api=FailingApi(), fallback_monthly=None)

        cost = source.cost_for_window(days=Decimal("15"), month_days=Decimal("30"), now=NOW)

        self.assertIsNone(cost.amount)
        self.assertEqual(cost.quality, "missing")

    def test_real_railway_cost_has_priority_over_configured_fallback(self):
        source = RailwayCostSource(api=WorkingApi(), fallback_monthly=Decimal("8"))

        cost = source.cost_for_window(days=Decimal("15"), month_days=Decimal("30"), now=NOW)

        self.assertEqual(cost.amount, Decimal("6"))
        self.assertEqual(cost.quality, "actual")

    def test_openai_usage_records_versioned_price(self):
        event = openai_cost_event(
            input_tokens=1000,
            output_tokens=200,
            model="gpt-test",
            pricing={
                "version": "2026-07-30",
                "input_per_1m": "2",
                "output_per_1m": "8",
            },
            occurred_at=NOW,
        )

        self.assertEqual(event.amount, Decimal("0.0036"))
        self.assertEqual(event.metadata["pricing_version"], "2026-07-30")

    def test_openai_usage_without_pricing_is_missing_not_zero(self):
        event = openai_usage_event(
            input_tokens=1000,
            output_tokens=200,
            model="gpt-test",
            pricing=None,
            occurred_at=NOW,
        )

        self.assertIsNone(event.amount)
        self.assertEqual(event.quality, "missing")

    def test_append_only_ledger_projects_totals_and_missing_costs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = CostLedger(Path(temp_dir))

            ledger.record(CostEvent("railway_worker", None, "USD", "missing", "railway", NOW, metadata={"reason": "no token"}))
            ledger.record(CostEvent("openai", Decimal("0.0036"), "USD", "actual", "responses_api", NOW))

            events = [json.loads(line) for line in (Path(temp_dir) / "cost_events.jsonl").read_text().splitlines()]
            projection = json.loads((Path(temp_dir) / "costs.json").read_text())
            self.assertEqual(len(events), 2)
            self.assertEqual(projection["totals"]["openai"], "0.0036")
            self.assertEqual(projection["missing"], ["railway_worker"])

    def test_configured_monthly_source_respects_allocation(self):
        source = ConfiguredMonthlyCostSource(monthly_cost=Decimal("20"), allocation_pct=Decimal("25"))

        cost = source.cost_for_window(days=Decimal("15"), month_days=Decimal("30"), now=NOW)

        self.assertEqual(cost.amount, Decimal("2.5"))

    def test_kraken_fee_and_slippage_events_are_positive_costs(self):
        fee = kraken_fee_event(
            amount=Decimal("0.01"),
            currency="USDT",
            asset="BTC/USDT",
            order_id="KRAKEN-1",
            occurred_at=NOW,
        )
        slippage = slippage_cost_event(
            side="buy",
            quantity=Decimal("0.1"),
            reference_price=Decimal("100"),
            average_price=Decimal("101"),
            asset="BTC/USDT",
            order_id="KRAKEN-1",
            occurred_at=NOW,
        )

        self.assertEqual(fee.category, "kraken_fee")
        self.assertEqual(fee.amount, Decimal("0.01"))
        self.assertEqual(slippage.category, "slippage")
        self.assertEqual(slippage.amount, Decimal("0.1"))


if __name__ == "__main__":
    unittest.main()
