import json
import unittest
from datetime import UTC, datetime

from hermes_trading.discord_bot import (
    DiscordBotConfigError,
    DiscordControlBot,
    resolve_discord_bot_config,
    totp_code,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


class DiscordBotConfigTests(unittest.TestCase):
    def test_config_requires_token_allowed_users_hmac_secret_and_totp_secret(self) -> None:
        with self.assertRaises(DiscordBotConfigError):
            resolve_discord_bot_config({})

    def test_config_accepts_only_loopback_control_url(self) -> None:
        config = resolve_discord_bot_config(
            {
                "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                "HERMES_DISCORD_ALLOWED_USER_IDS": "123, 456",
                "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                "HERMES_CONTROL_BASE_URL": "http://127.0.0.1:8765",
            }
        )

        self.assertEqual(config.allowed_user_ids, frozenset({123, 456}))

        with self.assertRaises(DiscordBotConfigError):
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                    "HERMES_CONTROL_BASE_URL": "https://example.com",
                }
            )


class DiscordControlBotTests(unittest.TestCase):
    def test_status_rejects_unauthorized_user_and_bad_totp(self) -> None:
        calls = []
        bot = DiscordControlBot(
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                }
            ),
            request_json=lambda *args: calls.append(args) or {"status": "ok"},
            now=lambda: NOW,
        )

        self.assertEqual(bot.status(user_id=999, otp=totp_code(TOTP_SECRET, NOW)), "unauthorized")
        self.assertEqual(bot.status(user_id=123, otp="000000"), "invalid_totp")
        self.assertEqual(calls, [])

    def test_status_signs_local_control_api_request(self) -> None:
        captured = {}

        def request_json(method, url, headers, body):
            captured.update({"method": method, "url": url, "headers": headers, "body": body})
            return {"status": "ok", "real_orders_allowed": False}

        bot = DiscordControlBot(
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                }
            ),
            request_json=request_json,
            now=lambda: NOW,
        )

        message = bot.status(user_id=123, otp=totp_code(TOTP_SECRET, NOW))

        self.assertIn("status=ok", message)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/status")
        self.assertEqual(captured["body"], "")
        self.assertIn("X-Hermes-Signature", captured["headers"])

    def test_qualification_signs_validation_request_without_promoting(self) -> None:
        captured = {}

        def request_json(method, url, headers, body):
            captured.update({"method": method, "url": url, "headers": headers, "body": body})
            return {"requested_phase": "validation_ready", "promoted": False, "promotable": False}

        bot = DiscordControlBot(
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                }
            ),
            request_json=request_json,
            now=lambda: NOW,
        )

        message = bot.qualification(user_id=123, otp=totp_code(TOTP_SECRET, NOW))

        self.assertIn("promoted=false", message)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/qualification")
        self.assertEqual(json.loads(captured["body"]), {"phase": "validation_ready"})
