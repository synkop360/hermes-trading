from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.domain import OrderStatus, Position, PositionStatus
from hermes_trading.exchange import ExchangeOrder, KrakenGateway, MarketRules
from hermes_trading.funds import resolve_funds_alert_settings
from hermes_trading.adapters.execution import resolve_execution_settings
from hermes_trading.loop import _prioritize_live_assets, _risk_context, resolve_trading_config, run_live_cycle
from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.risk import RiskEngine
from scripts.check_live_readiness import ReadinessContext, check_readiness
from tests.fakes.kraken import FakeKrakenExchange


NOW = datetime(2026, 7, 30, 20, tzinfo=UTC)


class FakeMarketData:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def collect(self, assets: list[str]) -> dict[str, MarketSnapshot]:
        self.events.append("collect_market")
        self.calls += 1
        return {asset: entry_snapshot(asset) for asset in assets}


class MissingMarketData(FakeMarketData):
    async def collect(self, assets: list[str]) -> dict[str, MarketSnapshot]:
        self.events.append("collect_market")
        self.calls += 1
        return {}


class ValidationGateway:
    def __init__(self, events: list[str], *, unknown_remote: bool = False) -> None:
        self.events = events
        self.real_order_calls = 0
        self.validation_calls = 0
        self.balances = {"USDT": Decimal("100"), "BTC": Decimal("0")}
        self._orders: list[ExchangeOrder] = []
        if unknown_remote:
            self._orders.append(exchange_order("unknown", client_order_id=None))

    async def load_rules(self, asset: str) -> MarketRules:
        self.events.append("load_rules")
        return MarketRules(asset, Decimal("0.00001"), Decimal("5"), 8, 2)

    async def fetch_balances(self) -> dict[str, Decimal]:
        self.events.append("fetch_balances")
        return dict(self.balances)

    async def fetch_orders(self, asset: str | None = None) -> tuple[ExchangeOrder, ...]:
        self.events.append("fetch_orders")
        return tuple(order for order in self._orders if asset is None or order.asset == asset)

    async def create_market_order(
        self, *, asset: str, side: str, amount: Decimal, client_order_id: str
    ) -> ExchangeOrder:
        self.events.append("validate_market")
        self.validation_calls += 1
        return exchange_order(
            f"validation-{self.validation_calls}",
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            amount=amount,
            status=OrderStatus.VALIDATED,
        )

    async def create_stop_order(self, **kwargs: object) -> ExchangeOrder:
        raise AssertionError("validation-only cycles must not create hosted stops")

    async def cancel_order(self, exchange_order_id: str, asset: str) -> ExchangeOrder:
        raise AssertionError("no cancellation expected")


class FilledValidationGateway(ValidationGateway):
    async def create_market_order(
        self, *, asset: str, side: str, amount: Decimal, client_order_id: str
    ) -> ExchangeOrder:
        self.events.append("validate_market")
        self.validation_calls += 1
        order = exchange_order(
            f"filled-{self.validation_calls}", client_order_id=client_order_id,
            asset=asset, side=side, amount=amount, status=OrderStatus.FILLED,
            filled=amount, average_price=Decimal("80"),
        )
        self._orders.append(order)
        self.balances[asset.split("/", 1)[0]] = amount
        return order

    async def create_stop_order(self, **kwargs: object) -> ExchangeOrder:
        self.events.append("create_stop")
        order = ExchangeOrder(
            "stop-1", str(kwargs["client_order_id"]), str(kwargs["asset"]),
            "sell", "stop-loss", OrderStatus.OPEN, Decimal(kwargs["amount"]),
            Decimal("0"), Decimal(kwargs["amount"]), None, Decimal("0"), "USDT",
            NOW.isoformat(), NOW.isoformat(), Decimal(kwargs["stop_price"]),
        )
        self._orders.append(order)
        return order


class AmbiguousValidationGateway(ValidationGateway):
    async def create_market_order(
        self, *, asset: str, side: str, amount: Decimal, client_order_id: str
    ) -> ExchangeOrder:
        self.events.append("validate_market")
        self.validation_calls += 1
        raise TimeoutError("exchange response unavailable")


