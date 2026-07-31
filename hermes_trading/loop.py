from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiofiles
import yaml

from hermes_trading.adapters import SchemaError
from hermes_trading.adapters import macro, news, onchain, price
from hermes_trading.adapters.execution import execute_signal, resolve_execution_settings
from hermes_trading.config import LIVE_CONFIRM_PHRASE
from hermes_trading.domain import Position, json_value
from hermes_trading.funds import build_due_funds_alert, resolve_funds_alert_settings
from hermes_trading.market_data import MarketDataCoordinator
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.positions import PositionEngine, PositionProcessResult
from hermes_trading.notifications import (
    PnlSummaryCounter,
    build_cycle_notifications,
    build_error_notification,
    build_summary_notifications,
    NotificationSettings,
    resolve_notification_settings,
    send_notification,
)


class TradingModeError(RuntimeError):
    """Raised when the requested trading mode is unsafe or unsupported."""


@dataclass(frozen=True)
class TradingConfig:
    mode: str
    execution: str
    accepts_real_orders: bool


@dataclass(frozen=True)
class CycleResult:
    results: tuple[PositionProcessResult, ...]
    mode: str

    @property
    def decisions(self) -> tuple[object, ...]:
        return tuple(result.decision for result in self.results if result.decision is not None)

    @property
    def orders(self) -> tuple[object, ...]:
        return tuple(order for result in self.results for order in result.orders)

    @property
    def closed_positions(self) -> tuple[Position, ...]:
        return tuple(position for result in self.results for position in result.closed_positions)

    @property
    def processed_count(self) -> int:
        return sum(result.processed for result in self.results)

    @property
    def degraded(self) -> bool:
        return any(result.projection_error is not None for result in self.results)


def resolve_trading_config(env: dict[str, str] | None = None) -> TradingConfig:
    values = env if env is not None else os.environ
    mode = values.get("HERMES_TRADING_MODE", "paper").strip().lower()
    if mode in {"paper", "training", "train", "demo"}:
        normalized = "training" if mode in {"training", "train", "demo"} else "paper"
        return TradingConfig(mode=normalized, execution="simulated", accepts_real_orders=False)
    if mode != "live":
        raise TradingModeError(f"unsupported HERMES_TRADING_MODE={mode!r}")

    accepts_risk = values.get("HERMES_TRADING_I_ACCEPT_RISK", "").strip().lower() == "true"
    confirmation = values.get("HERMES_TRADING_LIVE_CONFIRM", "").strip()
    if not accepts_risk:
        raise TradingModeError("live mode blocked: HERMES_TRADING_I_ACCEPT_RISK must be true")
    if confirmation != LIVE_CONFIRM_PHRASE:
        raise TradingModeError(
            "live mode blocked: HERMES_TRADING_LIVE_CONFIRM must exactly match the live confirmation phrase"
        )
    return TradingConfig(mode="live", execution="real", accepts_real_orders=True)


def parse_assets(env: dict[str, str] | None = None) -> list[str]:
    values = env if env is not None else os.environ
    raw = values.get("HERMES_TRADING_ASSETS") or values.get("HERMES_TRADING_ASSET") or "BTC/USDT"
    assets = [item.strip().upper() for item in re.split(r"[\s,]+", raw) if item.strip()]
    seen = set()
    unique_assets = []
    for asset in assets:
        if asset not in seen:
            unique_assets.append(asset)
            seen.add(asset)
    return unique_assets or ["BTC/USDT"]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_asset_strategy(strategy: dict[str, Any], asset: str) -> dict[str, Any]:
    symbols = strategy.get("symbols") if isinstance(strategy.get("symbols"), dict) else {}
    base = {key: value for key, value in strategy.items() if key != "symbols"}
    override = symbols.get(asset, {}) if isinstance(symbols, dict) else {}
    if not isinstance(override, dict):
        override = {}
    return _deep_merge(base, override)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains = []
    losses = []
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


async def _retry(name: str, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    delay = 1.0
    last_error: Exception | None = None
    for _ in range(3):
        try:
            return await operation()
        except SchemaError:
            raise
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{name} adapter failed after 3 attempts: {last_error}")


async def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    async with aiofiles.open(path, "a", encoding="utf-8") as handle:
        await handle.write(
            json.dumps(payload, separators=(",", ":"), default=json_value) + "\n"
        )


async def _write_json(path: Path, payload: dict[str, Any]) -> None:
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(payload, indent=2, default=json_value))


async def _notify_all(messages: list[str], settings: NotificationSettings | None = None) -> None:
    settings = settings or resolve_notification_settings()
    if not settings.enabled:
        return
    for message in messages:
        try:
            await send_notification(settings, message)
        except Exception as exc:
            print(f"notification error: {exc}", flush=True)


