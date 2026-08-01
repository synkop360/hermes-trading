import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_trading.loop import run_loop
from hermes_trading.run import ensure_state_files


class LoopFundsAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_loop_sends_low_funds_alert_when_enabled(self):
        messages = []

        async def fake_send(settings, content):
            messages.append(content)
            return True

        async def fake_funds_alert(state_dir, settings, now=None, exchange_factory=None):
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
                    with patch("hermes_trading.loop.build_due_funds_alert", fake_funds_alert):
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
