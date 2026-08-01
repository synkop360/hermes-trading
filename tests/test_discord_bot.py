import asyncio
import sys
from types import SimpleNamespace
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import hermes_trading.discord_bot as discord_bot_module
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


class FakeDiscordClient:
    def __init__(self, *, intents: object) -> None:
        self.intents = intents
        self.user = "fake-discord-user"

    def event(self, function):
        return function

    async def start(self, token: str) -> None:
        self.token = token


class FakeCommandTree:
    last: "FakeCommandTree | None" = None

    def __init__(self, client: FakeDiscordClient) -> None:
        self.client = client
        self.commands = {}
        FakeCommandTree.last = self

    def command(self, *, name: str, description: str):
        def decorate(function):
            self.commands[name] = function
            return function

        return decorate

    async def sync(self) -> None:
        return None


class FakeInteraction:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self.user = SimpleNamespace(id=123)
        self.response = SimpleNamespace(defer=self._defer, send_message=self._send_initial)
        self.followup = SimpleNamespace(send=self._send)
        self._events = events

    async def _defer(self, *, ephemeral: bool) -> None:
        self._events.append(("defer", ephemeral))

    async def _send_initial(self, message: str, *, ephemeral: bool) -> None:
        self._events.append(("initial_response", message, ephemeral))

    async def _send(self, message: str, *, ephemeral: bool) -> None:
        self._events.append(("followup", message, ephemeral))


class FakeDiscordControlBot:
    def __init__(self, config) -> None:
        self.config = config

    def status(self, *, user_id: int, otp: str) -> str:
        return f"status for {user_id} with {otp}"

    def qualification(self, *, user_id: int, otp: str) -> str:
        return f"qualification for {user_id} with {otp}"


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

    def test_discord_handlers_defer_before_offloading_blocking_bot_calls(self) -> None:
        config = resolve_discord_bot_config(
            {
                "HERMES_DISCORD_BOT_TOKEN": "bot-token",
                "HERMES_DISCORD_ALLOWED_USER_IDS": "123",
                "HERMES_CONTROL_HMAC_SECRET": "control-secret",
                "HERMES_DISCORD_TOTP_SECRET": TOTP_SECRET,
            }
        )
        fake_app_commands = SimpleNamespace(CommandTree=FakeCommandTree)
        fake_discord = SimpleNamespace(
            Intents=SimpleNamespace(default=lambda: object()),
            Client=FakeDiscordClient,
            Interaction=object,
            app_commands=fake_app_commands,
        )
        modules = {
            "discord": fake_discord,
            "discord.app_commands": fake_app_commands,
        }
        events: list[tuple[str, object]] = []

        async def fake_to_thread(function, *args, **kwargs):
            events.append(("to_thread", function.__name__, kwargs))
            return function(*args, **kwargs)

        with (
            patch.dict(sys.modules, modules),
            patch.object(discord_bot_module, "DiscordControlBot", FakeDiscordControlBot),
            patch.object(discord_bot_module.asyncio, "to_thread", fake_to_thread),
        ):
            asyncio.run(discord_bot_module.run_discord_bot(config))
            commands = FakeCommandTree.last.commands

            asyncio.run(commands["status"](FakeInteraction(events), "111111"))
            asyncio.run(commands["qualification"](FakeInteraction(events), "222222"))

        self.assertEqual(events[0], ("defer", True))
        self.assertEqual(events[1][0:2], ("to_thread", "status"))
        self.assertEqual(events[2], ("followup", "status for 123 with 111111", True))
        self.assertEqual(events[3], ("defer", True))
        self.assertEqual(events[4][0:2], ("to_thread", "qualification"))
        self.assertEqual(events[5], ("followup", "qualification for 123 with 222222", True))
