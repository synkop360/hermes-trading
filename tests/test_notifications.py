import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from hermes_trading.notifications import (
    NotificationConfigError,
    NotificationSettings,
    PnlSummaryCounter,
    build_cycle_notifications,
    build_due_periodic_summaries,
    build_summary_notifications,
    resolve_notification_settings,
    send_notification,
)
from hermes_trading.loop import run_loop
from hermes_trading.run import ensure_state_files


class NotificationSettingsTests(unittest.TestCase):
    def test_notifications_are_disabled_by_default(self):
        settings = resolve_notification_settings({})

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.service, "discord")

    def test_discord_requires_webhook_when_enabled(self):
        with self.assertRaises(NotificationConfigError):
            resolve_notification_settings({"HERMES_NOTIFY_ENABLED": "true"})

    def test_notification_mode_aliases_are_normalized(self):
        settings = resolve_notification_settings({"HERMES_NOTIFY_MODE": "gain_loss_only"})

        self.assertEqual(settings.mode, "pnl_only")

    def test_summary_frequency_list_is_normalized(self):
        settings = resolve_notification_settings({"HERMES_NOTIFY_SUMMARY_FREQUENCIES": "[ 12h, 1w, monthly ]"})

        self.assertEqual(settings.summary_frequencies, ("12h", "1w", "1mo"))

    def test_hourly_summary_alias_is_normalized(self):
        settings = resolve_notification_settings({"HERMES_NOTIFY_SUMMARY_FREQUENCIES": "hourly"})

        self.assertEqual(settings.summary_frequencies, ("1h",))

    def test_summary_frequency_rejects_ambiguous_month_shortcut(self):
        with self.assertRaises(NotificationConfigError):
            resolve_notification_settings({"HERMES_NOTIFY_SUMMARY_FREQUENCIES": "1m"})


class NotificationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_notification_posts_content_to_webhook(self):
        calls = []

        async def post_json(url, payload):
            calls.append((url, payload))

        settings = resolve_notification_settings(
            {
                "HERMES_NOTIFY_ENABLED": "true",
                "HERMES_NOTIFY_SERVICE": "discord",
                "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token",
            }
        )

        delivered = await send_notification(settings, "Hermes test", post_json=post_json)

        self.assertTrue(delivered)
        self.assertEqual(calls[0][0], "https://discord.com/api/webhooks/test/token")
        self.assertEqual(calls[0][1]["content"], "Hermes test")
        self.assertEqual(calls[0][1]["username"], "Hermes Trading")

    async def test_disabled_notifications_do_not_call_webhook(self):
        calls = []

        async def post_json(url, payload):
            calls.append((url, payload))

        settings = resolve_notification_settings({})

        delivered = await send_notification(settings, "Hermes test", post_json=post_json)

        self.assertFalse(delivered)
        self.assertEqual(calls, [])


class CycleNotificationTests(unittest.TestCase):
    def test_cycle_notifications_include_orders_and_trades(self):
        messages = build_cycle_notifications(
            trades=[
                {
                    "asset": "BTC/USDT",
                    "realized_pnl_pct": 0.0123,
                    "strategy_version": "46",
                    "mode": "training",
                }
            ],
            orders=[
                {
                    "asset": "BTC/USDT",
                    "side": "buy",
                    "status": "validated",
                    "amount": "0.05000000",
                    "validate_only": True,
                }
            ],
        )

        self.assertEqual(len(messages), 2)
        self.assertIn("P&L GAIN", messages[0])
        self.assertIn("BTC/USDT", messages[0])
        self.assertIn("Order validated", messages[1])
        self.assertIn("validate_only=True", messages[1])

    def test_simulated_order_message_uses_virtual_order_fields(self):
        messages = build_cycle_notifications(
            trades=[],
            orders=[
                {
                    "asset": "XRP/USDT",
                    "side": "buy",
                    "status": "simulated",
                    "reference_price": 1.06009,
                    "position_size_r": 2.0,
                }
            ],
        )

        self.assertEqual(len(messages), 1)
        self.assertIn("Virtual order simulated buy XRP/USDT", messages[0])
        self.assertIn("reference_price=1.06009", messages[0])
        self.assertIn("position_size_r=2.0", messages[0])
        self.assertNotIn("None", messages[0])

    def test_orders_only_mode_filters_trade_messages(self):
        settings = NotificationSettings(
            enabled=True,
            service="discord",
            webhook_url="https://discord.com/api/webhooks/test/token",
            username="Hermes Trading",
            mode="orders_only",
            notify_trades=True,
            notify_orders=True,
            notify_errors=True,
            notify_heartbeat=False,
            summary_every_cycles=0,
        )

        messages = build_cycle_notifications(
            trades=[{"asset": "BTC/USDT", "realized_pnl_pct": 0.01, "strategy_version": "47", "mode": "training"}],
            orders=[{"asset": "BTC/USDT", "side": "buy", "status": "simulated", "reference_price": 100.0}],
            settings=settings,
        )

        self.assertEqual(len(messages), 1)
        self.assertIn("Virtual order", messages[0])

    def test_pnl_only_mode_filters_flat_trades_and_orders(self):
        settings = NotificationSettings(
            enabled=True,
            service="discord",
            webhook_url="https://discord.com/api/webhooks/test/token",
            username="Hermes Trading",
            mode="pnl_only",
            notify_trades=True,
            notify_orders=True,
            notify_errors=True,
            notify_heartbeat=False,
            summary_every_cycles=0,
        )

        messages = build_cycle_notifications(
            trades=[
                {"asset": "XRP/USDT", "realized_pnl_pct": 0.0, "strategy_version": "47", "mode": "training"},
                {"asset": "SHIB/USDT", "realized_pnl_pct": -0.001, "strategy_version": "47", "mode": "training"},
            ],
            orders=[{"asset": "SHIB/USDT", "side": "buy", "status": "simulated", "reference_price": 0.000004}],
            settings=settings,
        )

        self.assertEqual(len(messages), 1)
        self.assertIn("SHIB/USDT", messages[0])
        self.assertIn("P&L LOSS", messages[0])
        self.assertIn("raw=-0.001", messages[0])

    def test_pnl_trade_message_uses_gain_loss_formalism(self):
        settings = NotificationSettings(
            enabled=True,
            service="discord",
            webhook_url="https://discord.com/api/webhooks/test/token",
            username="Hermes Trading",
            mode="pnl_only",
            notify_trades=True,
            notify_orders=True,
            notify_errors=True,
            notify_heartbeat=False,
            summary_every_cycles=0,
        )

        messages = build_cycle_notifications(
            trades=[
                {
                    "asset": "XRP/USDT",
                    "realized_pnl_pct": -0.000688,
                    "strategy_version": "47",
                    "mode": "training",
                    "entry_value": 12.8656,
                    "threshold": 17,
                }
            ],
            orders=[],
            settings=settings,
        )

        self.assertEqual(
            messages,
            [
                "Hermes Trading | P&L LOSS XRP/USDT -0.0688% "
                "(raw=-0.000688) strategy=47 mode=training rsi=12.8656 threshold=17"
            ],
        )