def _load_yaml(path: Path) -> dict[str, Any]:
    class DecimalSafeLoader(yaml.SafeLoader):
        pass

    def decimal_value(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
        return Decimal(loader.construct_scalar(node).replace("_", ""))

    DecimalSafeLoader.add_constructor("tag:yaml.org,2002:float", decimal_value)
    return yaml.load(path.read_text(), Loader=DecimalSafeLoader) or {}


def _paper_trade(asset: str, market: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any] | None:
    strategy = resolve_asset_strategy(strategy, asset)
    price_data = market["price"]
    rsi = _rsi(price_data.get("closes", []))
    entry = strategy.get("entry", {})
    threshold = float(entry.get("threshold", 30))
    direction = entry.get("direction", "long")
    should_enter = entry.get("indicator") == "rsi" and direction == "long" and rsi <= threshold
    if not should_enter:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "decision",
            "asset": asset,
            "strategy_version": strategy.get("version", "01"),
            "side": direction,
            "entry_indicator": entry.get("indicator"),
            "entry_value": round(rsi, 4),
            "threshold": threshold,
            "trade_closed": False,
            "reason": "entry_condition_not_met",
        }

    closes = price_data.get("closes", [])
    realized = 0.0
    if len(closes) >= 2 and closes[-2]:
        realized = (float(closes[-1]) - float(closes[-2])) / float(closes[-2])
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "trade",
        "asset": asset,
        "strategy_version": strategy.get("version", "01"),
        "side": direction,
        "entry_indicator": "rsi",
        "entry_value": round(rsi, 4),
        "entry_price": price_data["last"],
        "realized_pnl_pct": round(realized, 6),
        "equity": round(100.0 * (1.0 + realized), 4),
        "trade_closed": True,
        "mode": "paper",
        "signal": {
            "action": "enter",
            "order_type": "market",
            "side": "buy",
            "asset": asset,
            "reference_price": price_data["last"],
            "reason": "rsi_entry",
        },
    }


