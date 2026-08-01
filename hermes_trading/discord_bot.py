from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import os
from typing import Protocol

from hermes_trading.control_client import (
    ControlClient,
    ControlClientConfigError,
    control_status_message,
    qualification_message,
)


class DiscordBotConfigError(RuntimeError):
    """Raised when the Discord bot service is missing private configuration."""


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    allowed_user_ids: frozenset[int]
    control_base_url: str
    hmac_secret: str
    totp_secret: str
    control_allowed_hosts: frozenset[str]


class ControlClientLike(Protocol):
    def status(self) -> dict[str, object]:
        ...

    def qualification(self, phase: str = "validation_ready") -> dict[str, object]:
        ...


class DiscordControlBot:
    def __init__(
        self,
        config: DiscordBotConfig,
        *,
        control_client: ControlClientLike | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._now = now or (lambda: datetime.now(UTC))
        self._control_client = control_client or ControlClient(
            config.control_base_url,
            config.hmac_secret,
            now=self._now,
            allowed_hosts=config.control_allowed_hosts,
        )

    def status(self, *, user_id: int, otp: str) -> str:
        denial = self._deny(user_id, otp)
        if denial is not None:
            return denial
        return f"Hermes {control_status_message(self._control_client.status())}"

    def qualification(self, *, user_id: int, otp: str) -> str:
        denial = self._deny(user_id, otp)
        if denial is not None:
            return denial
        return f"Hermes {qualification_message(self._control_client.qualification('validation_ready'))}"

    def _deny(self, user_id: int, otp: str) -> str | None:
        if user_id not in self._config.allowed_user_ids:
            return "unauthorized"
        if not verify_totp(self._config.totp_secret, otp, self._now()):
            return "invalid_totp"
        return None


async def run_discord_bot(config: DiscordBotConfig) -> None:
    try:
        import discord
        from discord import app_commands
    except ImportError as error:
        raise DiscordBotConfigError("discord.py is required to run the Discord bot service") from error

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    bot = DiscordControlBot(config)

    @client.event
    async def on_ready() -> None:
        await tree.sync()
        print(f"Hermes Discord bot logged in as {client.user}", flush=True)

    @tree.command(name="status", description="Read Hermes status")
    async def status(interaction: discord.Interaction, otp: str) -> None:
        await interaction.response.defer(ephemeral=True)
        message = await asyncio.to_thread(bot.status, user_id=interaction.user.id, otp=otp)
        await interaction.followup.send(
            message,
            ephemeral=True,
        )

    @tree.command(name="qualification", description="Request a validation qualification report")
    async def qualification(interaction: discord.Interaction, otp: str) -> None:
        await interaction.response.defer(ephemeral=True)
        message = await asyncio.to_thread(bot.qualification, user_id=interaction.user.id, otp=otp)
        await interaction.followup.send(
            message,
            ephemeral=True,
        )

    await client.start(config.token)


def resolve_discord_bot_config(env: Mapping[str, str] | None = None) -> DiscordBotConfig:
    values = env if env is not None else os.environ
    token = _required(values, "HERMES_DISCORD_BOT_TOKEN")
    allowed_user_ids = _allowed_user_ids(_required(values, "HERMES_DISCORD_ALLOWED_USER_IDS"))
    hmac_secret = _required(values, "HERMES_CONTROL_HMAC_SECRET")
    totp_secret = _required(values, "HERMES_DISCORD_TOTP_SECRET")
    control_base_url = values.get("HERMES_CONTROL_BASE_URL", "http://127.0.0.1:8765").strip()
    allowed_hosts = _allowed_control_hosts(values.get("HERMES_CONTROL_ALLOWED_HOSTS", ""))
    try:
        ControlClient(control_base_url, hmac_secret, allowed_hosts=allowed_hosts)
    except ControlClientConfigError as error:
        raise DiscordBotConfigError(
            "HERMES_CONTROL_BASE_URL must be an HTTP loopback URL or an explicitly allowed host"
        ) from error
    return DiscordBotConfig(token, allowed_user_ids, control_base_url, hmac_secret, totp_secret, allowed_hosts)


def totp_code(secret: str, at: datetime | None = None, *, step_seconds: int = 30, digits: int = 6) -> str:
    current = (at or datetime.now(UTC)).astimezone(UTC)
    counter = int(current.timestamp()) // step_seconds
    key = base64.b32decode(_base32_padded(secret), casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, at: datetime | None = None) -> bool:
    normalized = code.strip()
    current = (at or datetime.now(UTC)).astimezone(UTC)
    return any(
        hmac.compare_digest(totp_code(secret, current, step_seconds=30), normalized)
        for current in (
            current,
            datetime.fromtimestamp(current.timestamp() - 30, tz=UTC),
            datetime.fromtimestamp(current.timestamp() + 30, tz=UTC),
        )
    )


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise DiscordBotConfigError(f"{name} is required")
    return value


def _allowed_user_ids(raw: str) -> frozenset[int]:
    try:
        user_ids = frozenset(int(part.strip()) for part in raw.replace(";", ",").split(",") if part.strip())
    except ValueError as error:
        raise DiscordBotConfigError("HERMES_DISCORD_ALLOWED_USER_IDS must contain numeric Discord user IDs") from error
    if not user_ids:
        raise DiscordBotConfigError("HERMES_DISCORD_ALLOWED_USER_IDS must contain at least one user ID")
    return user_ids


def _allowed_control_hosts(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip())


def _base32_padded(value: str) -> str:
    stripped = value.replace(" ", "").upper()
    return stripped + ("=" * ((8 - len(stripped) % 8) % 8))