class FailingPostProtectionGateway(FilledValidationGateway):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.fetch_order_calls = 0

    async def fetch_orders(self, asset: str | None = None) -> tuple[ExchangeOrder, ...]:
        self.fetch_order_calls += 1
        if self.fetch_order_calls == 4:
            raise TimeoutError("post-protection reconciliation unavailable")
        return await super().fetch_orders(asset)

class LiveCycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name)
        self.repository = StateRepository.open(self.state_dir / "trading.db")
        self.repository.migrate()
        self.projector = ArtifactProjector(self.state_dir)

    def tearDown(self) -> None:
        self.repository.close()
        self.temp.cleanup()

    async def test_cycle_reconciles_before_collecting_market_or_entering(self) -> None:
        events: list[str] = []
        gateway = ValidationGateway(events, unknown_remote=True)
        market = FakeMarketData(events)

        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, market,
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.new_orders, ())
        self.assertEqual(result.status, "blocked_reconciliation")
        self.assertEqual(market.calls, 0)
        self.assertEqual(events[:2], ["fetch_orders", "fetch_balances"])

    async def test_validate_only_persists_intent_without_real_order(self) -> None:
        events: list[str] = []
        gateway = ValidationGateway(events)

        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, FakeMarketData(events),
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.status, "validation_only")
        self.assertEqual(result.new_orders[0].status, OrderStatus.VALIDATED)
        self.assertEqual(gateway.real_order_calls, 0)
        self.assertEqual(gateway.validation_calls, 1)
        self.assertLess(events.index("fetch_balances"), events.index("collect_market"))
        self.assertLess(events.index("load_rules"), events.index("validate_market"))
        self.assertTrue(self.repository.risk_decisions()[0].allowed)
        self.assertEqual(len(self.repository.order_intents()), 1)
        self.assertTrue((self.state_dir / "orders.jsonl").exists())

    async def test_multi_asset_cycle_submits_one_intent_before_reconciliation_and_protection(self) -> None:
        events: list[str] = []
        gateway = ValidationGateway(events)

        result = await run_live_cycle(
            ["BTC/USDT", "ETH/USDT"], self.repository, self.projector,
            FakeMarketData(events), entry_strategy(), gateway,
            validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.status, "validation_only")
        self.assertEqual(gateway.validation_calls, 1)
        self.assertEqual(len(self.repository.order_intents()), 1)

    async def test_ambiguous_first_submission_blocks_later_assets(self) -> None:
        events: list[str] = []
        gateway = AmbiguousValidationGateway(events)

        result = await run_live_cycle(
            ["BTC/USDT", "ETH/USDT"], self.repository, self.projector,
            FakeMarketData(events), entry_strategy(), gateway,
            validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.status, "blocked_reconciliation")
        self.assertEqual(gateway.validation_calls, 1)
        self.assertEqual(len(self.repository.order_intents()), 1)

    def test_risk_reducing_exit_precedes_an_earlier_asset_entry(self) -> None:
        self.repository.save_position(
            Position(
                "eth-open", "ETH/USDT", PositionStatus.OPEN, Decimal("0.1"),
                Decimal("100"), Decimal("90"), "test", (NOW - timedelta(hours=2)).isoformat(),
            )
        )

        ordered = _prioritize_live_assets(
            ["BTC/USDT", "ETH/USDT"],
            {"BTC/USDT": entry_snapshot("BTC/USDT"), "ETH/USDT": entry_snapshot("ETH/USDT")},
            entry_strategy(), self.repository,
        )

        self.assertEqual(ordered, ["ETH/USDT", "BTC/USDT"])

    def test_risk_context_uses_durable_daily_pnl_and_drawdown(self) -> None:
        self.repository.save_position(closed_position("win", Decimal("10"), NOW - timedelta(hours=2)))
        self.repository.save_position(closed_position("loss", Decimal("-20"), NOW - timedelta(hours=1)))

        context = _risk_context(
            "BTC/USDT", entry_snapshot("BTC/USDT").price,
            {"USDT": Decimal("90")}, (), NOW, self.repository,
        )

        self.assertEqual(context.daily_pnl, Decimal("-10"))
        self.assertEqual(context.drawdown, Decimal("20") / Decimal("110"))
        decision = RiskEngine(self.repository, now=lambda: NOW).authorize_entry(
            context=context, stop_loss_pct=Decimal("0.01"), position_size_r=Decimal("0.5"),
            exchange_min_cost=Decimal("5"),
        )
        self.assertEqual(decision.reason_code, "daily_loss_limit")
        self.assertTrue(self.repository.risk_state().kill_switch)

    def test_simulated_aliases_remain_in_training_mode(self) -> None:
        for alias in ("paper", "training", "train", "demo"):
            self.assertEqual(resolve_trading_config({"HERMES_TRADING_MODE": alias}).mode, "training")
            settings = resolve_execution_settings({"HERMES_TRADING_MODE": alias})
            self.assertEqual(settings.mode, "training")
            self.assertEqual(settings.executor, "simulated")

    async def test_unknown_quote_balance_records_no_order_and_human_reason(self) -> None:
        events: list[str] = []
        gateway = ValidationGateway(events)

        async def missing_quote() -> dict[str, Decimal]:
            events.append("fetch_balances")
            return {"BTC": Decimal("0")}

        gateway.fetch_balances = missing_quote  # type: ignore[method-assign]
        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, FakeMarketData(events),
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.new_orders, ())
        self.assertEqual(result.status, "blocked_risk")
        self.assertIn("unknown_balance", result.heartbeat["reason"])

    async def test_funds_alert_uses_reconciled_balance_and_transport_failure_is_nonfatal(self) -> None:
        events: list[str] = []
        gateway = ValidationGateway(events)
        settings = resolve_funds_alert_settings(
            {"HERMES_FUNDS_ALERT_ENABLED": "true", "HERMES_FUNDS_MIN_AVAILABLE_USDT": "200"}
        )

        async def failed_notification(message: str) -> None:
            events.append("notify_funds")
            raise OSError("discord unavailable")

        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, FakeMarketData(events),
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
            funds_alert_settings=settings, notify=failed_notification,
        )

        self.assertEqual(result.status, "validation_only")
        self.assertIn("notify_funds", events)
        self.assertEqual(events.count("fetch_balances"), 2)  # pre and post reconciliation only
        self.assertTrue(
            any("funds_alert_transport_failed" in warning for warning in result.heartbeat["warnings"])
        )

    async def test_non_validation_cycle_is_rejected_before_gateway_access(self) -> None:
        events: list[str] = []

        with self.assertRaisesRegex(RuntimeError, "validation-only"):
            await run_live_cycle(
                ["BTC/USDT"], self.repository, self.projector, FakeMarketData(events),
                entry_strategy(), ValidationGateway(events), validate_only=False,
            )

        self.assertEqual(events, [])

    async def test_filled_entry_is_reconciled_before_its_protective_stop(self) -> None:
        events: list[str] = []
        gateway = FilledValidationGateway(events)

        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, FakeMarketData(events),
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.status, "validation_only")
        self.assertIn("create_stop", events)
        submission_index = events.index("validate_market")
        stop_index = events.index("create_stop")
        self.assertIn("fetch_orders", events[submission_index + 1 : stop_index])
        self.assertFalse(self.repository.risk_state().kill_switch)

    async def test_kraken_validation_gateway_never_sends_a_real_order(self) -> None:
        fake = FakeKrakenExchange()
        gateway = KrakenGateway(lambda: fake, validate_only=True)

        order = await gateway.create_market_order(
            asset="BTC/USDT", side="buy", amount=Decimal("0.0001"),
            client_order_id="ht-validation-test",
        )

        self.assertEqual(order.status, OrderStatus.VALIDATED)
        self.assertTrue(fake.create_order_requests[0]["params"]["validate"])
        await gateway.close()

    async def test_repeated_validation_cycle_does_not_resubmit_same_candle(self) -> None:
        events: list[str] = []
        gateway = ValidationGateway(events)
        market = FakeMarketData(events)

        first = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, market,
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
        )
        second = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, market,
            entry_strategy(), gateway, validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(len(first.new_orders), 1)
        self.assertEqual(second.new_orders, ())
        self.assertEqual(gateway.validation_calls, 1)

    async def test_missing_market_snapshot_is_a_visible_block_not_no_trade(self) -> None:
        events: list[str] = []

        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, MissingMarketData(events),
            entry_strategy(), ValidationGateway(events), validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.status, "blocked_market")
        self.assertIn("market_snapshot_missing", result.heartbeat["reason"])

    async def test_post_protection_reconciliation_failure_is_fail_closed(self) -> None:
        events: list[str] = []

        result = await run_live_cycle(
            ["BTC/USDT"], self.repository, self.projector, FakeMarketData(events),
            entry_strategy(), FailingPostProtectionGateway(events),
            validate_only=True, now=lambda: NOW,
        )

        self.assertEqual(result.status, "blocked_reconciliation")
        self.assertIn("post_protection", result.heartbeat["reason"])


