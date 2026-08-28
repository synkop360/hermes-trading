import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

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
            "position_viewer.py",
            "sync_railway_state.py",
            "mark_reflection_regime.py",
            "watch_consultant.py",
            "compare_wallets.py",
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

    def test_reset_qualification_restarts_clock_and_clears_evidence(self) -> None:
        state_dir = ROOT / ".test-tmp-reset-qualification"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "qualification-training-started-at.txt").write_text(
            "2020-01-01T00:00:00+00:00\n", encoding="utf-8"
        )
        (state_dir / "qualification-evidence.json").write_text('{"fees_and_slippage_charged": true}', encoding="utf-8")
        (state_dir / "qualification-replay.json").write_text('{"status": "pass"}', encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "reset_qualification.py"),
                "--state-dir",
                str(state_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        restarted_at = (state_dir / "qualification-training-started-at.txt").read_text(encoding="utf-8").strip()
        self.assertNotEqual(restarted_at, "2020-01-01T00:00:00+00:00")
        self.assertFalse((state_dir / "qualification-evidence.json").exists())
        self.assertFalse((state_dir / "qualification-replay.json").exists())

    def test_mark_reflection_regime_resets_marker_to_now(self) -> None:
        state_dir = ROOT / ".test-tmp-mark-reflection-regime"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "reflection-regime-started-at.txt").write_text("2020-01-01T00:00:00+00:00\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "mark_reflection_regime.py"),
                "--state-dir",
                str(state_dir),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        marker = (state_dir / "reflection-regime-started-at.txt").read_text(encoding="utf-8").strip()
        self.assertNotEqual(marker, "2020-01-01T00:00:00+00:00")
        self.assertIn(marker, result.stdout)

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


class WatchConsultantTests(unittest.TestCase):
    @staticmethod
    def _load_module():
        script_path = ROOT / "scripts" / "watch_consultant.py"
        spec = importlib.util.spec_from_file_location("watch_consultant", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_default_interval_matches_prods_observed_cadence(self) -> None:
        module = self._load_module()

        self.assertEqual(module.DEFAULT_INTERVAL_SECONDS, 900.0)

    def test_run_reflection_once_calls_hermes_path_not_deterministic(self) -> None:
        module = self._load_module()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(module.subprocess, "run") as run,
        ):
            state_dir = Path(tmp)
            module.run_reflection_once(state_dir, ROOT)

        command = run.call_args.args[0]
        self.assertIn("--hermes", command)
        self.assertIn("--state-dir", command)
        self.assertEqual(command[command.index("--state-dir") + 1], str(state_dir))

    def test_main_calls_reflection_unconditionally_every_interval_with_no_pre_gate(self) -> None:
        module = self._load_module()
        calls: list[str] = []

        def fake_run_reflection_once(state_dir, project_dir):
            calls.append(str(state_dir))
            if len(calls) >= 3:
                raise SystemExit("stop-test-loop")
            return None

        module.run_reflection_once = fake_run_reflection_once

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                module.main(["--state-dir", tmp, "--interval-seconds", "0.001"])

        self.assertEqual(str(caught.exception), "stop-test-loop")
        # No evidence was ever written to state_dir -- if a pre-gate still
        # existed, this would never have called run_reflection_once at all.
        self.assertEqual(len(calls), 3)

    def test_main_calls_once_and_exits_when_interval_is_zero(self) -> None:
        module = self._load_module()
        calls: list[str] = []
        module.run_reflection_once = lambda state_dir, project_dir: calls.append(str(state_dir))

        with tempfile.TemporaryDirectory() as tmp:
            exit_code = module.main(["--state-dir", tmp, "--interval-seconds", "0"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)

    def test_main_survives_a_failed_reflection_call_and_keeps_polling(self) -> None:
        module = self._load_module()
        calls = {"count": 0}

        def flaky_run_reflection_once(state_dir, project_dir):
            calls["count"] += 1
            if calls["count"] == 1:
                raise subprocess.CalledProcessError(1, ["uv"], output="", stderr="local model unreachable")
            raise SystemExit("stop-test-loop")

        module.run_reflection_once = flaky_run_reflection_once

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                module.main(["--state-dir", tmp, "--interval-seconds", "0.001"])

        self.assertEqual(calls["count"], 2)


class CompareWalletsTests(unittest.TestCase):
    @staticmethod
    def _load_module():
        script_path = ROOT / "scripts" / "compare_wallets.py"
        spec = importlib.util.spec_from_file_location("compare_wallets", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_summarize_scopes_trades_to_the_marker_and_computes_wallet_balance(self) -> None:
        module = self._load_module()

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "qualification-training-started-at.txt").write_text(
                "2026-08-15T00:00:00+00:00\n", encoding="utf-8"
            )
            trades = [
                {"closed_at": "2026-08-10T00:00:00+00:00", "realized_pnl": "-5.00"},  # before marker, excluded
                {"closed_at": "2026-08-16T00:00:00+00:00", "realized_pnl": "1.50"},
                {"closed_at": "2026-08-17T00:00:00+00:00", "realized_pnl": "-0.75"},
            ]
            (state_dir / "trades.jsonl").write_text(
                "\n".join(json.dumps(row) for row in trades) + "\n", encoding="utf-8"
            )

            summary = module.summarize(state_dir, "baseline")

        self.assertEqual(summary["trade_count"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["total_realized_pnl"], Decimal("0.75"))
        self.assertEqual(summary["wallet_balance"], Decimal("50.75"))
        self.assertEqual(summary["win_rate_pct"], 50.0)

    def test_summarize_handles_zero_trades(self) -> None:
        module = self._load_module()

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "qualification-training-started-at.txt").write_text(
                "2026-08-18T00:00:00+00:00\n", encoding="utf-8"
            )
            (state_dir / "trades.jsonl").write_text("")

            summary = module.summarize(state_dir, "consultant")

        self.assertEqual(summary["trade_count"], 0)
        self.assertIsNone(summary["win_rate_pct"])
        self.assertEqual(summary["wallet_balance"], Decimal("50"))

    def test_summarize_raises_when_state_dir_is_missing(self) -> None:
        module = self._load_module()

        with self.assertRaises(FileNotFoundError):
            module.summarize(ROOT / ".test-tmp-does-not-exist", "baseline")

    def test_format_comparison_includes_both_labels_and_state_dirs(self) -> None:
        module = self._load_module()
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            state_a = Path(tmp_a)
            state_b = Path(tmp_b)
            (state_a / "qualification-training-started-at.txt").write_text(
                "2026-08-18T00:00:00+00:00\n", encoding="utf-8"
            )
            (state_a / "trades.jsonl").write_text("")
            (state_b / "qualification-training-started-at.txt").write_text(
                "2026-08-18T00:00:00+00:00\n", encoding="utf-8"
            )
            (state_b / "trades.jsonl").write_text("")

            baseline = module.summarize(state_a, "baseline")
            consultant = module.summarize(state_b, "consultant")
            rendered = module.format_comparison(baseline, consultant)

        self.assertIn(str(state_a), rendered)
        self.assertIn(str(state_b), rendered)
        self.assertIn("BASELINE", rendered)
        self.assertIn("CONSULTANT", rendered)

    def test_format_comparison_rounds_wallet_and_pnl_to_three_decimals(self) -> None:
        module = self._load_module()
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "qualification-training-started-at.txt").write_text(
                "2026-08-18T00:00:00+00:00\n", encoding="utf-8"
            )
            trades = [
                {"closed_at": "2026-08-19T00:00:00+00:00", "realized_pnl": "0.0095320489123"},
            ]
            (state_dir / "trades.jsonl").write_text(
                "\n".join(json.dumps(row) for row in trades) + "\n", encoding="utf-8"
            )

            summary = module.summarize(state_dir, "baseline")
            rendered = module.format_comparison(summary, summary)

        self.assertIn("50.010", rendered)
        self.assertIn("0.010", rendered)
        self.assertNotIn("0.0095320489123", rendered)


class SyncRailwayStateTests(unittest.TestCase):
    @staticmethod
    def _load_module():
        script_path = ROOT / "scripts" / "sync_railway_state.py"
        spec = importlib.util.spec_from_file_location("sync_railway_state", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_download_remote_file_writes_content_and_strips_ssh_banner(self) -> None:
        module = self._load_module()
        calls: list[list[str]] = []

        def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(
                command, 0, stdout="Using SSH key from file foo.pub: bar\n{\"positions\": []}\n", stderr=""
            )

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrote = module.download_remote_file(state_dir, "positions.json", runner=fake_runner)

            self.assertTrue(wrote)
            self.assertEqual(
                (state_dir / "positions.json").read_text(encoding="utf-8"), '{"positions": []}'
            )
            self.assertEqual(calls[0][-1], "cat /app/state/positions.json 2>/dev/null || true")

    def test_download_remote_file_skips_when_remote_content_empty(self) -> None:
        module = self._load_module()

        def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrote = module.download_remote_file(state_dir, "positions.json", runner=fake_runner)

            self.assertFalse(wrote)
            self.assertFalse((state_dir / "positions.json").exists())

    def test_download_remote_file_rejects_the_ssh_gateway_discovery_payload(self) -> None:
        # Seen in production: railway ssh -- "<command>" returned Railway's own
        # account/discovery JSON instead of executing the command, and this
        # silently overwrote goal.yaml/strategy.yaml/trades.jsonl with it (only
        # empty responses were rejected before) -- corrupting the live baseline
        # instance's config until caught and repaired by hand.
        module = self._load_module()
        gateway_payload = (
            '{"status":"ready","account":{"user":{"name":null,"username":"synkop360"}},'
            '"actions":[{"command":"ssh -T whoami@railway.new"}]}'
        )

        def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout=gateway_payload, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "strategy.yaml").write_text("version: '187'\n", encoding="utf-8")

            wrote = module.download_remote_file(state_dir, "strategy.yaml", runner=fake_runner)

            self.assertFalse(wrote)
            self.assertEqual((state_dir / "strategy.yaml").read_text(encoding="utf-8"), "version: '187'\n")

    def test_sync_once_reports_only_successfully_downloaded_files(self) -> None:
        module = self._load_module()

        def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            requested_name = command[-1].split("cat ")[1].split(" ")[0].rsplit("/", 1)[-1]
            if requested_name == "positions.json":
                return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            synced = module.sync_once(
                state_dir, runner=fake_runner, files=["positions.json", "trades.jsonl"]
            )

            self.assertEqual(synced, ["positions.json"])
            self.assertTrue((state_dir / "positions.json").exists())
            self.assertFalse((state_dir / "trades.jsonl").exists())

    def test_download_remote_file_survives_transient_railway_ssh_failure(self) -> None:
        module = self._load_module()

        def failing_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, command, output="", stderr="ssh: connection reset")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            wrote = module.download_remote_file(state_dir, "positions.json", runner=failing_runner)

            self.assertFalse(wrote)
            self.assertFalse((state_dir / "positions.json").exists())

    def test_sync_once_skips_file_whose_ssh_call_fails_but_still_syncs_the_rest(self) -> None:
        module = self._load_module()

        def flaky_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            requested_name = command[-1].split("cat ")[1].split(" ")[0].rsplit("/", 1)[-1]
            if requested_name == "trades.jsonl":
                raise subprocess.CalledProcessError(1, command, output="", stderr="ssh: connection reset")
            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            synced = module.sync_once(
                state_dir, runner=flaky_runner, files=["positions.json", "trades.jsonl", "heartbeat.json"]
            )

            self.assertEqual(synced, ["positions.json", "heartbeat.json"])

    def test_main_loop_survives_a_failed_cycle_and_keeps_polling(self) -> None:
        module = self._load_module()
        calls = {"count": 0}

        def flaky_sync_once(state_dir, *, runner=module.default_runner, files=module.STATE_FILES):
            calls["count"] += 1
            if calls["count"] == 1:
                raise subprocess.CalledProcessError(1, ["railway.cmd"], output="", stderr="ssh: connection reset")
            raise SystemExit("stop-test-loop")

        module.sync_once = flaky_sync_once

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as caught:
                module.main(["--state-dir", tmp, "--interval-seconds", "0.01"])

        self.assertEqual(str(caught.exception), "stop-test-loop")
        self.assertEqual(calls["count"], 2)


