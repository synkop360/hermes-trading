import asyncio
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hermes_trading.loop import run_loop
from hermes_trading.run import ensure_state_files


class LoopFundsAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_loop_uses_configured_market_timeframe(self):
        initialized = []

        def fake_init(self, **kwargs):
            initialized.append(kwargs)

        async def blocked_market_collection(_, assets):
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            ensure_state_files(state)
            with patch.dict(
                os.environ,
                {
                    "HERMES_TRADING_MODE": "training",
                    "HERMES_TRADING_TIMEFRAME": "15m",
                },
                clear=False,
            ):
                with patch("hermes_trading.loop.MarketDataCoordinator.__init__", fake_init):
                    with patch("hermes_trading.loop.MarketDataCoordinator.collect", blocked_market_collection):
                        task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                        for _ in range(20):
                            if initialized:
                                break
                            await asyncio.sleep(0.01)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

        self.assertEqual(initialized[0]["timeframe"], "15m")

    async def test_run_loop_uses_persisted_operator_market_timeframe(self):
        initialized = []

        def fake_init(self, **kwargs):
            initialized.append(kwargs)

        async def blocked_market_collection(_, assets):
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            ensure_state_files(state)
            (state / "operator_config.json").write_text(
                '{"HERMES_TRADING_TIMEFRAME":"15m"}\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HERMES_TRADING_MODE": "training"}, clear=False):
                with patch("hermes_trading.loop.MarketDataCoordinator.__init__", fake_init):
                    with patch("hermes_trading.loop.MarketDataCoordinator.collect", blocked_market_collection):
                        task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                        for _ in range(20):
                            if initialized:
                                break
                            await asyncio.sleep(0.01)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

        self.assertEqual(initialized[0]["timeframe"], "15m")

    async def test_run_loop_sends_low_funds_alert_when_enabled(self):
        messages = []

        async def fake_send(settings, content):
            messages.append(content)
            return True

        def fake_funds_alert(state_dir, settings, balance, now=None):
            return "[HERMES WARNING] LOW FUNDS"

        async def blocked_market_collection(_, assets):
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            ensure_state_files(state)
            with patch.dict(
                os.environ,
                {
                    "HERMES_TRADING_MODE": "training",
                    "HERMES_NOTIFY_ENABLED": "true",
                    "HERMES_NOTIFY_SERVICE": "discord",
                    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token",
                    "HERMES_FUNDS_ALERT_ENABLED": "true",
                },
                clear=False,
            ):
                with patch("hermes_trading.loop.send_notification", fake_send):
                    with patch("hermes_trading.loop.build_funds_alert_from_wallet_balance", fake_funds_alert):
                        with patch("hermes_trading.loop.MarketDataCoordinator.collect", blocked_market_collection):
                            task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                            for _ in range(20):
                                if messages:
                                    break
                                await asyncio.sleep(0.01)
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task

        self.assertIn("[HERMES WARNING] LOW FUNDS", messages)

    async def test_run_loop_funds_alert_uses_virtual_wallet_not_real_exchange(self):
        calls = []

        def fake_wallet_balance(repository, env):
            calls.append("wallet")
            return Decimal("50")

        async def fake_exchange_fetch(*args, **kwargs):
            calls.append("real_exchange")
            raise AssertionError("training mode must not fetch the real exchange balance")

        async def blocked_market_collection(_, assets):
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            ensure_state_files(state)
            with patch.dict(
                os.environ,
                {
                    "HERMES_TRADING_MODE": "training",
                    "HERMES_FUNDS_ALERT_ENABLED": "true",
                },
                clear=False,
            ):
                with patch("hermes_trading.loop.compute_virtual_wallet_balance", fake_wallet_balance):
                    with patch("hermes_trading.funds.fetch_available_funds", fake_exchange_fetch):
                        with patch("hermes_trading.loop.MarketDataCoordinator.collect", blocked_market_collection):
                            task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                            for _ in range(20):
                                if calls:
                                    break
                                await asyncio.sleep(0.01)
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task

        self.assertIn("wallet", calls)
        self.assertNotIn("real_exchange", calls)
