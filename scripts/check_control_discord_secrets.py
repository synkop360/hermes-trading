from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from urllib.parse import urlparse


CONTROL_REQUIRED = ("HERMES_CONTROL_HMAC_SECRET",)
DISCORD_REQUIRED = (
    "HERMES_DISCORD_BOT_TOKEN",
    "HERMES_DISCORD_ALLOWED_USER_IDS",
    "HERMES_CONTROL_HMAC_SECRET",
    "HERMES_DISCORD_TOTP_SECRET",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Hermes control and Discord secret readiness")
    parser.add_argument("service", choices=("control-api", "discord-bot", "all"))
    args = parser.parse_args()

    result = check_readiness(args.service, os.environ)
    for line in result:
        print(line)
    if any(line.startswith("missing ") or line.startswith("invalid ") for line in result):
        raise SystemExit(1)


def check_readiness(service: str, env: Mapping[str, str]) -> list[str]:
    lines: list[str] = []
    if service in {"control-api", "all"}:
        lines.extend(_required_status("control-api", CONTROL_REQUIRED, env))
    if service in {"discord-bot", "all"}:
        lines.extend(_required_status("discord-bot", DISCORD_REQUIRED, env))
        lines.extend(_discord_specific_status(env))
    return lines


def _required_status(service: str, names: tuple[str, ...], env: Mapping[str, str]) -> list[str]:
    missing = [name for name in names if not env.get(name, "").strip()]
    if missing:
        return [f"missing {service}: {', '.join(missing)}"]
    return [f"ready {service}: required secrets present"]


def _discord_specific_status(env: Mapping[str, str]) -> list[str]:
    lines: list[str] = []
    allowed_users = env.get("HERMES_DISCORD_ALLOWED_USER_IDS", "")
    if allowed_users.strip():
        try:
            user_ids = [int(part.strip()) for part in allowed_users.replace(";", ",").split(",") if part.strip()]
        except ValueError:
            lines.append("invalid discord-bot: HERMES_DISCORD_ALLOWED_USER_IDS must be numeric")
        else:
            if not user_ids:
                lines.append("invalid discord-bot: HERMES_DISCORD_ALLOWED_USER_IDS must not be empty")
            else:
                lines.append(f"ready discord-bot: {len(user_ids)} allowed user id(s) configured")

    base_url = env.get("HERMES_CONTROL_BASE_URL", "http://127.0.0.1:8765").strip()
    parsed = urlparse(base_url)
    allowed_hosts = _allowed_hosts(env.get("HERMES_CONTROL_ALLOWED_HOSTS", ""))
    if parsed.scheme != "http":
        lines.append("invalid discord-bot: HERMES_CONTROL_BASE_URL must be HTTP")
    elif parsed.hostname in {"127.0.0.1", "localhost"}:
        lines.append("ready discord-bot: control URL is loopback")
    elif parsed.hostname in allowed_hosts:
        lines.append("ready discord-bot: control URL host is explicitly allowed")
    else:
        lines.append("invalid discord-bot: HERMES_CONTROL_BASE_URL must be an HTTP loopback URL")
    return lines


def _allowed_hosts(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip())


if __name__ == "__main__":
    main()
