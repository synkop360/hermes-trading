import importlib.util
import subprocess
import sys
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
        self.assertIn('CMD ["uv", "run", "python", "-m", "hermes_trading.colocated"]', dockerfile)

    def test_deployment_doc_names_separate_service_commands(self) -> None:
        content = (ROOT / "docs" / "RAILWAY_CONTROL_DISCORD_DEPLOYMENT_2026-08-01.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv run python -m hermes_trading.run", content)
        self.assertIn("uv run python -m hermes_trading.colocated", content)
        self.assertIn("uv run python scripts/run_control_api.py", content)
        self.assertIn("uv run python scripts/run_discord_bot.py", content)
        self.assertIn("HERMES_TRADING_ALLOW_REAL_ORDERS=false", content)

    def test_control_api_and_discord_bot_scripts_are_importable(self) -> None:
        for script_name in (
            "control_command.py",
            "run_control_api.py",
            "run_discord_bot.py",
            "check_control_discord_secrets.py",
        ):
            script_path = ROOT / "scripts" / script_name
            spec = importlib.util.spec_from_file_location(script_name, script_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.main))

    def test_control_discord_secret_preflight_reports_missing_without_values(self) -> None:
        script_path = ROOT / "scripts" / "check_control_discord_secrets.py"
        spec = importlib.util.spec_from_file_location("check_control_discord_secrets", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module.check_readiness("all", {})

        self.assertIn("missing control-api: HERMES_CONTROL_HMAC_SECRET", result)
        self.assertIn(
            "missing discord-bot: HERMES_DISCORD_BOT_TOKEN, HERMES_DISCORD_ALLOWED_USER_IDS, "
            "HERMES_CONTROL_HMAC_SECRET, HERMES_DISCORD_TOTP_SECRET",
            result,
        )

    def test_control_discord_secret_preflight_accepts_ready_configuration(self) -> None:
        script_path = ROOT / "scripts" / "check_control_discord_secrets.py"
        spec = importlib.util.spec_from_file_location("check_control_discord_secrets", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module.check_readiness(
            "all",
            {
                "HERMES_CONTROL_HMAC_SECRET": "control-secret-value",
                "HERMES_DISCORD_BOT_TOKEN": "discord-token-value",
                "HERMES_DISCORD_ALLOWED_USER_IDS": "123,456",
                "HERMES_DISCORD_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                "HERMES_CONTROL_BASE_URL": "http://127.0.0.1:8765",
            },
        )

        self.assertIn("ready control-api: required secrets present", result)
        self.assertIn("ready discord-bot: required secrets present", result)
        self.assertIn("ready discord-bot: 2 allowed user id(s) configured", result)
        self.assertIn("ready discord-bot: control URL is loopback", result)
        self.assertNotIn("control-secret-value", "\n".join(result))
        self.assertNotIn("discord-token-value", "\n".join(result))

    def test_control_discord_secret_preflight_rejects_non_loopback_control_url(self) -> None:
        script_path = ROOT / "scripts" / "check_control_discord_secrets.py"
        spec = importlib.util.spec_from_file_location("check_control_discord_secrets", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module.check_readiness(
            "discord-bot",
            {
                "HERMES_CONTROL_HMAC_SECRET": "control-secret-value",
                "HERMES_DISCORD_BOT_TOKEN": "discord-token-value",
                "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                "HERMES_DISCORD_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                "HERMES_CONTROL_BASE_URL": "http://control-api.railway.internal:8765",
            },
        )

        self.assertIn("invalid discord-bot: HERMES_CONTROL_BASE_URL must be an HTTP loopback URL", result)

    def test_control_discord_secret_preflight_accepts_allowed_private_control_url(self) -> None:
        script_path = ROOT / "scripts" / "check_control_discord_secrets.py"
        spec = importlib.util.spec_from_file_location("check_control_discord_secrets", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module.check_readiness(
            "discord-bot",
            {
                "HERMES_CONTROL_HMAC_SECRET": "control-secret-value",
                "HERMES_DISCORD_BOT_TOKEN": "discord-token-value",
                "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                "HERMES_DISCORD_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                "HERMES_CONTROL_BASE_URL": "http://control-api.railway.internal:8765",
                "HERMES_CONTROL_ALLOWED_HOSTS": "control-api.railway.internal",
            },
        )

        self.assertIn("ready discord-bot: control URL host is explicitly allowed", result)

    def test_control_command_parses_allowed_control_hosts(self) -> None:
        script_path = ROOT / "scripts" / "control_command.py"
        spec = importlib.util.spec_from_file_location("control_command", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertEqual(
            module.allowed_control_hosts("control-api.railway.internal; other.internal"),
            frozenset({"control-api.railway.internal", "other.internal"}),
        )

    def test_control_command_supports_direct_container_execution(self) -> None:
        content = (ROOT / "scripts" / "control_command.py").read_text(encoding="utf-8")

        self.assertIn("sys.path.insert(0, str(Path(__file__).resolve().parents[1]))", content)

    def test_qualification_evidence_status_script_imports_project(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "qualification_evidence_status.py"),
                "--state-dir",
                str(ROOT / ".test-tmp-qualification-evidence-status"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replay_without_lookahead=", result.stdout)
        self.assertIn("fees_and_slippage_charged=", result.stdout)