class ReadinessTests(unittest.TestCase):
    def test_readiness_reports_all_required_safety_failures(self) -> None:
        report = check_readiness(
            ReadinessContext(
                mode="live",
                validate_only=False,
                allow_real_orders=True,
                api_permissions=None,
                sqlite_integrity="corrupt",
                reconciliation_fresh=False,
                kill_switch=True,
                risk_configured=False,
                market_supported=False,
                market_minimum=Decimal("10"),
                max_order_quote=Decimal("5"),
                qualification_approved=False,
            )
        )

        self.assertFalse(report.ready)
        text = "\n".join(report.failures)
        for expected in (
            "API permissions", "SQLite integrity", "reconciliation",
            "kill switch", "risk configuration", "unsupported market",
            "exchange minimum", "real orders remain blocked",
        ):
            self.assertIn(expected, text)

    def test_readiness_rejects_withdrawal_permission(self) -> None:
        report = check_readiness(
            ReadinessContext(
                mode="live", validate_only=True, allow_real_orders=False,
                api_permissions=frozenset({"query-funds", "modify-trades", "withdraw-funds"}),
                sqlite_integrity="ok", reconciliation_fresh=True, kill_switch=False,
                risk_configured=True, market_supported=True,
                market_minimum=Decimal("5"), max_order_quote=Decimal("5"),
            )
        )

        self.assertFalse(report.ready)
        self.assertIn("withdrawal", " ".join(report.failures))

    def test_validation_only_is_ready_when_every_gate_is_green(self) -> None:
        report = check_readiness(
            ReadinessContext(
                mode="live", validate_only=True, allow_real_orders=False,
                api_permissions=frozenset({"query-funds", "modify-trades"}),
                sqlite_integrity="ok", reconciliation_fresh=True, kill_switch=False,
                risk_configured=True, market_supported=True,
                market_minimum=Decimal("5"), max_order_quote=Decimal("5"),
            )
        )

        self.assertTrue(report.ready, report.failures)


