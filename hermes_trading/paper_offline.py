"""Finite, deterministic offline paper-trading session.

This module runs the canonical training engine against a local fixture of
closed candles. No network sockets, no model calls, and no live order flow
are used. The output is explicitly synthetic evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from hermes_trading.loop import run_cycle
from hermes_trading.market_data import ClosedCandle, MarketSnapshot, timeframe_interval
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository


DEFAULT_START_EQUITY = Decimal("100")


class NetworkBlockedError(RuntimeError):
    """Raised when offline mode detects a socket creation attempt."""


_REAL_SOCKET = socket.socket


class _GuardedSocket:
    """Socket wrapper that allows local/Unix sockets but blocks IPv4/IPv6."""

    def __init__(self, family: int = socket.AF_INET, type: int = socket.SOCK_STREAM, proto: int = 0, fileno: int | None = None) -> None:
        if family in (socket.AF_INET, socket.AF_INET6):
            raise NetworkBlockedError("network sockets are blocked in offline mode")
        if fileno is None:
            self._sock = _REAL_SOCKET(family, type, proto)
        else:
            self._sock = _REAL_SOCKET(family, type, proto, fileno)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


@contextlib.contextmanager
def _block_sockets() -> Any:
    """Temporarily prevent IPv4/IPv6 socket creation in this process."""
    original = socket.socket
    socket.socket = _GuardedSocket  # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = original


def _default_fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "paper_offline_btcusdt_1h.json"


def _load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported fixture schema_version")
    if not isinstance(payload.get("cycles"), list) or len(payload["cycles"]) < 1:
        raise ValueError("fixture must contain at least one cycle")
    return payload


def _validate_root(root: Path) -> Path:
    """A paper-offline root must be new and live under a .test-tmp* tree."""
    root = Path(root).resolve()
    if root.exists() or root.is_symlink():
        raise ValueError(f"root already exists: {root}")
    if not any(part.startswith(".test-tmp") for part in root.parts):
        raise ValueError("root must be inside a .test-tmp* directory")
    return root


class OfflineMarketData:
    """Replay a deterministic fixture into MarketSnapshot objects."""

    def __init__(
        self,
        fixture: dict[str, Any],
        clock: Any,
        assets: list[str],
    ) -> None:
        self.fixture = fixture
        self.clock = clock
        self.assets = assets
        self.cycle = 0
        self._interval = timeframe_interval(fixture["timeframe"])

    async def collect(self, assets: list[str]) -> dict[str, MarketSnapshot]:
        current = self.clock()
        if self.cycle >= len(self.fixture["cycles"]):
            raise ValueError("fixture exhausted: add more cycles or reduce --cycles")
        snapshots: dict[str, MarketSnapshot] = {}
        for asset in assets:
            if asset != self.fixture["asset"]:
                raise ValueError(f"fixture only supports {self.fixture['asset']!r}, not {asset!r}")
            snapshots[asset] = self._snapshot(asset, current)
        self.cycle += 1
        return snapshots

    def _snapshot(self, asset: str, current: datetime) -> MarketSnapshot:
        cycle = self.fixture["cycles"][self.cycle]
        close_at = current
        open_at = close_at - self._interval
        candle = ClosedCandle(
            open_timestamp=open_at,
            close_timestamp=close_at,
            open=Decimal(str(cycle["open"])),
            high=Decimal(str(cycle["high"])),
            low=Decimal(str(cycle["low"])),
            close=Decimal(str(cycle["close"])),
            volume=Decimal(str(cycle.get("volume", "1"))),
        )
        base_closes = tuple(Decimal(str(value)) for value in self.fixture["base_closes"])
        current_close = Decimal(str(cycle["close"]))
        closes = base_closes + (current_close,)
        price = {
            "closes": closes,
            "last": current_close,
            "bid": current_close,
            "ask": current_close,
            "source": self.fixture["source"],
            "received_at": current.isoformat(),
        }
        return MarketSnapshot(
            asset=asset,
            timeframe=self.fixture["timeframe"],
            availability="available",
            decision_action="evaluate",
            reason="closed_candle_available",
            candle=candle,
            price=price,
        )


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ensure_state_files(account_dir: Path, fixture: dict[str, Any]) -> None:
    account_dir.mkdir(parents=True, exist_ok=True)
    fixture_strategy = fixture.get("strategy", {})
    strategy: dict[str, Any] = {
        "version": "01",
        "entry": {"indicator": "rsi", "threshold": 30, "direction": "long"},
        "exit": {"take_profit_pct": "3.0"},
        "stop_loss_pct": "2.0",
        "position_size_r": "0.5",
        "simulation": {
            "quote_notional": "5",
            "taker_fee_rate": "0.008",
            "maker_fee_rate": "0.004",
            "slippage_rate": "0.001",
            "minimum_expected_edge_pct": "0.2",
            "risk_per_trade_pct": "0.0025",
        },
    }
    strategy = _deep_merge(strategy, fixture_strategy)
    _write_yaml(account_dir / "strategy.yaml", strategy)
    goal = {
        "asset": fixture["asset"],
        "assets": [fixture["asset"]],
        "target_return_30d": 0.05,
        "max_drawdown": 0.08,
        "min_sharpe": 1.0,
        "failure_below": -0.04,
        "reflection_every": 5,
        "one_variable_only": True,
    }
    _write_yaml(account_dir / "goal.yaml", goal)
    for name in ("trades.jsonl", "decisions.jsonl", "hypotheses.jsonl", "orders.jsonl"):
        (account_dir / name).touch(exist_ok=True)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _write_reflection_owner(account_dir: Path, session_id: str) -> None:
    """Mark the offline session as owned by the deterministic worker.

    This lets ``reflect --fallback`` consume the session's closed trades
    without converting the account to an external-LLM experiment.
    """
    _write_json(
        account_dir / "reflection_owner.json",
        {
            "schema_version": 1,
            "owner": "deterministic_worker",
            "experiment_id": session_id,
            "cadence_closed_trades": 5,
            "one_variable_only": True,
        },
    )


def _activity_record(result: Any) -> dict[str, Any] | None:
    decision = result.decision
    if decision is None:
        return None
    record: dict[str, Any] = {
        "asset": decision.asset,
        "action": decision.action,
        "reason": decision.reason,
        "reference_price": str(decision.reference_price),
        "decided_at": decision.decided_at,
    }
    if result.orders:
        order = result.orders[0]
        record["quantity"] = str(order.filled_quantity)
        record["fill_price"] = str(order.average_fill_price) if order.average_fill_price is not None else None
        record["fees"] = str(order.fees)
    if result.closed_positions:
        closed = result.closed_positions[0]
        record["realized_pnl"] = str(closed.realized_pnl)
    return record


def _build_result(
    root: Path,
    account_dir: Path,
    session_id: str,
    cycles_completed: int,
    simulated_periods: list[str],
    activity: list[dict[str, Any]],
    state: StateRepository,
) -> dict[str, Any]:
    closed = state.closed_positions()
    active = state.active_positions()
    start_equity = DEFAULT_START_EQUITY
    realized_pnl = sum((position.realized_pnl for position in closed), Decimal("0"))
    fees = sum((position.fees for position in closed), Decimal("0"))
    final_balance = start_equity + realized_pnl
    wins = sum(1 for position in closed if position.realized_pnl > 0)
    losses = sum(1 for position in closed if position.realized_pnl < 0)
    return {
        "mode": "paper",
        "execution": "simulated",
        "origin": "synthetic_offline_fixture",
        "session_id": session_id,
        "cycles_completed": cycles_completed,
        "assets": [state.export_canonical_tables()["decisions"][0]["asset"] if state.export_canonical_tables()["decisions"] else None],
        "simulated_periods": simulated_periods,
        "paths": {
            "root": str(root),
            "account_dir": str(account_dir),
            "trading_db": str(account_dir / "trading.db"),
            "decisions_jsonl": str(account_dir / "decisions.jsonl"),
            "trades_jsonl": str(account_dir / "trades.jsonl"),
            "result_json": str(account_dir / "result.json"),
            "reflection_owner": str(account_dir / "reflection_owner.json"),
        },
        "financials": {
            "start_equity": str(start_equity),
            "final_balance": str(final_balance),
            "realized_pnl": str(realized_pnl),
            "fees": str(fees),
            "trade_count": len(closed),
            "win_count": wins,
            "loss_count": losses,
            "open_position_count": len(active),
        },
        "activity": activity,
        "costs": {
            "taker_fee_rate": "0.008",
            "maker_fee_rate": "0.004",
            "slippage_rate": "0.001",
            "data_source": "synthetic_offline_fixture",
            "ai_cost": "0",
            "data_cost": "0",
        },
        "synthetic_origin": {
            "fixture": str(_default_fixture_path().relative_to(Path(__file__).resolve().parents[1])),
            "provider": "local_fixture",
            "model": "no_calls",
            "note": "No network, no model, and no real orders were used.",
        },
    }


async def run_paper_offline(
    root: Path,
    cycles: int,
    interval_seconds: int,
    fixture_path: Path,
) -> dict[str, Any]:
    root = _validate_root(root)
    fixture = _load_fixture(fixture_path)
    if cycles < 1 or cycles > len(fixture["cycles"]):
        raise ValueError(f"--cycles must be between 1 and {len(fixture['cycles'])}")

    account_dir = root / "account"
    session_id = f"paper-offline-{uuid.uuid4()}"
    _ensure_state_files(account_dir, fixture)
    _write_reflection_owner(account_dir, session_id)

    state = StateRepository.open(account_dir / "trading.db")
    state.migrate()
    projector = ArtifactProjector(account_dir)

    start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    tick = [start_time]

    def clock() -> datetime:
        return tick[0]

    asset = fixture["asset"]
    assets = [asset]
    strategy_file = account_dir / "strategy.yaml"
    strategy = yaml.safe_load(strategy_file.read_text(encoding="utf-8")) or {}
    market_data = OfflineMarketData(fixture, clock, assets)
    activity: list[dict[str, Any]] = []
    simulated_periods: list[str] = []

    try:
        for cycle in range(cycles):
            cycle_result = await run_cycle(
                assets,
                state,
                projector,
                market_data,
                strategy,
                "training",
                equity=DEFAULT_START_EQUITY,
            )
            simulated_periods.append(clock().isoformat())
            for result in cycle_result.results:
                record = _activity_record(result)
                if record is not None:
                    activity.append({"cycle": cycle + 1, **record})
            tick[0] += timedelta(hours=1)
            if interval_seconds > 0:
                await asyncio.sleep(interval_seconds)

        projector.reconcile(state)
        result = _build_result(
            root,
            account_dir,
            session_id,
            cycles,
            simulated_periods,
            activity,
            state,
        )
    finally:
        state.close()

    result_path = account_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="new session root under a .test-tmp* directory")
    parser.add_argument("--cycles", type=int, default=6, help="number of cycles to run (1-6)")
    parser.add_argument("--interval-seconds", type=int, default=0, help="real seconds between cycles (0 for instant)")
    parser.add_argument("--fixture", type=Path, default=_default_fixture_path(), help="path to fixture JSON")
    args = parser.parse_args()

    try:
        with _block_sockets():
            result = asyncio.run(
                run_paper_offline(
                    Path(args.root),
                    args.cycles,
                    args.interval_seconds,
                    args.fixture,
                )
            )
    except (ValueError, NetworkBlockedError) as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
