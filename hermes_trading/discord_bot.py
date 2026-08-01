from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from hermes_trading.control_auth import sign_command


class DiscordBotConfigError(RuntimeError):
    """Raised when the Discord bot service is missing private configuration."""


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    allowed_user_ids: frozenset[int]
    control_base_url: str
    hmac_secret: str
    totp_secret: str


RequestJson = Callable[[str, str, Mapping[str, str], str], dict[str, object]]


class DiscordControlBot:
    def __init__(
        self,
        config: DiscordBotConfig,
        *,
        request_json: RequestJson | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._request_json = request_json or _httpx_request_json
        self._now = now or (lambda: datetime.now(UTC))

    def status(self, *, user_id: int, otp: str) -> str:
        denial = self._deny(user_id, otp)
        if denial is not None:
            return denial
        payload = self._signed_request("GET", "/status", "")
        return (
            f"Hermes status={payload.get('status')} "
            f"mode={payload.get('trading_mode')} "
            f"real_orders_allowed={_bool_text(payload.get('real_orders_allowed'))}"
        )

    def qualification(self, *, user_id: int, otp: str) -> str:
        denial = self._deny(user_id, otp)
        if denial is not None:
            return denial
        body = json.dumps({"phase": "validation_ready"}, separators=(",", ":"), sort_keys=True)
        payload = self._signed_request("POST", "/qualification", body)
        return (
            f"Hermes qualification phase={payload.get('requested_phase')} "
            f"promotable={_bool_text(payload.get('promotable'))} "
            f"promoted={_bool_text(payload.get('promoted'))}"
        )

    def _deny(self, user_id: int, otp: str) -> str | None:
        if user_id not in self._config.allowed_user_ids:
            return "unauthorized"
        if not verify_totp(self._config.totp_secret, otp, self._now()):
            return "invalid_totp"
        return None

    def _signed_request(self, method: str, path: str, body: str) -> dict[str, object]:
        current = self._now().astimezone(UTC)
        nonce = f"discord-{uuid4()}"
        headers = {
            "Content-Type": "application/json",
            "X-Hermes-Nonce": nonce,
            "X-Hermes-Timestamp": current.isoformat(),
            "X-Hermes-Signature": sign_command(self._config.hmac_secret, nonce, current, body),
        }
        return self._request_json(
            method,
            self._config.control_base_url.rstrip("/") + path,
            headers,
            body,
        )


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
        await interaction.response.send_message(
            bot.status(user_id=interaction.user.id, otp=otp),
            ephemeral=True,
        )

    @tree.command(name="qualification", description="Request a validation qualification report")
    async def qualification(interaction: discord.Interaction, otp: str) -> None:
        await interaction.response.send_message(
            bot.qualification(user_id=interaction.user.id, otp=otp),
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
    _validate_loopback_url(control_base_url)
    return DiscordBotConfig(token, allowed_user_ids, control_base_url, hmac_secret, totp_secret)


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


def _httpx_request_json(method: str, url: str, headers: Mapping[str, str], body: str) -> dict[str, object]:
    response = httpx.request(method, url, headers=dict(headers), content=body.encode("utf-8"), timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("control API returned a non-object payload")
    return payload


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


def _validate_loopback_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise DiscordBotConfigError("HERMES_CONTROL_BASE_URL must be an HTTP loopback URL")


def _base32_padded(value: str) -> str:
    stripped = value.replace(" ", "").upper()
    return stripped + ("=" * ((8 - len(stripped) % 8) % 8))


def _bool_text(value: object) -> str:
    return "true" if value is True else "false" if value is False else str(value)