async def _evaluate_asset(asset: str, strategy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    market = {
        "price": await _retry("price", lambda: price.fetch(asset)),
        "onchain": await _retry("onchain", onchain.fetch),
        "news": await _retry("news", news.fetch),
        "macro": await _retry("macro", macro.fetch),
    }
    return market, _paper_trade(asset, market, strategy)


async def run_cycle(
    assets: list[str],
    state: StateRepository,
    projector: ArtifactProjector,
    market_data: MarketDataCoordinator,
    strategy: dict[str, Any],
    mode: str,
) -> CycleResult:
    """Process one closed-candle batch through the canonical training engine."""
    if mode != "training":
        raise TradingModeError("run_cycle is available only in training mode")
    engine = PositionEngine(state, projector)
    recovery_error = engine.recover_pending_projections()
    snapshots = await market_data.collect(assets)
    results = tuple(
        engine.process_candle(
            snapshots[asset],
            resolve_asset_strategy(strategy, asset),
            recovery_error=recovery_error,
            recover_pending=False,
        )
        for asset in assets
    )
    return CycleResult(results=results, mode=mode)


def _cycle_trades(cycle: CycleResult) -> list[dict[str, Any]]:
    trades = []
    for position in cycle.closed_positions:
        entry_cost = position.quantity * position.entry_price
        realized_pct = position.realized_pnl / entry_cost if entry_cost else 0
        trades.append(
            {
                "timestamp": position.closed_at,
                "asset": position.asset,
                "strategy_version": position.strategy_version,
                "mode": cycle.mode,
                "realized_pnl_pct": realized_pct,
            }
        )
    return trades


def _cycle_orders(cycle: CycleResult) -> list[dict[str, Any]]:
    return [json_value(order) for order in cycle.orders]


async def _run_training_loop(
    assets: list[str], state_dir: Path, interval_seconds: int, trading: TradingConfig,
    notification_settings: NotificationSettings, funds_alert_settings: Any,
    pnl_summary: PnlSummaryCounter,
) -> None:
    state = StateRepository.open(state_dir / "trading.db")
    state.migrate()
    projector = ArtifactProjector(state_dir)
    market_data = MarketDataCoordinator(
        repository=state,
        news_fetch=lambda: _retry("news", news.fetch),
        macro_fetch=lambda: _retry("macro", macro.fetch),
        onchain_fetch=lambda: _retry("onchain", onchain.fetch),
    )
    consecutive_failures = 0
    try:
        while True:
            started_at = datetime.now(timezone.utc)
            try:
                funds_alert = await build_due_funds_alert(state_dir, funds_alert_settings, started_at)
                if funds_alert:
                    await _notify_all([funds_alert], notification_settings)
                strategy = _load_yaml(state_dir / "strategy.yaml")
                cycle = await run_cycle(assets, state, projector, market_data, strategy, trading.mode)
                trades = _cycle_trades(cycle)
                orders = _cycle_orders(cycle)
                await _write_json(
                    state_dir / "heartbeat.json",
                    {
                        "timestamp": started_at.isoformat(), "assets": assets,
                        "strategy_version": strategy.get("version", "01"), "mode": trading.mode,
                        "execution": trading.execution,
                        "status": "degraded" if cycle.degraded else "ok",
                        "decision_count": len(cycle.decisions),
                        "closed_trade_count": len(trades), "order_count": len(orders),
                        "trade_closed": bool(trades), "projection_degraded": cycle.degraded,
                    },
                )
                messages = build_cycle_notifications(trades, orders, notification_settings)
                messages.extend(build_summary_notifications(state_dir, trades, notification_settings, pnl_summary, started_at))
                await _notify_all(messages, notification_settings)
                consecutive_failures = 0
            except SchemaError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                await _write_json(state_dir / "heartbeat.json", {
                    "timestamp": started_at.isoformat(), "assets": assets, "mode": trading.mode,
                    "execution": trading.execution, "status": "error", "error": str(exc),
                    "consecutive_failures": consecutive_failures,
                })
                await _notify_all([build_error_notification(exc, consecutive_failures)], notification_settings)
                funds_alert = await build_due_funds_alert(state_dir, funds_alert_settings, started_at)
                if funds_alert:
                    await _notify_all([funds_alert], notification_settings)
                if consecutive_failures >= 5:
                    raise RuntimeError("circuit breaker opened after 5 consecutive failures") from exc
            await asyncio.sleep(interval_seconds)
    finally:
        state.close()


async def run_loop(asset: str | list[str], state_dir: Path, interval_seconds: int = 60) -> None:
    trading = resolve_trading_config()
    execution_settings = resolve_execution_settings()
    notification_settings = resolve_notification_settings()
    funds_alert_settings = resolve_funds_alert_settings()
    pnl_summary = PnlSummaryCounter(notification_settings)

    assets = [asset] if isinstance(asset, str) else asset
    print(f"Booting hermes-trading worker for {', '.join(assets)} in {trading.mode} mode", flush=True)
    if trading.mode == "training":
        await _run_training_loop(
            assets, state_dir, interval_seconds, trading, notification_settings,
            funds_alert_settings, pnl_summary,
        )
        return
    consecutive_failures = 0
    state_dir.mkdir(parents=True, exist_ok=True)

    while True:
        started_at = datetime.now(timezone.utc)
        try:
            strategy = _load_yaml(state_dir / "strategy.yaml")
            decisions = []
            trades = []
            orders = []
            for watched_asset in assets:
                _, decision = await _evaluate_asset(watched_asset, strategy)
                decision["mode"] = trading.mode
                decisions.append(decision)
                await _append_jsonl(state_dir / "decisions.jsonl", decision)
                asset_strategy = resolve_asset_strategy(
                    strategy,
                    watched_asset,
                )

                order = await execute_signal(
                    decision.get("signal"),
                    asset_strategy,
                    execution_settings,
                )
                if order:
                    orders.append(order)
                    await _append_jsonl(state_dir / "orders.jsonl", order)
                if decision["trade_closed"]:
                    trades.append(decision)
                    await _append_jsonl(state_dir / "trades.jsonl", decision)
                    print(
                        f"Closed {trading.mode} trade {decision['asset']} pnl={decision['realized_pnl_pct']}",
                        flush=True,
                    )

            await _write_json(
                state_dir / "heartbeat.json",
                {
                    "timestamp": started_at.isoformat(),
                    "assets": assets,
                    "strategy_version": strategy.get("version", "01"),
                    "mode": trading.mode,
                    "execution": trading.execution,
                    "status": "ok",
                    "decision_count": len(decisions),
                    "closed_trade_count": len(trades),
                    "order_count": len(orders),
                    "live_validate_only": execution_settings.validate_only,
                    "trade_closed": bool(trades),
                },
            )
            messages = build_cycle_notifications(trades, orders, notification_settings)
            messages.extend(
                build_summary_notifications(state_dir, trades, notification_settings, pnl_summary, started_at)
            )
            funds_alert = await build_due_funds_alert(state_dir, funds_alert_settings, started_at)
            if funds_alert:
                messages.append(funds_alert)
            await _notify_all(messages, notification_settings)
            consecutive_failures = 0
        except SchemaError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            await _write_json(
                state_dir / "heartbeat.json",
                {
                    "timestamp": started_at.isoformat(),
                    "assets": assets,
                    "mode": trading.mode,
                    "execution": trading.execution,
                    "status": "error",
                    "error": str(exc),
                    "live_validate_only": execution_settings.validate_only,
                    "consecutive_failures": consecutive_failures,
                },
            )
            await _notify_all([build_error_notification(exc, consecutive_failures)], notification_settings)
            print(f"worker error {consecutive_failures}/5: {exc}", flush=True)
            if consecutive_failures >= 5:
                raise RuntimeError("circuit breaker opened after 5 consecutive failures") from exc

        await asyncio.sleep(interval_seconds)
