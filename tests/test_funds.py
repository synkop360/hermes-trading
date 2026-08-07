import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from hermes_trading.funds import (
    VIRTUAL_WALLET_NOTE,
    FundsAlertConfigError,
    build_funds_alert_from_wallet_balance,
    build_funds_alert_message,
    build_funds_snapshot,
    calculate_critical_limit,
    calculate_low_limit,
    extract_available_funds,
    fetch_available_funds,
    format_funds_alert,
    resolve_funds_alert_settings,
)


class FundsAlertSettingsTests(unittest.TestCase):
    def test_funds_alerts_are_disabled_by_default(self):
        settings = resolve_funds_alert_settings({})

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.quote, "USDT")
        self.assertEqual(settings.min_available, 20.0)
        self.assertEqual(settings.min_order_multiple, 3.0)
        self.assertEqual(settings.buffer_pct, 0.10)
        self.assertEqual(settings.cooldown_seconds, 21600)
        self.assertEqual(settings.resolve_multiple, 1.20)
        self.assertEqual(settings.mention, "")

    def test_low_limit_uses_absolute_or_dynamic_limit_whichever_is_higher(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_ALERT_ENABLED": "true",
                "HERMES_FUNDS_MIN_AVAILABLE_USDT": "20",
                "HERMES_FUNDS_MIN_ORDER_MULTIPLE": "3",
                "HERMES_FUNDS_BUFFER_PCT": "0.10",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )

        self.assertEqual(calculate_low_limit(settings), 20.0)

    def test_dynamic_low_limit_can_exceed_absolute_floor(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_MIN_AVAILABLE_USDT": "10",
                "HERMES_FUNDS_MIN_ORDER_MULTIPLE": "5",
                "HERMES_FUNDS_BUFFER_PCT": "0.20",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )

        self.assertEqual(calculate_low_limit(settings), 30.0)

    def test_critical_limit_uses_order_budget_or_exchange_min_cost(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        self.assertEqual(calculate_critical_limit(settings, exchange_min_cost=1.0), 5.0)
        self.assertEqual(calculate_critical_limit(settings, exchange_min_cost=12.0), 12.0)

    def test_invalid_numeric_settings_raise_clear_error(self):
        with self.assertRaises(FundsAlertConfigError):
            resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_COOLDOWN_SECONDS": "-1"})


class FundsAlertSeverityTests(unittest.TestCase):
    def test_warning_means_below_low_limit_but_above_critical_limit(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        self.assertEqual(snapshot.severity, "warning")
        self.assertEqual(snapshot.low_limit, 20.0)
        self.assertEqual(snapshot.critical_limit, 5.0)

    def test_critical_means_available_cannot_cover_configured_order_budget(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=3.91, exchange_min_cost=1.0)

        self.assertEqual(snapshot.severity, "critical")

    def test_ok_means_available_is_above_low_limit(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=26.40, exchange_min_cost=1.0)

        self.assertEqual(snapshot.severity, "ok")

    def test_warning_message_is_short_and_visible(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_QUOTE": "USDT"})
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, order_budget=5.0)

        message = format_funds_alert(snapshot, "warning", mention="@here")

        self.assertEqual(
            message,
            "\n".join(
                [
                    "@here",
                    "[HERMES WARNING] LOW FUNDS",
                    "",
                    "Quote: USDT",
                    "Available: 14.82",
                    "Low limit: 20.00",
                    "Critical limit: 5.00",
                    "",
                    "Action: add funds or reduce order size",
                ]
            ),
        )

    def test_critical_message_has_critical_heading_and_action(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_QUOTE": "USDT"})
        snapshot = build_funds_snapshot(settings, available=3.91, exchange_min_cost=1.0, order_budget=5.0)

        message = format_funds_alert(snapshot, "critical", mention="@here")

        self.assertIn("[HERMES CRITICAL] FUNDS TOO LOW", message)
        self.assertIn("Action: pause live trading or add funds now", message)


class VirtualWalletFundsAlertTests(unittest.TestCase):
    def test_virtual_snapshot_defaults_source_to_real(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        self.assertEqual(snapshot.source, "real")

    def test_virtual_wallet_alert_carries_note_and_no_real_funds(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, source="virtual")

        message = format_funds_alert(snapshot, "warning", mention="@here")

        self.assertIn(VIRTUAL_WALLET_NOTE, message)

    def test_real_wallet_alert_has_no_virtual_note(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        message = format_funds_alert(snapshot, "warning", mention="@here")

        self.assertNotIn(VIRTUAL_WALLET_NOTE, message)

    def test_build_funds_alert_from_wallet_balance_never_touches_network(self):
        settings = resolve_funds_alert_settings(
            {"HERMES_FUNDS_ALERT_ENABLED": "true", "HERMES_TRADING_MAX_ORDER_USDT": "5"}
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            message = build_funds_alert_from_wallet_balance(
                Path(temp_dir),
                settings,
                Decimal("3.91"),
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )

        self.assertIn("[HERMES CRITICAL] FUNDS TOO LOW", message)
        self.assertIn(VIRTUAL_WALLET_NOTE, message)


class FundsAlertStateTests(unittest.TestCase):
    def test_first_warning_alert_is_sent_and_state_is_persisted(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_ALERT_ENABLED": "true",
                "HERMES_FUNDS_ALERT_MENTION": "@here",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            message = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIn("[HERMES WARNING] LOW FUNDS", message)
        self.assertEqual(saved["status"], "warning")
        self.assertEqual(saved["last_available"], 14.82)

    def test_warning_repeats_only_after_cooldown(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_ALERT_ENABLED": "true",
                "HERMES_FUNDS_ALERT_COOLDOWN_SECONDS": "21600",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            first = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            second = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T10:00:00+00:00"),
            )
            third = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T15:00:01+00:00"),
            )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)

    def test_critical_upgrade_bypasses_warning_cooldown(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_ENABLED": "true"})
        warning = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, order_budget=5.0)
        critical = build_funds_snapshot(settings, available=3.91, exchange_min_cost=1.0, order_budget=5.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            build_funds_alert_message(
                state_path,
                settings,
                warning,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            message = build_funds_alert_message(
                state_path,
                settings,
                critical,
                datetime.fromisoformat("2026-07-29T09:05:00+00:00"),
            )

        self.assertIn("[HERMES CRITICAL] FUNDS TOO LOW", message)

    def test_resolved_message_is_sent_only_above_resolve_limit(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_ENABLED": "true"})
        warning = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, order_budget=5.0)
        still_low = build_funds_snapshot(settings, available=22.0, exchange_min_cost=1.0, order_budget=5.0)
        resolved = build_funds_snapshot(settings, available=26.4, exchange_min_cost=1.0, order_budget=5.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            build_funds_alert_message(
                state_path,
                settings,
                warning,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            no_message = build_funds_alert_message(
                state_path,
                settings,
                still_low,
                datetime.fromisoformat("2026-07-29T10:00:00+00:00"),
            )
            message = build_funds_alert_message(
                state_path,
                settings,
                resolved,
                datetime.fromisoformat("2026-07-29T11:00:00+00:00"),
            )

        self.assertIsNone(no_message)
        self.assertIn("[HERMES RESOLVED] FUNDS BACK ABOVE LIMIT", message)


class FakeBalanceExchange:
    def __init__(self, balance):
        self.balance = balance
        self.closed = False

    async def fetch_balance(self):
        return self.balance

    async def close(self):
        self.closed = True


class FundsBalanceTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_available_funds_prefers_free_balance(self):
        balance = {
            "free": {"USDT": 14.82},
            "total": {"USDT": 30.0},
        }

        self.assertEqual(extract_available_funds(balance, "USDT"), 14.82)

    def test_extract_available_funds_supports_kraken_extended_shape(self):
        balance = {
            "info": {
                "result": {
                    "USDT": {
                        "balance": "30.00",
                        "credit": "0",
                        "credit_used": "0",
                        "hold_trade": "12.50",
                    }
                }
            }
        }

        self.assertEqual(extract_available_funds(balance, "USDT"), 17.5)

    async def test_fetch_available_funds_closes_exchange(self):
        fake = FakeBalanceExchange({"free": {"USDT": 14.82}})
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_QUOTE": "USDT"})

        available = await fetch_available_funds(settings, exchange_factory=lambda: fake)

        self.assertEqual(available, 14.82)
        self.assertTrue(fake.closed)
