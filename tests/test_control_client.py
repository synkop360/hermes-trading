import unittest
from datetime import UTC, datetime

from hermes_trading.control_client import ControlClient, ControlClientConfigError


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class ControlClientTests(unittest.TestCase):
    def test_private_network_url_requires_explicit_allowed_host(self) -> None:
        with self.assertRaises(ControlClientConfigError):
            ControlClient("http://control-api.railway.internal:8765", "secret")

    def test_private_network_url_is_accepted_when_host_is_allowed(self) -> None:
        captured = {}

        def request_json(method, url, headers, body):
            captured.update({"method": method, "url": url, "headers": headers, "body": body})
            return {"status": "ok", "trading_mode": "training", "real_orders_allowed": False}

        client = ControlClient(
            "http://control-api.railway.internal:8765",
            "secret",
            allowed_hosts=frozenset({"control-api.railway.internal"}),
            request_json=request_json,
            now=lambda: NOW,
            nonce=lambda: "nonce-1",
        )

        payload = client.status()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(captured["url"], "http://control-api.railway.internal:8765/status")
        self.assertIn("X-Hermes-Signature", captured["headers"])

    def test_config_commands_use_signed_control_requests(self) -> None:
        calls = []

        def request_json(method, url, headers, body):
            calls.append({"method": method, "url": url, "headers": headers, "body": body})
            if method == "GET":
                return {"settings": {"HERMES_TRADING_TIMEFRAME": {"value": "1h"}}}
            return {"key": "HERMES_TRADING_TIMEFRAME", "value": "15m", "restart_required": True}

        client = ControlClient(
            "http://127.0.0.1:8765",
            "secret",
            request_json=request_json,
            now=lambda: NOW,
            nonce=lambda: f"nonce-{len(calls)}",
        )

        listed = client.config()
        updated = client.set_config("HERMES_TRADING_TIMEFRAME", "15m")

        self.assertEqual(listed["settings"]["HERMES_TRADING_TIMEFRAME"]["value"], "1h")
        self.assertEqual(updated["value"], "15m")
        self.assertEqual(calls[0]["method"], "GET")
        self.assertEqual(calls[0]["url"], "http://127.0.0.1:8765/config")
        self.assertEqual(calls[1]["method"], "POST")
        self.assertEqual(calls[1]["body"], '{"key":"HERMES_TRADING_TIMEFRAME","value":"15m"}')
        self.assertIn("X-Hermes-Signature", calls[1]["headers"])


if __name__ == "__main__":
    unittest.main()
