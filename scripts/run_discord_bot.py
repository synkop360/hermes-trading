from __future__ import annotations

import asyncio

from hermes_trading.discord_bot import run_discord_bot, resolve_discord_bot_config


def main() -> None:
    asyncio.run(run_discord_bot(resolve_discord_bot_config()))


if __name__ == "__main__":
    main()
