from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import yaml

from hermes_trading.loop import parse_assets, run_loop
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository


DEFAULT_GOAL = {
    "asset": "BTC/USDT",
    "assets": ["BTC/USDT", "ETH/USDT", "XRP/USDT"],
    "target_return_30d": 0.05,
    "max_drawdown": 0.08,
    "min_sharpe": 1.0,
    "failure_below": -0.04,
    "reflection_every": 5,
    "one_variable_only": True,
}

DEFAULT_STRATEGY = {
    "version": "01",
    "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
    "stop_loss_pct": 2.0,
    "position_size_r": 0.5,
}


def resolve_state_dir(project_root: Path, env: dict[str, str] | None = None) -> Path:
    """Defaults to <project_root>/state, matching Railway; HERMES_STATE_DIR
    overrides it so a second local instance (a separate wallet/state
    entirely) can run alongside the default one without colliding."""
    env = os.environ if env is None else env
    override = env.get("HERMES_STATE_DIR", "").strip()
    return Path(override) if override else project_root / "state"


def ensure_state_files(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "history").mkdir(exist_ok=True)
    goal_path = state_dir / "goal.yaml"
    strategy_path = state_dir / "strategy.yaml"
    if not goal_path.exists():
        goal_path.write_text(yaml.safe_dump(DEFAULT_GOAL, sort_keys=False))
    if not strategy_path.exists():
        strategy_path.write_text(yaml.safe_dump(DEFAULT_STRATEGY, sort_keys=False))
    for name in ["trades.jsonl", "decisions.jsonl", "hypotheses.jsonl", "orders.jsonl"]:
        (state_dir / name).touch(exist_ok=True)
    heartbeat_path = state_dir / "heartbeat.json"
    if not heartbeat_path.exists():
        heartbeat_path.write_text('{"status":"not_started"}\n')
    funds_alert_path = state_dir / "funds_alert.json"
    if not funds_alert_path.exists():
        funds_alert_path.write_text('{"status":"ok"}\n')
    repository = StateRepository.open(state_dir / "trading.db")
    try:
        repository.migrate()
        ArtifactProjector(state_dir).rebuild_missing(repository)
    finally:
        repository.close()


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    state_dir = resolve_state_dir(project_root)
    ensure_state_files(state_dir)
    goal = yaml.safe_load((state_dir / "goal.yaml").read_text()) or {}

    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default=goal.get("asset", "BTC/USDT"))
    parser.add_argument("--assets", default=None)
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()

    if args.assets:
        assets = parse_assets({"HERMES_TRADING_ASSETS": args.assets})
    elif os.getenv("HERMES_TRADING_ASSETS"):
        assets = parse_assets()
    else:
        goal_assets = goal.get("assets") or [args.asset]
        assets = parse_assets({"HERMES_TRADING_ASSETS": ",".join(goal_assets)})
    asyncio.run(run_loop(assets, state_dir, args.interval_seconds))


if __name__ == "__main__":
    main()
