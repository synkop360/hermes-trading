import inspect
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


class RecordingControlClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def status(self) -> dict[str, object]:
        self.calls.append(("status", None))
        return {"status": "ok", "trading_mode": "training", "real_orders_allowed": False}

    def qualification(self, phase: str = "validation_ready") -> dict[str, object]:
        self.calls.append(("qualification", phase))
        return {
            "requested_phase": phase,
            "promotable": False,
            "promoted": False,
            "run_id": "qualification-run",
        }


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
        control_client = RecordingControlClient()
        bot = DiscordControlBot(
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                }
            ),
            control_client=control_client,
            now=lambda: NOW,
        )

        self.assertEqual(bot.status(user_id=999, otp=totp_code(TOTP_SECRET, NOW)), "unauthorized")
        self.assertEqual(bot.status(user_id=123, otp="000000"), "invalid_totp")
        self.assertEqual(control_client.calls, [])

    def test_status_uses_injected_control_client_after_authorization(self) -> None:
        control_client = RecordingControlClient()
        bot = DiscordControlBot(
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                }
            ),
            control_client=control_client,
            now=lambda: NOW,
        )

        message = bot.status(user_id=123, otp=totp_code(TOTP_SECRET, NOW))

        self.assertIn("status=ok", message)
        self.assertEqual(control_client.calls, [("status", None)])

    def test_qualification_uses_control_client_without_promoting(self) -> None:
        control_client = RecordingControlClient()
        bot = DiscordControlBot(
            resolve_discord_bot_config(
                {
                    "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                    "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                    "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                    "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
                }
            ),
            control_client=control_client,
            now=lambda: NOW,
        )

        message = bot.qualification(user_id=123, otp=totp_code(TOTP_SECRET, NOW))

        self.assertIn("promoted=false", message)
        self.assertIn("run_id=qualification-run", message)
        self.assertEqual(control_client.calls, [("qualification", "validation_ready")])

    def test_discord_handlers_offload_blocking_bot_calls(self) -> None:
        source = inspect.getsource(__import__("hermes_trading.discord_bot").discord_bot.run_discord_bot)

        self.assertIn("await asyncio.to_thread(bot.status", source)
        self.assertIn("await asyncio.to_thread(bot.qualification", source)
