import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hermes_trading.domain import Decision, Fill, OrderIntent, OrderRecord, OrderStatus
from hermes_trading.persistence.repository import StateRepository


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
            "discord_bot_smoke.py",
            "run_control_api.py",
            "run_discord_bot.py",
            "check_control_discord_secrets.py",
            "generate_final_qualification_evidence.py",
            "generate_qualification_replay.py",
            "qualification_evidence_status.py",
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

    def test_generate_qualification_replay_writes_journal_scan_report(self) -> None:
        state_dir = ROOT / ".test-tmp-generate-qualification-replay"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "decisions.jsonl").write_text(
            '{"timestamp":"2026-08-01T10:00:00+00:00","asset":"BTC/USDT","event":"decision"}\n'
            '{"action":"enter","asset":"BTC/USDT","candle_id":"BTC/USDT:1h:2026-08-01T09:00:00+00:00",'
            '"decided_at":"2026-08-01T09:00:00+00:00","decision_id":"decision-1"}\n'
            '{"timestamp":"2026-08-01T10:01:00+00:00","asset":"ETH/USDT","event":"decision"}\n',
            encoding="utf-8",
        )
        (state_dir / "trades.jsonl").write_text(
            '{"timestamp":"2026-08-01T10:02:00+00:00","asset":"BTC/USDT","event":"trade"}\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_qualification_replay.py"),
                "--state-dir",
                str(state_dir),
                "--generated-at",
                "2026-08-01T12:00:00+00:00",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )

        report = __import__("json").loads((state_dir / "qualification-replay.json").read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["candles_checked"], 3)
        self.assertEqual(report["events_checked"], 4)
        self.assertEqual(report["lookahead_violations"], 0)

    def test_generate_final_qualification_evidence_marks_remaining_gates(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        state_dir = Path(temporary_directory.name)
        repository = StateRepository.open(state_dir / "trading.db")
        try:
            repository.migrate()
            repository.record_decision(
                Decision(
                    "decision-final-evidence",
                    "BTC/USDT",
                    "enter",
                    "rsi_entry",
                    "BTC/USDT:1h:2026-08-01T12:00:00+00:00",
                    "83",
                    Decimal("65000"),
                    "2026-08-01T12:00:00+00:00",
                )
            )
            repository.create_order_intent(
                OrderIntent(
                    "client-final-evidence",
                    "decision-final-evidence",
                    "BTC/USDT",
                    "buy",
                    "market",
                    Decimal("0.001"),
                    Decimal("65000"),
                    "2026-08-01T12:00:00+00:00",
                )
            )
            repository.upsert_order(
                OrderRecord(
                    "order-final-evidence",
                    "client-final-evidence",
                    "BTC/USDT",
                    "buy",
                    "market",
                    OrderStatus.FILLED,
                    Decimal("0.001"),
                    Decimal("0.001"),
                    Decimal("65010"),
                    Decimal("0.01"),
                    "2026-08-01T12:00:00+00:00",
                    "2026-08-01T12:00:01+00:00",
                )
            )
            repository.record_fill(
                Fill(
                    "fill-final-evidence",
                    "order-final-evidence",
                    "BTC/USDT",
                    "buy",
                    Decimal("0.001"),
                    Decimal("65010"),
                    Decimal("0.01"),
                    "2026-08-01T12:00:01+00:00",
                )
            )
        finally:
            repository.close()
        (state_dir / "qualification-replay.json").write_text(
            json.dumps({
                "run_id": "replay-final-evidence",
                "status": "pass",
                "candles_checked": 1,
                "lookahead_violations": 0,
            }),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_final_qualification_evidence.py"),
                "--state-dir",
                str(state_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        status = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "qualification_evidence_status.py"),
                "--state-dir",
                str(state_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fees_and_slippage=generated", result.stdout)
        self.assertIn("duplicate_candle=generated", result.stdout)
        self.assertIn("risk_limit=generated", result.stdout)
        self.assertIn("fees_and_slippage_charged=pass", status.stdout)
        self.assertIn("duplicate_candle_protected=pass", status.stdout)
        self.assertIn("risk_limits_exercised=pass", status.stdout)

    def test_discord_bot_smoke_checks_commands_and_can_send_trading_channel_prompt_without_printing_token(self) -> None:
        script_path = ROOT / "scripts" / "discord_bot_smoke.py"
        spec = importlib.util.spec_from_file_location("discord_bot_smoke", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls: list[tuple[str, str, object | None]] = []

        def request_json(method: str, url: str, *, token: str, payload: object | None = None) -> object:
            calls.append((method, url, payload))
            self.assertEqual(token, "discord-token-value")
            if url.endswith("/users/@me"):
                return {"id": "bot-123", "username": "Hermes"}
            if url.endswith("/oauth2/applications/@me"):
                return {"id": "app-123", "name": "Hermes Trader"}
            if url.endswith("/applications/app-123/commands"):
                return [{"name": "status"}, {"name": "qualification"}, {"name": "config"}, {"name": "config_set"}]
            if url.endswith("/users/@me/guilds"):
                return [{"id": "guild-123", "name": "Hermes Ops"}]
            if url.endswith("/guilds/guild-123/channels"):
                return [{"id": "channel-123", "name": "trading", "type": 0}]
            if url.endswith("/users/@me/channels"):
                return {"id": "dm-123"}
            if url.endswith("/channels/channel-123/messages"):
                return {"id": "message-123"}
            raise AssertionError(url)

        lines = module.smoke_discord_bot(
            {
                "HERMES_DISCORD_BOT_TOKEN": "discord-token-value",
                "HERMES_DISCORD_ALLOWED_USER_IDS": "174585364944519168",
            },
            request_json=request_json,
            send_channel_message=True,
            channel_name="trading",
            message="Hermes smoke: lance /status, /qualification puis /config",
        )

        rendered = "\n".join(lines)
        self.assertIn("authenticated bot_id=bot-123 app_id=app-123", rendered)
        self.assertIn("commands=config,config_set,qualification,status", rendered)
        self.assertIn("channel=sent name=trading id=channel-123 message_id=message-123", rendered)
        self.assertNotIn("discord-token-value", rendered)
        self.assertEqual(
            calls[-1],
            (
                "POST",
                "https://discord.com/api/v10/channels/channel-123/messages",
                {"content": "Hermes smoke: lance /status, /qualification puis /config"},
            ),
        )