def entry_strategy() -> dict[str, object]:
    return {
        "version": "live-cycle-test",
        "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"},
        "stop_loss_pct": "0.5",
        "position_size_r": "0.5",
    }


def entry_snapshot(asset: str) -> MarketSnapshot:
    return MarketSnapshot(
        asset=asset,
        timeframe="1h",
        availability="available",
        decision_action="evaluate",
        reason="closed_candle_available",
        candle=ClosedCandle(
            NOW - timedelta(hours=1), NOW,
            Decimal("100"), Decimal("100"), Decimal("79"), Decimal("80"), Decimal("1"),
        ),
        price={
            "last": Decimal("80"), "bid": Decimal("79.9"), "ask": Decimal("80.1"),
            "closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),),
        },
    )


def exchange_order(
    order_id: str,
    *,
    client_order_id: str | None,
    asset: str = "BTC/USDT",
    side: str = "buy",
    amount: Decimal = Decimal("0.0001"),
    status: object = OrderStatus.OPEN,
    filled: Decimal = Decimal("0"),
    average_price: Decimal | None = None,
) -> ExchangeOrder:
    return ExchangeOrder(
        order_id, client_order_id, asset, side, "market", status, amount,
        filled, amount - filled, average_price, Decimal("0"), "USDT",
        NOW.isoformat(), NOW.isoformat(),
    )


def closed_position(position_id: str, pnl: Decimal, closed_at: datetime) -> Position:
    return Position(
        position_id, "BTC/USDT", PositionStatus.CLOSED, Decimal("0.1"),
        Decimal("100"), Decimal("90"), "test", (closed_at - timedelta(hours=1)).isoformat(),
        closed_at.isoformat(), pnl, Decimal("0"),
    )


if __name__ == "__main__":
    unittest.main()
