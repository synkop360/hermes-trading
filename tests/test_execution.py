import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hermes_trading.adapters.execution import ExecutionSafetyError, ExecutionSettings, execute_signal, resolve_execution_settings
from hermes_trading.loop import LIVE_CONFIRM_PHRASE
from hermes_trading.run import ensure_state_files


class ExecutionSettingsTests(unittest.TestCase):
    def test_training_mode_selects_simulated_executor(self):
        settings = resolve_execution_settings({"HERMES_TRADING_MODE": "training"})

        self.assertEqual(settings.mode, "training")
        self.assertEqual(settings.executor, "simulated")
        self.assertFalse(settings.allow_real_orders)
        self.assertTrue(settings.validate_only)

    def test_live_requires_validation_only_by_default(self):
        settings = resolve_execution_settings(
            {
                "HERMES_TRADING_MODE": "live",
                "HERMES_TRADING_I_ACCEPT_RISK": "true",
                "HERMES_TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE,
                "HERMES_TRADING_LIVE_VALIDATE_ONLY": "true",
            }
        )

        self.assertEqual(settings.executor, "kraken")
        self.assertTrue(settings.validate_only)
        self.assertFalse(settings.allow_real_orders)

    def test_real_live_orders_need_separate_allow_flag(self):
        with self.assertRaises(ExecutionSafetyError):
            resolve_execution_settings(
                {
                    "HERMES_TRADING_MODE": "live",
                    "HERMES_TRADING_I_ACCEPT_RISK": "true",
                    "HERMES_TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE,
                    "HERMES_TRADING_LIVE_VALIDATE_ONLY": "false",
                }
            )


class ExecuteSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulated_executor_returns_no_order_when_no_signal(self):
        settings = ExecutionSettings("training", "simulated", validate_only=True, allow_real_orders=False)

        result = await execute_signal(None, {}, settings)

        self.assertIsNone(result)

    async def test_simulated_executor_records_virtual_order(self):
        settings = ExecutionSettings("training", "simulated", validate_only=True, allow_real_orders=False)
        signal = {
            "action": "enter",
            "order_type": "market",
            "side": "buy",
            "asset": "BTC/USDT",
            "reference_price": 100.0,
        }

        result = await execute_signal(signal, {"position_size_r": 0.5}, settings)

        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["asset"], "BTC/USDT")
        self.assertEqual(result["side"], "buy")


class FakeExchange:
    def __init__(self):
        self.closed = False
        self.orders = []

    async def load_markets(self):
        return {
            "BTC/USDT": {
                "limits": {"amount": {"min": 0.00001}, "cost": {"min": 1.0}},
                "precision": {"amount": 8},
            }
        }

    def amount_to_precision(self, asset, amount):
        return f"{float(amount):.8f}"

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.orders.append((symbol, type, side, amount, price, params))
        return {"id": "validated-order", "status": "open", "info": {"validate": params.get("validate")}}

    async def close(self):
        self.closed = True


class KrakenExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_kraken_validate_only_uses_validate_param(self):
        fake = FakeExchange()
        settings = ExecutionSettings("live", "kraken", validate_only=True, allow_real_orders=False)
        signal = {
            "action": "enter",
            "order_type": "market",
            "side": "buy",
            "asset": "BTC/USDT",
            "reference_price": 100.0,
        }

        result = await execute_signal(signal, {"position_size_r": 0.5}, settings, exchange_factory=lambda: fake)

        self.assertEqual(result["status"], "validated")
        self.assertEqual(fake.orders[0][5]["validate"], True)
        self.assertTrue(fake.closed)


class StateBootstrapExecutionTests(unittest.TestCase):
    def test_ensure_state_files_creates_orders_journal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)

            ensure_state_files(state)

            self.assertTrue((state / "orders.jsonl").exists())

    def test_ensure_state_files_creates_funds_alert_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)

            ensure_state_files(state)

            self.assertTrue((state / "funds_alert.json").exists())


class LiveReadinessScriptTests(unittest.TestCase):
    def test_readiness_rejects_real_orders_without_allow_flag(self):
        env = os.environ.copy()
        env.update(
            {
                "HERMES_TRADING_MODE": "live",
                "HERMES_TRADING_I_ACCEPT_RISK": "true",
                "HERMES_TRADING_LIVE_CONFIRM": "I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS",
                "HERMES_TRADING_LIVE_VALIDATE_ONLY": "false",
                "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
            }
        )

        result = subprocess.run(
            [sys.executable, "scripts/check_live_readiness.py"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("real orders require", result.stderr)
