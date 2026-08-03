import json
from http.client import HTTPConnection
from http.server import HTTPServer
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hermes_trading.control_api import LocalControlApi, make_control_handler
from hermes_trading.control_auth import sign_command
from hermes_trading.persistence.repository import StateRepository


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class LocalControlApiTests(unittest.TestCase):
    def signed_headers(self, secret: str, nonce: str, timestamp: datetime, body: str) -> dict[str, str]:
        return {
            "X-Hermes-Nonce": nonce,
            "X-Hermes-Timestamp": timestamp.isoformat(),
            "X-Hermes-Signature": sign_command(secret, nonce, timestamp, body),
        }

    def test_status_requires_hmac_and_rejects_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            api = LocalControlApi(repository, state_dir, secret="secret", now=lambda: NOW)
            headers = self.signed_headers("secret", "nonce-1", NOW, "")

            response = api.handle("GET", "/status", headers, "")
            replay = api.handle("GET", "/status", headers, "")
            bad_signature = api.handle(
                "GET",
                "/status",
                self.signed_headers("wrong", "nonce-2", NOW + timedelta(seconds=1), ""),
                "",
            )
            repository.close()

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["system_status"]["integrity"], "ok")
        self.assertFalse(payload["real_orders_allowed"])
        self.assertEqual(replay.status, 401)
        self.assertEqual(bad_signature.status, 401)

    def test_qualification_request_runs_report_without_promoting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            body = '{"phase":"validation_ready"}'
            api = LocalControlApi(repository, state_dir, secret="secret", now=lambda: NOW)

            response = api.handle(
                "POST",
                "/qualification",
                self.signed_headers("secret", "nonce-1", NOW, body),
                body,
            )
            repository.close()

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertEqual(payload["requested_phase"], "validation_ready")
        self.assertFalse(payload["promoted"])
        self.assertFalse(payload["promotable"])

    def test_config_lists_and_updates_only_allowlisted_operator_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            api = LocalControlApi(repository, state_dir, secret="secret", now=lambda: NOW)

            list_response = api.handle(
                "GET",
                "/config",
                self.signed_headers("secret", "nonce-config-list", NOW, ""),
                "",
            )
            body = '{"key":"HERMES_TRADING_TIMEFRAME","value":"15m"}'
            set_response = api.handle(
                "POST",
                "/config",
                self.signed_headers("secret", "nonce-config-set", NOW + timedelta(seconds=1), body),
                body,
            )
            dangerous_body = '{"key":"HERMES_TRADING_ALLOW_REAL_ORDERS","value":"true"}'
            dangerous_response = api.handle(
                "POST",
                "/config",
                self.signed_headers("secret", "nonce-config-danger", NOW + timedelta(seconds=2), dangerous_body),
                dangerous_body,
            )
            persisted = json.loads((state_dir / "operator_config.json").read_text(encoding="utf-8"))
            repository.close()

        self.assertEqual(list_response.status, 200)
        listed = json.loads(list_response.body)
        self.assertIn("HERMES_TRADING_TIMEFRAME", listed["settings"])
        self.assertNotIn("HERMES_DISCORD_BOT_TOKEN", listed["settings"])
        self.assertEqual(set_response.status, 200)
        updated = json.loads(set_response.body)
        self.assertEqual(updated["key"], "HERMES_TRADING_TIMEFRAME")
        self.assertEqual(updated["value"], "15m")
        self.assertTrue(updated["restart_required"])
        self.assertEqual(persisted["HERMES_TRADING_TIMEFRAME"], "15m")
        self.assertEqual(dangerous_response.status, 400)

    def test_http_handler_serves_signed_status_on_loopback_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            api = LocalControlApi(repository, state_dir, secret="secret", now=lambda: NOW)
            server = HTTPServer(("127.0.0.1", 0), make_control_handler(api))

            result: dict[str, object] = {}

            def request_status() -> None:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("GET", "/status", headers=self.signed_headers("secret", "nonce-http", NOW, ""))
                response = connection.getresponse()
                result["status"] = response.status
                result["payload"] = json.loads(response.read().decode())
                connection.close()

            thread = threading.Thread(target=request_status, daemon=True)
            thread.start()
            try:
                server.handle_request()
            finally:
                server.server_close()
                thread.join(timeout=5)
                repository.close()

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["payload"]["status"], "ok")
