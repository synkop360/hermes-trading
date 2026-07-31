from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.market_data import ClosedCandle, MarketSnapshot
from hermes_trading.persistence import StateRepository
from hermes_trading.positions import PositionEngine


def candle(close_at: datetime, *, open: str, low: str, close: str) -> MarketSnapshot:
    return MarketSnapshot(
        asset="BTC/USDT",
        timeframe="1h",
        availability="available",
        decision_action="evaluate",
        reason="closed_candle_available",
        candle=ClosedCandle(
            open_timestamp=close_at - timedelta(hours=1),
            close_timestamp=close_at,
            open=Decimal(open),
            high=max(Decimal(open), Decimal(close)),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1"),
        ),
        price={"closes": tuple(Decimal("100") for _ in range(15)) + (Decimal("80"),)},
    )


def entry_strategy() -> dict[str, object]:
    return {
        "version": "test",
        "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"},
        "stop_loss_pct": "0.5",
    }


class PositionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state = StateRepository.open(Path(self.temporary_directory.name) / "trading.db")
        self.state.migrate()
        self.engine = PositionEngine(self.state)
        self.entry_candle = candle(
            datetime(2026, 7, 30, 20, tzinfo=UTC), open="100", low="79", close="80"
        )

    def tearDown(self) -> None:
        self.state.close()
        self.temporary_directory.cleanup()

    def test_entry_does_not_close_on_same_candle(self) -> None:
        result = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertTrue(result.processed)
        self.assertEqual(result.position.status.value, "open")
        self.assertEqual(result.closed_positions, ())

    def test_stop_exit_uses_low_and_charges_fees(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="70", close="90"),
            entry_strategy(),
        )

        closed = result.closed_positions[0]
        self.assertEqual(closed.status.value, "closed")
        self.assertLess(closed.realized_pnl, Decimal("0"))
        self.assertGreater(closed.fees, Decimal("0"))

    def test_same_candle_is_not_processed_twice(self) -> None:
        first = self.engine.process_candle(self.entry_candle, entry_strategy())
        second = self.engine.process_candle(self.entry_candle, entry_strategy())

        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertEqual(second.reason, "duplicate_candle")

    def test_open_position_survives_restart(self) -> None:
        self.engine.process_candle(self.entry_candle, entry_strategy())
        database_path = Path(self.temporary_directory.name) / "trading.db"
        self.state.close()
        self.state = StateRepository.open(database_path)
        self.engine = PositionEngine(self.state)

        result = self.engine.process_candle(
            candle(datetime(2026, 7, 30, 21, tzinfo=UTC), open="100", low="90", close="95"),
            entry_strategy(),
        )

        self.assertTrue(result.processed)
        self.assertEqual(result.position.status.value, "open")