class HermesWatchTests(unittest.TestCase):
    @staticmethod
    def _load_module():
        script_path = ROOT / "scripts" / "hermes_watch.py"
        spec = importlib.util.spec_from_file_location("hermes_watch", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_download_remote_file_rejects_the_ssh_gateway_discovery_payload(self) -> None:
        # Same failure as sync_railway_state.py's guard covers, but this is
        # the external hermes-trading-watch cron's own separate, unpatched
        # copy of the same download logic -- it re-corrupted every local
        # state file on every unattended cycle (~every 15-20 min) while
        # railway ssh was returning this payload instead of running the
        # requested command, wiping out a hand-repaired state/strategy.yaml
        # within minutes of it being fixed.
        module = self._load_module()
        gateway_payload = (
            '{"status":"ready","account":{"user":{"name":null,"username":"synkop360"}},'
            '"actions":[{"command":"ssh -T whoami@railway.new"}]}'
        )

        def fake_run(command, *, input_text=None):
            return subprocess.CompletedProcess(command, 0, stdout=gateway_payload, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            (state_dir / "strategy.yaml").write_text("version: '187'\n", encoding="utf-8")
            module._run = fake_run
            module.STATE_DIR = state_dir

            wrote = module._download_remote_file("strategy.yaml")

            self.assertFalse(wrote)
            self.assertEqual((state_dir / "strategy.yaml").read_text(encoding="utf-8"), "version: '187'\n")