class PnlSummaryCounterTests(unittest.TestCase):
    def test_counter_publishes_summary_after_configured_cycles_and_resets(self):
        settings = NotificationSettings(
            enabled=True,
            service="discord",
            webhook_url="https://discord.com/api/webhooks/test/token",
            username="Hermes Trading",
            mode="pnl_only",
            notify_trades=True,
            notify_orders=True,
            notify_errors=True,
            notify_heartbeat=False,
            summary_every_cycles=2,
        )
        counter = PnlSummaryCounter(settings)

        self.assertIsNone(
            counter.record_cycle(
                [
                    {"asset": "BTC/USDT", "realized_pnl_pct": 0.01},
                    {"asset": "ETH/USDT", "realized_pnl_pct": 0.0},
                ]
            )
        )

        summary = counter.record_cycle(
            [
                {"asset": "BTC/USDT", "realized_pnl_pct": -0.004},
                {"asset": "SHIB/USDT", "realized_pnl_pct": 0.002},
            ]
        )

        self.assertIsNotNone(summary)
        self.assertIn("P&L summary 2 cycles", summary)
        self.assertIn("trades=4", summary)
        self.assertIn("wins=2", summary)
        self.assertIn("losses=1", summary)
        self.assertIn("flat=1", summary)
        self.assertIn("total_pnl=0.008", summary)
        self.assertIn("BTC/USDT: trades=2 wins=1 losses=1 flat=0 pnl=0.006", summary)
        self.assertEqual(counter.cycles, 0)

    def test_counter_is_disabled_when_period_is_zero(self):
        settings = NotificationSettings(
            enabled=True,
            service="discord",
            webhook_url="https://discord.com/api/webhooks/test/token",
            username="Hermes Trading",
            mode="verbose",
            notify_trades=True,
            notify_orders=True,
            notify_errors=True,
            notify_heartbeat=False,
            summary_every_cycles=0,
        )

        self.assertIsNone(PnlSummaryCounter(settings).record_cycle([{"asset": "BTC/USDT", "realized_pnl_pct": 0.01}]))


