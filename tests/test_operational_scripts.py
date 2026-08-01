import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OperationalScriptTests(unittest.TestCase):
    def test_container_defaults_keep_trading_in_training_without_real_orders(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY pyproject.toml uv.lock ./", dockerfile)
        self.assertIn("COPY hermes_trading ./hermes_trading", dockerfile)
        self.assertIn("COPY scripts ./scripts", dockerfile)
        self.assertIn("RUN uv sync", dockerfile)
        self.assertIn("ENV HERMES_TRADING_MODE=training", dockerfile)
        self.assertIn("ENV HERMES_TRADING_ALLOW_REAL_ORDERS=false", dockerfile)
        self.assertIn('CMD ["uv", "run", "python", "-m", "hermes_trading.run"]', dockerfile)

    def test_deployment_doc_names_separate_service_commands(self) -> None:
        content = (ROOT / "docs" / "RAILWAY_CONTROL_DISCORD_DEPLOYMENT_2026-08-01.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv run python -m hermes_trading.run", content)
        self.assertIn("uv run python scripts/run_control_api.py", content)
        self.assertIn("uv run python scripts/run_discord_bot.py", content)
        self.assertIn("HERMES_TRADING_ALLOW_REAL_ORDERS=false", content)

    def test_control_api_and_discord_bot_scripts_are_importable(self) -> None:
        for script_name in ("control_command.py", "run_control_api.py", "run_discord_bot.py"):
            script_path = ROOT / "scripts" / script_name
            spec = importlib.util.spec_from_file_location(script_name, script_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.main))
