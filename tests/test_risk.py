from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.config import ExecutionQualityLimits, resolve_execution_quality_limits
from hermes_trading.risk import (
    RiskContext,
    RiskEngine,
    resolve_risk_limits,
)


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary_directory.name)
        self.repository = StateRepository.open(self.state_dir / "trading.db")
        self.repository.migrate()
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        self.engine = RiskEngine(
            self.repository,
            ArtifactProjector(self.state_dir),
            now=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.repository.close()
        self.temporary_directory.cleanup()

    def test_size_is_minimum_of_all_caps(self) -> None:
        decision = self.engine.authorize_entry(
            context=self.context(
                equity=Decimal("1000"),
                free_quote=Decimal("100"),
                symbol_exposure=Decimal("0"),
                total_exposure=Decimal("0"),
            ),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.notional, Decimal("5"))

    def test_daily_loss_activates_kill_switch(self) -> None:
        decision = self.engine.authorize_entry(
            context=self.context(daily_pnl=Decimal("-5.01")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "daily_loss_limit")
        self.assertTrue(self.repository.risk_state().kill_switch)

    def test_below_exchange_minimum_is_no_order(self) -> None:
        decision = self.engine.authorize_entry(
            context=self.context(free_quote=Decimal("2")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "below_exchange_minimum")
        self.assertIsNone(decision.notional)

    def test_shib_uses_stricter_spread_and_slippage_limits(self) -> None:
        decision = self.engine.authorize_entry(
            context=self.context(symbol="SHIB/USDT", spread_bps=Decimal("35")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "spread_limit")

    def test_missing_or_stale_spread_is_no_trade(self) -> None:
        missing = self.engine.authorize_entry(
            context=self.context(spread_available=False),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )
        stale = self.engine.authorize_entry(
            context=self.context(spread_observed_at=self.now - timedelta(minutes=6)),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertEqual(missing.reason_code, "spread_unavailable")
        self.assertEqual(stale.reason_code, "spread_stale")

    def test_negative_or_future_spread_observation_is_no_trade(self) -> None:
        negative = self.engine.authorize_entry(
            context=self.context(spread_bps=Decimal("-0.01")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )
        future = self.engine.authorize_entry(
            context=self.context(spread_observed_at=self.now + timedelta(seconds=1)),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertEqual(negative.reason_code, "invalid_spread")
        self.assertEqual(future.reason_code, "spread_future")

    def test_rejects_invalid_risk_inputs_and_unknown_or_active_state(self) -> None:
        cases = (
            (self.context(), Decimal("0"), Decimal("1"), "invalid_stop_loss"),
            (self.context(), Decimal("0.01"), Decimal("1.01"), "invalid_position_size_r"),
            (self.context(free_quote=None), Decimal("0.01"), Decimal("1"), "unknown_balance"),
            (self.context(active_position=True), Decimal("0.01"), Decimal("1"), "active_position"),
        )

        for context, stop_loss_pct, position_size_r, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                decision = self.engine.authorize_entry(
                    context=context,
                    stop_loss_pct=stop_loss_pct,
                    position_size_r=position_size_r,
                    exchange_min_cost=Decimal("5"),
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, expected_reason)

    def test_open_incident_blocks_entry(self) -> None:
        self.repository.open_incident("test:operator", "operator review required")

        decision = self.engine.authorize_entry(
            context=self.context(),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "unresolved_incident")

    def test_resume_requires_explicit_authorization_no_incident_and_fresh_reconciliation(self) -> None:
        self.engine.activate_kill_switch("operator_pause")

        self.assertFalse(self.engine.resume(authorized=False))
        self.assertTrue(self.repository.risk_state().kill_switch)

        self.assertFalse(self.engine.resume(authorized=True))
        self.repository.mark_reconciliation_complete(self.now - timedelta(minutes=6))
        self.assertFalse(self.engine.resume(authorized=True))

        self.repository.mark_reconciliation_complete(self.now + timedelta(minutes=1))
        self.assertFalse(self.engine.resume(authorized=True))

        self.repository.mark_reconciliation_complete(self.now)
        self.repository.open_incident("test:operator", "operator review required")
        self.assertFalse(self.engine.resume(authorized=True))
        self.repository.resolve_incident("test:operator")

        self.assertTrue(self.engine.resume(authorized=True))
        self.assertFalse(self.repository.risk_state().kill_switch)

    def test_authorization_projects_human_readable_risk_status(self) -> None:
        decision = self.engine.authorize_entry(
            context=self.context(),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        import json

        payload = json.loads((self.state_dir / "risk_status.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["last_decision"]["reason_code"], decision.reason_code)
        self.assertEqual(payload["state"]["kill_switch"], False)
        self.assertEqual(payload["last_decision"]["notional"], "5")

    def test_decision_is_canonical_before_projection_and_recovers_after_projection_failure(self) -> None:
        class FailingProjector:
            def __init__(self, state_dir: Path) -> None:
                self.delegate = ArtifactProjector(state_dir)

            def append_event(self, name: str, payload: object) -> None:
                self.delegate.append_event(name, payload)

            def write_snapshot(self, name: str, payload: object) -> None:
                raise OSError("artifact unavailable")

        failing = RiskEngine(self.repository, FailingProjector(self.state_dir), now=lambda: self.now)
        with self.assertRaisesRegex(OSError, "artifact unavailable"):
            failing.authorize_entry(
                context=self.context(daily_pnl=Decimal("-5.01")),
                stop_loss_pct=Decimal("0.01"),
                position_size_r=Decimal("1"),
                exchange_min_cost=Decimal("5"),
            )

        records = self.repository.risk_decisions()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].reason_code, "daily_loss_limit")
        self.assertEqual(records[0].risk_state_revision, self.repository.risk_state().revision)
        self.assertEqual(self.repository.pending_risk_decision_count(), 1)

        self.repository.close()
        self.repository = StateRepository.open(self.state_dir / "trading.db")
        self.repository.migrate()
        self.assertEqual(self.repository.risk_decisions()[0].reason_code, "daily_loss_limit")

        recovered = RiskEngine(
            self.repository,
            ArtifactProjector(self.state_dir),
            now=lambda: self.now,
        )
        self.assertEqual(recovered.recover_pending_projections(), 1)
        self.assertEqual(self.repository.pending_risk_decision_count(), 0)
        journal = (self.state_dir / "risk_decisions.jsonl")
        self.assertTrue(journal.exists())
        self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 1)

    def test_limits_are_decimal_and_environment_overrides_are_strict(self) -> None:
        limits = resolve_risk_limits({"HERMES_TRADING_MAX_ORDER_USDT": "7.50"})

        self.assertEqual(limits.max_order_quote, Decimal("7.50"))
        with self.assertRaises(ValueError):
            resolve_risk_limits({"HERMES_TRADING_MAX_ORDER_USDT": "NaN"})

    def test_symbol_specific_quality_override_does_not_relax_shib_default(self) -> None:
        btc = resolve_execution_quality_limits(
            {"HERMES_TRADING_BTC_MAX_SPREAD_BPS": "17"},
            "BTC/USDT",
        )
        shib = resolve_execution_quality_limits({}, "SHIB/USDT")

        self.assertEqual(btc.max_spread_bps, Decimal("17"))
        self.assertEqual(shib.max_spread_bps, Decimal("30"))

    def test_quality_limit_constructor_rejects_binary_float_with_validation_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite Decimal"):
            ExecutionQualityLimits(1.0, Decimal("10"))

    def test_environment_cannot_weaken_shib_quality_profile(self) -> None:
        values = {
            "HERMES_TRADING_MAX_SPREAD_BPS": "100",
            "HERMES_TRADING_MODELED_SLIPPAGE_BPS": "1",
            "HERMES_TRADING_SHIB_MAX_SPREAD_BPS": "100",
            "HERMES_TRADING_SHIB_MODELED_SLIPPAGE_BPS": "1",
        }

        shib = resolve_execution_quality_limits(values, "SHIB/USDT")

        self.assertEqual(shib.max_spread_bps, Decimal("30"))
        self.assertEqual(shib.modeled_slippage_bps, Decimal("20"))

    def test_negative_balances_are_not_authorized(self) -> None:
        decision = self.engine.authorize_entry(
            context=self.context(free_quote=Decimal("-1")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "invalid_balance")

    def context(self, **overrides: object) -> RiskContext:
        values: dict[str, object] = {
            "symbol": "BTC/USDT",
            "equity": Decimal("1000"),
            "free_quote": Decimal("100"),
            "symbol_exposure": Decimal("0"),
            "total_exposure": Decimal("0"),
            "daily_pnl": Decimal("0"),
            "drawdown": Decimal("0"),
            "spread_bps": Decimal("10"),
            "spread_available": True,
            "spread_observed_at": self.now,
            "active_position": False,
        }
        values.update(overrides)
        return RiskContext(**values)


if __name__ == "__main__":
    unittest.main()
