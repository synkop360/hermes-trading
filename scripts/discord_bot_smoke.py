from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

import httpx


DISCORD_API_BASE = "https://discord.com/api/v10"
REQUIRED_COMMANDS = frozenset({"status", "qualification"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test Hermes Discord bot connectivity")
    parser.add_argument("--send-dm", action="store_true")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--message", default="Hermes Discord smoke test: bot can send messages.")
    args = parser.parse_args()

    for line in smoke_discord_bot(
        os.environ,
        send_dm=args.send_dm,
        user_id=args.user_id or None,
        message=args.message,
    ):
        print(line)


def smoke_discord_bot(
    env: Mapping[str, str],
    *,
    request_json: Callable[..., object] | None = None,
    send_dm: bool = False,
    user_id: str | None = None,
    message: str = "Hermes Discord smoke test: bot can send messages.",
) -> list[str]:
    token = _required(env, "HERMES_DISCORD_BOT_TOKEN")
    allowed_user = user_id or _first_allowed_user(_required(env, "HERMES_DISCORD_ALLOWED_USER_IDS"))
    request = request_json or _request_json

    bot_user = _object(request("GET", f"{DISCORD_API_BASE}/users/@me", token=token))
    application = _object(request("GET", f"{DISCORD_API_BASE}/oauth2/applications/@me", token=token))
    app_id = str(application["id"])
    command_payload = request("GET", f"{DISCORD_API_BASE}/applications/{app_id}/commands", token=token)
    commands = _command_names(command_payload)
    missing = REQUIRED_COMMANDS - commands
    if missing:
        raise SystemExit(f"missing_commands={','.join(sorted(missing))}")

    lines = [
        f"authenticated bot_id={bot_user['id']} app_id={app_id}",
        f"commands={','.join(sorted(REQUIRED_COMMANDS))}",
    ]
    if not send_dm:
        lines.append("dm=skipped")
        return lines

    dm = _object(
        request(
            "POST",
            f"{DISCORD_API_BASE}/users/@me/channels",
            token=token,
            payload={"recipient_id": allowed_user},
        )
    )
    sent = _object(
        request(
            "POST",
            f"{DISCORD_API_BASE}/channels/{dm['id']}/messages",
            token=token,
            payload={"content": message},
        )
    )
    lines.append(f"dm=sent user_id={allowed_user} message_id={sent['id']}")
    return lines


def _request_json(method: str, url: str, *, token: str, payload: object | None = None) -> object:
    with httpx.Client(timeout=20) as client:
        response = client.request(
            method,
            url,
            headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _first_allowed_user(raw: str) -> str:
    for part in raw.replace(";", ",").split(","):
        user_id = part.strip()
        if user_id:
            return user_id
    raise SystemExit("HERMES_DISCORD_ALLOWED_USER_IDS must contain at least one user id")


def _object(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit("discord response was not an object")
    return payload


def _command_names(payload: object) -> frozenset[str]:
    if not isinstance(payload, list):
        raise SystemExit("discord command response was not a list")
    return frozenset(str(item.get("name", "")) for item in payload if isinstance(item, dict))


if __name__ == "__main__":
    main()
