from __future__ import annotations

import argparse
import asyncio
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from hermes_trading.control_server import build_control_server, resolve_control_server_config
from hermes_trading.discord_bot import run_discord_bot, resolve_discord_bot_config
from hermes_trading.loop import parse_assets, run_loop
from hermes_trading.run import ensure_state_files


DISCORD_REQUIRED = (
    "HERMES_DISCORD_BOT_TOKEN",
    "HERMES_DISCORD_ALLOWED_USER_IDS",
    "HERMES_CONTROL_HMAC_SECRET",
    "HERMES_DISCORD_TOTP_SECRET",
)


@dataclass(frozen=True)
class RunningService:
    name: str
    stop: object


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    state_dir = project_root / "state"
    ensure_state_files(state_dir)
    goal = yaml.safe_load((state_dir / "goal.yaml").read_text(encoding="utf-8")) or {}

    parser = argparse.ArgumentParser(description="Run Hermes worker with optional colocated control services")
    parser.add_argument("--asset", default=goal.get("asset", "BTC/USDT"))
    parser.add_argument("--assets", default=None)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()

    assets = resolve_colocated_assets(goal, args.assets, os.environ, fallback_asset=args.asset)
    asyncio.run(run_colocated(assets, state_dir, args.interval_seconds, os.environ))


async def run_colocated(
    assets: list[str],
    state_dir: Path,
    interval_seconds: int,
    env: Mapping[str, str],
) -> None:
    control_service = start_control_api_if_configured(state_dir, env)
    worker_task = asyncio.create_task(run_loop(assets, state_dir, interval_seconds), name="hermes-worker")
    tasks = [worker_task]
    discord_task = start_discord_bot_if_configured(env)
    if discord_task is not None:
        tasks.append(discord_task)
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            error = task.exception()
            if error is not None:
                raise error
            raise RuntimeError(f"{task.get_name()} stopped unexpectedly")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if control_service is not None:
            control_service.stop.shutdown()
            control_service.stop.server_close()


def should_start_control_api(env: Mapping[str, str]) -> bool:
    return bool(env.get("HERMES_CONTROL_HMAC_SECRET", "").strip())


def discord_bot_configured(env: Mapping[str, str]) -> bool:
    return all(env.get(name, "").strip() for name in DISCORD_REQUIRED)


def start_control_api_if_configured(state_dir: Path, env: Mapping[str, str]) -> RunningService | None:
    if not should_start_control_api(env):
        return None
    config_env = {
        **env,
        "HERMES_STATE_DIR": env.get("HERMES_STATE_DIR", str(state_dir)),
        "HERMES_CONTROL_HOST": env.get("HERMES_CONTROL_HOST", "127.0.0.1"),
    }
    server, repository = build_control_server(resolve_control_server_config(config_env))

    def serve() -> None:
        try:
            server.serve_forever()
        finally:
            repository.close()

    thread = threading.Thread(target=serve, name="hermes-control-api", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    print(f"Hermes colocated control API listening on http://{host}:{port}", flush=True)
    return RunningService("control-api", server)


def start_discord_bot_if_configured(env: Mapping[str, str]) -> asyncio.Task[None] | None:
    if not discord_bot_configured(env):
        return None
    config_env = {
        **env,
        "HERMES_CONTROL_BASE_URL": env.get("HERMES_CONTROL_BASE_URL", "http://127.0.0.1:8765"),
    }
    print("Hermes colocated Discord bot starting", flush=True)
    return asyncio.create_task(run_discord_bot(resolve_discord_bot_config(config_env)), name="hermes-discord-bot")


def resolve_colocated_assets(
    goal: Mapping[str, object],
    args_assets: str | None,
    env: Mapping[str, str],
    *,
    fallback_asset: str = "BTC/USDT",
) -> list[str]:
    if args_assets:
        return parse_assets({"HERMES_TRADING_ASSETS": args_assets})
    if env.get("HERMES_TRADING_ASSETS"):
        return parse_assets(dict(env))
    goal_assets = goal.get("assets") or [fallback_asset]
    return parse_assets({"HERMES_TRADING_ASSETS": ",".join(str(asset) for asset in goal_assets)})


if __name__ == "__main__":
    main()
