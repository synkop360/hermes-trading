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

        async def no_trade(asset, strategy):
            return {}, {
                "timestamp": "2026-07-29T09:00:00+00:00",
                "event": "decision",
                "asset": asset,
                "strategy_version": "47",
                "side": "long",
                "entry_indicator": "rsi",
                "entry_value": 50,
                "threshold": 17,
                "trade_closed": False,
                "reason": "entry_condition_not_met",
            }

        async def fake_funds_alert(state_dir, settings, now=None, exchange_factory=None):
            return "[HERMES WARNING] LOW FUNDS"

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
                with patch("hermes_trading.loop._evaluate_asset", no_trade):
                    with patch("hermes_trading.loop.send_notification", fake_send):
                        with patch("hermes_trading.loop.build_due_funds_alert", fake_funds_alert):
                            task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                            for _ in range(20):
                                if messages:
                                    break
                                await asyncio.sleep(0.01)
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task

        self.assertIn("[HERMES WARNING] LOW FUNDS", messages)