class PeriodicSummaryTests(unittest.TestCase):
    def test_periodic_summary_includes_trades_orders_and_flat_pnl(self):
        settings = resolve_notification_settings(
            {
                "HERMES_NOTIFY_SUMMARY_FREQUENCIES": "12h",
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "decisions.jsonl").write_text(
                '{"timestamp":"2026-07-28T01:00:00+00:00","asset":"XRP/USDT","trade_closed":true}\n'
                '{"timestamp":"2026-07-28T02:00:00+00:00","asset":"SHIB/USDT","trade_closed":false}\n',
                encoding="utf-8",
            )
            (state / "trades.jsonl").write_text(
                '{"timestamp":"2026-07-28T01:05:00+00:00","asset":"XRP/USDT","realized_pnl_pct":0.0}\n'
                '{"timestamp":"2026-07-28T03:05:00+00:00","asset":"SHIB/USDT","realized_pnl_pct":-0.002}\n',
                encoding="utf-8",
            )
            (state / "orders.jsonl").write_text(
                '{"timestamp":"2026-07-28T01:06:00+00:00","asset":"XRP/USDT","status":"simulated","side":"buy"}\n',
                encoding="utf-8",
            )

            summaries = build_due_periodic_summaries(
                state,
                settings,
                now=datetime.fromisoformat("2026-07-28T12:01:00+00:00"),
            )

        self.assertEqual(len(summaries), 1)
        self.assertEqual(
            summaries[0],
            "\n".join(
                [
                    "Hermes Trading | Periodic summary 12h",
                    "Window: 2026-07-28T00:00:00+00:00 -> 2026-07-28T12:00:00+00:00",
                    "----------------------------------------",
                    "Decisions: total=2 no_trade=1",
                    "Trades: total=2 wins=0 losses=1 flat=1 total_pnl=-0.002",
                    "Orders: total=1 by_status=simulated:1",
                    "----------------------------------------",
                    "By symbol:",
                    "SHIB/USDT | trades=1 wins=0 losses=1 flat=0 pnl=-0.002",
                    "XRP/USDT | trades=1 wins=0 losses=0 flat=1 pnl=0",
                ]
            ),
        )

    def test_periodic_summary_reports_empty_period_and_does_not_repeat_same_slot(self):
        settings = resolve_notification_settings({"HERMES_NOTIFY_SUMMARY_FREQUENCIES": "[12h,1w]"})

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "decisions.jsonl").write_text(
                '{"timestamp":"2026-07-28T01:00:00+00:00","asset":"BTC/USDT","trade_closed":false}\n',
                encoding="utf-8",
            )
            (state / "trades.jsonl").write_text("", encoding="utf-8")
            (state / "orders.jsonl").write_text("", encoding="utf-8")

            first = build_due_periodic_summaries(
                state,
                settings,
                now=datetime.fromisoformat("2026-07-28T12:01:00+00:00"),
            )
            second = build_due_periodic_summaries(
                state,
                settings,
                now=datetime.fromisoformat("2026-07-28T12:05:00+00:00"),
            )

        self.assertEqual(len(first), 2)
        half_day = next(message for message in first if "Periodic summary 12h" in message)
        self.assertIn("Trades: total=0 wins=0 losses=0 flat=0 total_pnl=0", half_day)
        self.assertIn("Orders: total=0 by_status=none", half_day)
        self.assertIn("By symbol:\nNo closed trades", half_day)
        self.assertEqual(second, [])

    def test_cycle_and_hourly_summaries_can_publish_together(self):
        settings = resolve_notification_settings(
            {
                "HERMES_NOTIFY_ENABLED": "true",
                "HERMES_NOTIFY_SERVICE": "discord",
                "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token",
                "HERMES_NOTIFY_SUMMARY_EVERY_CYCLES": "1",
                "HERMES_NOTIFY_SUMMARY_FREQUENCIES": "hourly",
            }
        )
        counter = PnlSummaryCounter(settings)

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "decisions.jsonl").write_text(
                '{"timestamp":"2026-07-29T08:05:00+00:00","asset":"BTC/USDT","trade_closed":true}\n',
                encoding="utf-8",
            )
            (state / "trades.jsonl").write_text(
                '{"timestamp":"2026-07-29T08:05:00+00:00","asset":"BTC/USDT","realized_pnl_pct":0.001}\n',
                encoding="utf-8",
            )
            (state / "orders.jsonl").write_text(
                '{"timestamp":"2026-07-29T08:06:00+00:00","asset":"BTC/USDT","status":"simulated","side":"buy"}\n',
                encoding="utf-8",
            )

            summaries = build_summary_notifications(
                state,
                [{"asset": "BTC/USDT", "realized_pnl_pct": 0.001}],
                settings,
                counter,
                datetime.fromisoformat("2026-07-29T09:01:00+00:00"),
            )

        self.assertEqual(len(summaries), 2)
        self.assertIn("P&L summary 1 cycles", summaries[0])
        self.assertIn("Periodic summary 1h", summaries[1])
        self.assertIn("Window: 2026-07-29T08:00:00+00:00 -> 2026-07-29T09:00:00+00:00", summaries[1])


class LoopNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_loop_notifies_worker_error_when_enabled(self):
        messages = []

        async def fake_send(settings, content):
            messages.append(content)
            return True

        async def failing_evaluate_asset(asset, strategy):
            raise RuntimeError("price adapter offline")

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
                },
                clear=False,
            ):
                with patch("hermes_trading.loop._evaluate_asset", failing_evaluate_asset):
                    with patch("hermes_trading.loop.send_notification", fake_send):
                        task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                        for _ in range(20):
                            if messages:
                                break
                            await asyncio.sleep(0.01)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

        self.assertEqual(len(messages), 1)
        self.assertIn("Worker error 1/5", messages[0])
        self.assertIn("price adapter offline", messages[0])
