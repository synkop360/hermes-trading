import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OperationalScriptTests(unittest.TestCase):
    def test_container_defaults_keep_trading_in_training_without_real_orders(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ENV HERMES_TRADING_MODE=training", dockerfile)
        self.assertIn("ENV HERMES_TRADING_ALLOW_REAL_ORDERS=false", dockerfile)

    def test_control_api_and_discord_bot_scripts_are_importable(self) -> None:
        for script_name in ("control_command.py", "run_control_api.py", "run_discord_bot.py"):
            script_path = ROOT / "scripts" / script_name
            spec = importlib.util.spec_from_file_location(script_name, script_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.main))
