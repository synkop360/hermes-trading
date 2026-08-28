import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("position_viewer", ROOT / "scripts" / "position_viewer.py")
position_viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(position_viewer)


def _write_goal(state_dir: Path, assets: list[str]) -> None:
    (state_dir / "goal.yaml").write_text(
        "assets:\n" + "\n".join(f'  - "{asset}"' for asset in assets) + "\n",
        encoding="utf-8",
    )


def _write_strategy(state_dir: Path) -> None:
    (state_dir / "strategy.yaml").write_text(
        "entry:\n  indicator: rsi\n  threshold: 30\nexit:\n  indicator: rsi\n  threshold: 55\n",
        encoding="utf-8",
    )


class ConfiguredSymbolsTests(unittest.TestCase):
    def test_reads_assets_list_from_goal_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            _write_goal(state_dir, ["BTC/USDT", "ETH/USDT"])

            self.assertEqual(position_viewer.configured_symbols(state_dir), ["BTC/USDT", "ETH/USDT"])

    def test_returns_empty_list_when_goal_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(position_viewer.configured_symbols(Path(temp_dir)), [])


class ClosedTradeExitPriceTests(unittest.TestCase):
    def test_derives_exit_price_from_entry_price_pnl_and_fees(self) -> None:
        trade = {
            "entry_price": "100",
            "quantity": "1",
            "realized_pnl": "5",
            "fees": "0.5",
        }

        self.assertEqual(position_viewer._closed_trade_exit_price(trade), 105.5)

    def test_returns_none_when_a_required_field_is_missing(self) -> None:
        self.assertIsNone(position_viewer._closed_trade_exit_price({"entry_price": "100"}))


class BuildSymbolPayloadTests(unittest.TestCase):
    def _fake_candles(self, count: int) -> tuple:
        Candle = position_viewer.Candle
        return tuple(
            Candle(
                timestamp=datetime(2026, 8, 1, tzinfo=UTC).replace(hour=hour % 24),
                open=Decimal("80"),
                high=Decimal(str(82 + hour)),
                low=Decimal("78"),
                close=Decimal(str(80 + hour)),
            )
            for hour in range(count)
        )

    def test_payload_includes_candles_indicators_trades_and_open_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            _write_strategy(state_dir)
            now = datetime.now(UTC)
            (state_dir / "trades.jsonl").write_text(
                json.dumps(
                    {
                        "asset": "BTC/USDT",
                        "opened_at": now.isoformat(),
                        "closed_at": now.isoformat(),
                        "entry_price": "80",
                        "quantity": "1",
                        "realized_pnl": "2",
                        "fees": "0.1",
                        "stop_price": "79",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (state_dir / "positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "position_id": "pos-1",
                                "asset": "BTC/USDT",
                                "status": "open",
                                "quantity": "1",
                                "entry_price": "90",
                                "stop_price": "89",
                                "take_profit_price": "95",
                                "opened_at": "2026-08-01T10:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with unittest.mock.patch.object(
                position_viewer, "fetch_candles", AsyncMock(return_value=self._fake_candles(40))
            ):
                payload = position_viewer.build_symbol_payload(state_dir, "kraken", "BTC/USDT", "1h", 24)

            self.assertEqual(payload["symbol"], "BTC/USDT")
            self.assertEqual(len(payload["candles"]), 40)
            self.assertEqual(len(payload["rsi"]), 40)
            self.assertTrue(all(v is None for v in payload["rsi"][:14]))
            self.assertEqual(payload["entry"], {"indicator": "rsi", "threshold": 30.0})
            self.assertEqual(payload["exit"], {"indicator": "rsi", "threshold": 55.0})
            self.assertEqual(len(payload["trades"]), 1)
            self.assertAlmostEqual(payload["trades"][0]["exit_price"], 82.1)
            self.assertIsNotNone(payload["open_position"])
            self.assertEqual(payload["open_position"]["take_profit_price"], 95.0)


if __name__ == "__main__":
    unittest.main()
