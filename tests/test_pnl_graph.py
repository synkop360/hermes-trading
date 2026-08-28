from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.domain import Position, PositionStatus
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.pnl_graph import build_pnl_graph_series, render_pnl_graph_png


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def closed_position(
    position_id: str,
    asset: str,
    realized_pnl: Decimal,
    closed_at: datetime,
) -> Position:
    return Position(
        position_id=position_id,
        asset=asset,
        status=PositionStatus.CLOSED,
        quantity=Decimal("0.01"),
        entry_price=Decimal("1000"),
        stop_price=Decimal("950"),
        strategy_version="83",
        opened_at=(closed_at - timedelta(hours=1)).isoformat(),
        closed_at=closed_at.isoformat(),
        realized_pnl=realized_pnl,
        fees=Decimal("0.01"),
    )


class PnlGraphTests(unittest.TestCase):
    def test_series_uses_training_window_and_accumulates_wallet_total_and_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            (state_dir / "qualification-training-started-at.txt").write_text(
                "2026-08-01T00:00:00+00:00\n", encoding="utf-8"
            )
            repository.save_position(
                closed_position("old", "BTC/USDT", Decimal("100"), datetime(2026, 7, 31, 23, 0, tzinfo=UTC))
            )
            repository.save_position(
                closed_position("btc-win", "BTC/USDT", Decimal("5"), datetime(2026, 8, 2, 10, 0, tzinfo=UTC))
            )
            repository.save_position(
                closed_position("eth-loss", "ETH/USDT", Decimal("-2"), datetime(2026, 8, 3, 10, 0, tzinfo=UTC))
            )
            repository.save_position(
                closed_position("future", "BTC/USDT", Decimal("999"), datetime(2026, 8, 12, 10, 0, tzinfo=UTC))
            )

            series = build_pnl_graph_series(
                repository,
                state_dir,
                now=NOW,
                env={"HERMES_QUALIFICATION_STARTING_WALLET_BALANCE": "75"},
            )
            repository.close()

        self.assertEqual(series.starting_wallet, Decimal("75"))
        self.assertEqual(series.symbols, ("BTC/USDT", "ETH/USDT"))
        self.assertEqual([point.asset for point in series.points], ["BTC/USDT", "ETH/USDT"])
        self.assertEqual([point.cumulative_pnl for point in series.points], [Decimal("5"), Decimal("3")])
        self.assertEqual([point.wallet for point in series.points], [Decimal("80"), Decimal("78")])
        self.assertEqual(series.symbol_pnl["BTC/USDT"], [Decimal("5"), Decimal("5")])
        self.assertEqual(series.symbol_pnl["ETH/USDT"], [Decimal("0"), Decimal("-2")])

    def test_png_renderer_returns_png_bytes_for_empty_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            series = build_pnl_graph_series(repository, state_dir, now=NOW)
            repository.close()

        png = render_pnl_graph_png(series)

        self.assertGreater(len(png), 100)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
