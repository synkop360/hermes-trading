import json
import unittest
from datetime import UTC, datetime

from hermes_trading.control_client import ControlClient, control_status_message


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class ControlClientTests(unittest.TestCase):
    def test_status_signs_get_request_to_local_api(self) -> None:
        captured = {}

        def request_json(method, url, headers, body):
            captured.update({"method": method, "url": url, "headers": headers, "body": body})
            return {"status": "ok", "trading_mode": "training", "real_orders_allowed": False}

        client = ControlClient(
            "http://127.0.0.1:8765",
            "secret",
            request_json=request_json,
            now=lambda: NOW,
            nonce=lambda: "nonce-1",
        )

        payload = client.status()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/status")
        self.assertEqual(captured["body"], "")
        self.assertEqual(captured["headers"]["X-Hermes-Nonce"], "nonce-1")
        self.assertIn("X-Hermes-Signature", captured["headers"])

    def test_qualification_signs_validation_request(self) -> None:
        captured = {}

        def request_json(method, url, headers, body):
            captured.update({"method": method, "url": url, "headers": headers, "body": body})
            return {"requested_phase": "validation_ready", "promoted": False}

        client = ControlClient(
            "http://127.0.0.1:8765",
            "secret",
            request_json=request_json,
            now=lambda: NOW,
            nonce=lambda: "nonce-1",
        )

        payload = client.qualification("validation_ready")

        self.assertFalse(payload["promoted"])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/qualification")
        self.assertEqual(json.loads(captured["body"]), {"phase": "validation_ready"})

    def test_status_message_keeps_real_order_state_visible(self) -> None:
        message = control_status_message(
            {"status": "ok", "trading_mode": "training", "real_orders_allowed": False}
        )

        self.assertEqual(message, "status=ok mode=training real_orders_allowed=false")
