from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.loop import run_cycle
from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector


class StaticMarketData:
    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def collect(self, assets: list[str]) -> dict[str, MarketSnapshot]:
        self.calls += 1
        return {asset: self.snapshot for asset in assets}


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
