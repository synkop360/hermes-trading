from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.loop import run_cycle
from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.positions import PositionEngine


class StaticMarketData:
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def collect(self, assets: list[str]) -> dict[str, MarketSnapshot]:
        self.calls += 1
        return {asset: self.snapshot for asset in assets}


class FailingProjector(ArtifactProjector):
    def project_pending_item(self, repository: StateRepository, item: object) -> None:
        raise OSError("audit storage unavailable")


class CountingProjector(ArtifactProjector):
    def __init__(self, state_dir: Path) -> None:
        super().__init__(state_dir)
        self.projected_items = 0
        self.reconcile_calls = 0

    def project_pending_item(self, repository: StateRepository, item: object) -> None:
        self.projected_items += 1
        super().project_pending_item(repository, item)

    def reconcile(self, repository: StateRepository) -> None:
        self.reconcile_calls += 1
        raise AssertionError("bounded outbox recovery must not perform a global reconciliation")


class TrainingLoopTests(unittest.TestCase):
    def test_run_cycle_is_public(self) -> None:
        self.assertTrue(callable(run_cycle))

    def test_two_cycles_on_the_same_candle_create_one_order(self) -> None:
        close_at = datetime(2026, 7, 30, 20, tzinfo=UTC)
        snapshot = MarketSnapshot(
            asset="BTC/USDT", timeframe="1h", availability="available",
            decision_action="evaluate", reason="closed_candle_available",
            candle=ClosedCandle(close_at - timedelta(hours=1), close_at, Decimal("100"), Decimal("100"), Decimal("79"), Decimal("80"), Decimal("1")),
            price={"closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),)},
        )
        strategy = {"version": "test", "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"}, "stop_loss_pct": "0.5"}
        with tempfile.TemporaryDirectory() as directory:
            state = StateRepository.open(Path(directory) / "trading.db")
            state.migrate()
            market_data = StaticMarketData(snapshot)
            first = __import__("asyncio").run(run_cycle(["BTC/USDT"], state, ArtifactProjector(directory), market_data, strategy, "training"))
            second = __import__("asyncio").run(run_cycle(["BTC/USDT"], state, ArtifactProjector(directory), market_data, strategy, "training"))
            state.close()

        self.assertEqual(len(first.orders), 1)
        self.assertEqual(len(second.orders), 0)
        self.assertEqual(second.results[0].reason, "duplicate_candle")
        self.assertEqual(market_data.calls, 2)

    def test_cycle_reports_pending_projection_when_market_has_no_candle(self) -> None:
        close_at = datetime(2026, 7, 30, 20, tzinfo=UTC)
        available = MarketSnapshot(
            asset="BTC/USDT", timeframe="1h", availability="available",
            decision_action="evaluate", reason="closed_candle_available",
            candle=ClosedCandle(close_at - timedelta(hours=1), close_at, Decimal("100"), Decimal("100"), Decimal("79"), Decimal("80"), Decimal("1")),
            price={"closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),)},
        )
        unavailable = MarketSnapshot(**{
            **available.__dict__, "availability": "unavailable", "decision_action": "no_trade",
            "reason": "price_unavailable", "candle": None,
        })
        strategy = {"version": "test", "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"}, "stop_loss_pct": "0.5"}
        with tempfile.TemporaryDirectory() as directory:
            state = StateRepository.open(Path(directory) / "trading.db")
            state.migrate()
            PositionEngine(state).process_candle(available, strategy)
            cycle = __import__("asyncio").run(run_cycle(
                ["BTC/USDT"], state, FailingProjector(directory), StaticMarketData(unavailable), strategy, "training"
            ))
            state.close()

        self.assertTrue(cycle.degraded)
        self.assertEqual(cycle.results[0].projection_error, "audit storage unavailable")

    def test_bounded_recovery_stays_degraded_until_the_outbox_is_empty(self) -> None:
        start = datetime(2026, 7, 30, 20, tzinfo=UTC)
        strategy = {"version": "test", "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"}, "stop_loss_pct": "0.5"}
        with tempfile.TemporaryDirectory() as directory:
            state = StateRepository.open(Path(directory) / "trading.db")
            try:
                state.migrate()
                for offset in range(9):
                    close_at = start + timedelta(hours=offset)
                    pending_snapshot = MarketSnapshot(
                        asset="BTC/USDT", timeframe="1h", availability="stale",
                        decision_action="no_trade", reason="stale_closed_candle",
                        candle=ClosedCandle(close_at - timedelta(hours=1), close_at, Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("1")),
                        price={"closes": (Decimal("100"),)},
                    )
                    PositionEngine(state).process_candle(pending_snapshot, strategy)
                self.assertEqual(len(state.pending_projection_items(limit=20)), 9)

                unavailable = MarketSnapshot(
                    asset="BTC/USDT", timeframe="1h", availability="unavailable",
                    decision_action="no_trade", reason="price_unavailable", candle=None, price={},
                )
                projector = CountingProjector(Path(directory))
                first = __import__("asyncio").run(run_cycle(
                    ["BTC/USDT"], state, projector, StaticMarketData(unavailable), strategy, "training"
                ))
                first_pending_count = len(state.pending_projection_items(limit=20))
                second = __import__("asyncio").run(run_cycle(
                    ["BTC/USDT"], state, projector, StaticMarketData(unavailable), strategy, "training"
                ))
            finally:
                state.close()

        self.assertTrue(first.degraded)
        self.assertEqual(first.results[0].projection_error, "projection backlog remains after bounded recovery")
        self.assertEqual(first_pending_count, 1)
        self.assertEqual(projector.projected_items, 9)
        self.assertEqual(projector.reconcile_calls, 0)
        self.assertFalse(second.degraded)
