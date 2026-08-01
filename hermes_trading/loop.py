from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from uuid import NAMESPACE_URL, uuid5

import aiofiles
import yaml

from hermes_trading.adapters import SchemaError
from hermes_trading.adapters import macro, news, onchain, price
from hermes_trading.adapters.execution import (
    _default_exchange as _default_execution_exchange,
    resolve_execution_settings,
)
from hermes_trading.config import LIVE_CONFIRM_PHRASE
from hermes_trading.costs import CostLedger
from hermes_trading.domain import Decision, OrderRecord, Position, json_value
from hermes_trading.exchange import ExchangeGateway, KrakenGateway
from hermes_trading.execution import ExecutionResult, ExecutionService
from hermes_trading.funds import (
    FundsAlertSettings,
    build_due_funds_alert,
    build_due_funds_alert_from_balances,
    resolve_funds_alert_settings,
)
from hermes_trading.market_data import MarketDataCoordinator
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.positions import PositionEngine, PositionProcessResult
from hermes_trading.profitability import update_profitability_artifacts
from hermes_trading.reconciliation import ReconciliationReport, ReconciliationService
from hermes_trading.risk import RiskContext, RiskDecision, RiskEngine, resolve_risk_limits
from hermes_trading.strategy import StrategySignal, _decimal_setting, evaluate_strategy
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


@dataclass(frozen=True)
class LiveCycleResult:
    status: str
    new_orders: tuple[OrderRecord, ...]
    risk_decisions: tuple[RiskDecision, ...]
    reconciliation: ReconciliationReport | None
    heartbeat: Mapping[str, Any]

    @property
    def orders(self) -> tuple[OrderRecord, ...]:
        return self.new_orders


def resolve_trading_config(env: dict[str, str] | None = None) -> TradingConfig:
    values = env if env is not None else os.environ
    mode = values.get("HERMES_TRADING_MODE", "paper").strip().lower()
    if mode in {"paper", "training", "train", "demo"}:
        return TradingConfig(mode="training", execution="simulated", accepts_real_orders=False)
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


async def _update_profitability_best_effort(state_dir: Path) -> None:
    try:
        update_profitability_artifacts(
            state_dir,
            capital=Decimal(os.getenv("HERMES_PROFITABILITY_CAPITAL_USD", "100")),
            days=Decimal(os.getenv("HERMES_PROFITABILITY_WINDOW_DAYS", "30")),
        )
    except Exception as exc:
        await _write_json(
            state_dir / "profitability_error.json",
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    class DecimalSafeLoader(yaml.SafeLoader):
        pass

    def decimal_value(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
        return Decimal(loader.construct_scalar(node).replace("_", ""))

    DecimalSafeLoader.add_constructor("tag:yaml.org,2002:float", decimal_value)
    return yaml.load(path.read_text(), Loader=DecimalSafeLoader) or {}


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


async def run_live_cycle(
    assets: list[str],
    state: StateRepository,
    projector: ArtifactProjector,
    market_data: MarketDataCoordinator,
    strategy: dict[str, Any],
    gateway: ExchangeGateway,
    *,
    validate_only: bool,
    funds_alert_settings: FundsAlertSettings | None = None,
    notify: Callable[[str], Awaitable[None]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> LiveCycleResult:
    """Run one fail-closed Kraken validation cycle.

    Real submission stays unavailable here until the later qualification plan
    supplies its durable approval gate. The explicit gateway keeps tests and
    deployments from constructing credentials as a side effect of importing
    the worker.
    """
    if not validate_only:
        raise TradingModeError(
            "live cycle blocked: only validation-only is available before qualification"
        )
    if not getattr(gateway, "validation_only", False):
        raise TradingModeError("live cycle blocked: gateway is not validation-only")
    current_time = (now or (lambda: datetime.now(UTC)))()
    if current_time.tzinfo is None:
        raise ValueError("live cycle clock must include a timezone")
    current_time = current_time.astimezone(UTC)
    reconciliation = ReconciliationService(state, projector=projector)
    execution = ExecutionService(
        state,
        allow_submission=True,
        cost_ledger=CostLedger(projector.state_dir),
    )
    risk = RiskEngine(
        state,
        projector,
        limits=resolve_risk_limits(),
        now=lambda: current_time,
    )
    existing_order_ids = {
        str(row["order_id"])
        for row in state.export_canonical_tables()["orders"]
    }

    try:
        report = await reconciliation.run(gateway)
    except Exception as error:
        reason = f"reconciliation_unavailable:{type(error).__name__}"
        state.open_incident(
            "live-cycle:reconciliation", reason, severity="critical", opened_at=current_time
        )
        return _blocked_live_cycle("blocked_reconciliation", reason, None)
    if not report.safe_for_new_entries:
        return _blocked_live_cycle(
            "blocked_reconciliation", _issue_summary(report), report
        )

    protection = await reconciliation.verify_protective_stops(gateway, execution)
    if not protection.safe_for_new_entries:
        return _blocked_live_cycle(
            "blocked_protection", _issue_summary(protection), protection
        )

    funds_warning = await _notify_cycle_funds(
        projector,
        report.balances,
        funds_alert_settings,
        notify,
        current_time,
    )
    snapshots = await market_data.collect(assets)
    risk_decisions: list[RiskDecision] = []
    execution_results: list[ExecutionResult] = []
    market_blockers: list[str] = []

    for asset in assets:
        if snapshots.get(asset) is not None:
            continue
        state.open_incident(
            f"live-cycle:market:{asset}",
            "market_snapshot_missing",
            severity="critical",
            opened_at=current_time,
        )
        market_blockers.append(f"{asset}:market_snapshot_missing")
    if market_blockers:
        return _live_cycle_result(
            "blocked_market", ",".join(market_blockers), state, existing_order_ids,
            (), report, warnings=(funds_warning,) if funds_warning else (),
        )

    for asset in _prioritize_live_assets(assets, snapshots, strategy, state):
        snapshot = snapshots.get(asset)
        if snapshot is None:
            continue
        asset_strategy = resolve_asset_strategy(strategy, asset)
        position = next(
            (candidate for candidate in state.active_positions() if candidate.asset == asset),
            None,
        )
        signal = evaluate_strategy(snapshot, asset_strategy, position)
        if signal.action not in {"enter", "exit"}:
            continue
        decision = _live_decision(signal, asset_strategy, current_time)
        if signal.action == "exit":
            assert position is not None
            execution_results.append(await execution.submit_exit(decision, position, gateway))
            break

        rules = await gateway.load_rules(asset)
        exchange_minimum = rules.min_cost
        context = _risk_context(
            asset,
            snapshot.price or {},
            report.balances,
            state.active_positions(),
            current_time,
            state,
        )
        risk_decision = risk.authorize_entry(
            context=context,
            stop_loss_pct=_strategy_fraction(asset_strategy, "stop_loss_pct"),
            position_size_r=_decimal_setting(
                asset_strategy.get("position_size_r", "0.5"), "position_size_r"
            ),
            exchange_min_cost=(exchange_minimum or Decimal("0")),
        )
        risk_decisions.append(risk_decision)
        if not risk_decision.allowed:
            continue
        execution_results.append(
            await execution.submit_entry(decision, risk_decision, gateway)
        )
        # Reconciliation and protection must complete before another asset can
        # submit with stale balances or portfolio exposure.
        break

    try:
        report = await reconciliation.run(gateway)
    except Exception as error:
        reason = f"post_execution_reconciliation_unavailable:{type(error).__name__}"
        state.open_incident(
            "live-cycle:post-reconciliation", reason,
            severity="critical", opened_at=current_time,
        )
        return _live_cycle_result(
            "blocked_reconciliation", reason, state, existing_order_ids,
            tuple(risk_decisions), None, warnings=(funds_warning,) if funds_warning else (),
        )

    protection_results: list[ExecutionResult] = []
    for position in state.active_positions():
        asset_strategy = resolve_asset_strategy(strategy, position.asset)
        stop_fraction = _strategy_fraction(asset_strategy, "stop_loss_pct")
        desired_stop = position.entry_price * (Decimal("1") - stop_fraction)
        if position.stop_price != desired_stop:
            position = Position(
                position.position_id,
                position.asset,
                position.status,
                position.quantity,
                position.entry_price,
                desired_stop,
                position.strategy_version,
                position.opened_at,
                position.closed_at,
                position.realized_pnl,
                position.fees,
            )
            state.save_position(position)
        protection_signal = Decision(
            decision_id=f"position:{position.position_id}:protective-stop",
            asset=position.asset,
            action="protective_stop",
            reason="filled_entry_requires_stop",
            candle_id=f"position:{position.position_id}:{position.opened_at}",
            strategy_version=position.strategy_version,
            reference_price=position.entry_price,
            decided_at=current_time.isoformat(),
        )
        protection_results.append(
            await execution.ensure_protective_stop(
                protection_signal, position, gateway
            )
        )
    if protection_results:
        failed_protection = next(
            (
                result.reason or result.status
                for result in protection_results
                if result.status not in {"submitted"}
            ),
            None,
        )
        if failed_protection is not None:
            return _live_cycle_result(
                "blocked_protection", failed_protection, state, existing_order_ids,
                tuple(risk_decisions), report,
                warnings=(funds_warning,) if funds_warning else (),
            )
        try:
            report = await reconciliation.run(gateway)
        except Exception as error:
            reason = f"post_protection_reconciliation_unavailable:{type(error).__name__}"
            state.open_incident(
                "live-cycle:post-protection-reconciliation",
                reason,
                severity="critical",
                opened_at=current_time,
            )
            return _live_cycle_result(
                "blocked_reconciliation", reason, state, existing_order_ids,
                tuple(risk_decisions), report,
                warnings=(funds_warning,) if funds_warning else (),
            )

    projection_error: str | None = None
    try:
        projector.reconcile(state)
    except Exception as error:
        projection_error = f"artifact_projection_failed:{type(error).__name__}"
        state.open_incident(
            "live-cycle:artifact-projection", projection_error,
            severity="warning", opened_at=current_time,
        )

    blocking_execution = next(
        (
            result.reason or result.status
            for result in execution_results
            if result.status in {"reconciliation_required", "rejected"}
        ),
        None,
    )
    if not report.safe_for_new_entries or blocking_execution is not None:
        reason = blocking_execution or _issue_summary(report)
        return _live_cycle_result(
            "blocked_reconciliation", reason, state, existing_order_ids,
            tuple(risk_decisions), report, projection_error=projection_error,
            warnings=(funds_warning,) if funds_warning else (),
        )
    if market_blockers:
        return _live_cycle_result(
            "blocked_market", ",".join(market_blockers), state, existing_order_ids,
            tuple(risk_decisions), report, projection_error=projection_error,
            warnings=(funds_warning,) if funds_warning else (),
        )
    if projection_error is not None:
        status, reason = "degraded", projection_error
    elif any(decision.allowed for decision in risk_decisions):
        status, reason = "validation_only", "orders_validated_without_remote_submission"
    elif risk_decisions:
        status = "blocked_risk"
        reason = ",".join(decision.reason_code for decision in risk_decisions)
    else:
        status, reason = "no_trade", "no_entry_or_exit_signal"
    return _live_cycle_result(
        status, reason, state, existing_order_ids,
        tuple(risk_decisions), report, projection_error=projection_error,
        warnings=(funds_warning,) if funds_warning else (),
    )


def _blocked_live_cycle(
    status: str, reason: str, report: ReconciliationReport | None
) -> LiveCycleResult:
    return LiveCycleResult(
        status=status,
        new_orders=(),
        risk_decisions=(),
        reconciliation=report,
        heartbeat={"status": status, "reason": reason, "order_count": 0},
    )


def _live_cycle_result(
    status: str,
    reason: str,
    state: StateRepository,
    existing_order_ids: set[str],
    risk_decisions: tuple[RiskDecision, ...],
    report: ReconciliationReport | None,
    *,
    projection_error: str | None = None,
    warnings: tuple[str, ...] = (),
) -> LiveCycleResult:
    new_orders = tuple(
        state.order_by_client_id(str(row["client_order_id"]))
        for row in state.export_canonical_tables()["orders"]
        if str(row["order_id"]) not in existing_order_ids
    )
    canonical_orders = tuple(order for order in new_orders if order is not None)
    heartbeat = {
        "status": status,
        "reason": reason,
        "mode": "validation-only",
        "order_count": len(canonical_orders),
        "risk_decision_count": len(risk_decisions),
        "safe_for_new_entries": bool(report and report.safe_for_new_entries),
        "projection_error": projection_error,
        "warnings": warnings,
    }
    return LiveCycleResult(
        status, canonical_orders, risk_decisions, report, heartbeat
    )


def _issue_summary(report: ReconciliationReport) -> str:
    return ",".join(issue.code for issue in report.issues) or "reconciliation_blocked"


def _live_decision(
    signal: StrategySignal, strategy: Mapping[str, Any], now: datetime
) -> Decision:
    strategy_version = str(strategy.get("version", "01"))
    identity = f"{signal.asset}|{signal.action}|{signal.candle_id}|{strategy_version}"
    return Decision(
        decision_id=f"live-{uuid5(NAMESPACE_URL, identity)}",
        asset=signal.asset,
        action=signal.action,
        reason=signal.reason,
        candle_id=signal.candle_id,
        strategy_version=strategy_version,
        reference_price=signal.reference_price,
        decided_at=now.isoformat(),
    )


def _strategy_fraction(strategy: Mapping[str, Any], name: str) -> Decimal:
    return _decimal_setting(strategy.get(name, "0"), name) / Decimal("100")


def _risk_context(
    asset: str,
    price: Mapping[str, Any],
    balances: Mapping[str, Decimal],
    positions: tuple[Position, ...],
    observed_at: datetime,
    state: StateRepository,
) -> RiskContext:
    quote = asset.split("/", 1)[1]
    free_quote = balances.get(quote)
    bid = price.get("bid")
    ask = price.get("ask")
    spread_available = isinstance(bid, Decimal) and isinstance(ask, Decimal) and bid > 0 and ask >= bid
    spread = None
    if spread_available:
        midpoint = (bid + ask) / Decimal("2")
        spread = (ask - bid) / midpoint * Decimal("10000")
    symbol_exposure = sum(
        (position.quantity * position.entry_price for position in positions if position.asset == asset),
        Decimal("0"),
    )
    total_exposure = sum(
        (position.quantity * position.entry_price for position in positions), Decimal("0")
    )
    equity = free_quote + total_exposure if free_quote is not None else None
    daily_pnl, drawdown = _realized_risk_metrics(state, equity, observed_at)
    return RiskContext(
        symbol=asset,
        equity=equity,
        free_quote=free_quote,
        symbol_exposure=symbol_exposure if free_quote is not None else None,
        total_exposure=total_exposure if free_quote is not None else None,
        daily_pnl=daily_pnl,
        drawdown=drawdown,
        spread_bps=spread,
        spread_available=spread_available,
        spread_observed_at=observed_at,
        active_position=any(position.asset == asset for position in positions),
    )


def _prioritize_live_assets(
    assets: list[str], snapshots: Mapping[str, MarketSnapshot], strategy: Mapping[str, Any], state: StateRepository
) -> list[str]:
    """Put risk-reducing exits ahead of entries while preserving peer order."""
    active_by_asset = {position.asset: position for position in state.active_positions()}
    return sorted(
        assets,
        key=lambda asset: 0 if evaluate_strategy(
            snapshots[asset], resolve_asset_strategy(strategy, asset), active_by_asset.get(asset)
        ).action == "exit" else 1,
    )


def _realized_risk_metrics(
    state: StateRepository, equity: Decimal | None, observed_at: datetime
) -> tuple[Decimal | None, Decimal | None]:
    """Derive net P&L and drawdown from the durable closed-position history."""
    if equity is None:
        return None, None
    closed = state.closed_positions()
    day_start = observed_at.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_pnl = sum(
        (
            position.realized_pnl for position in closed
            if position.closed_at is not None
            and datetime.fromisoformat(position.closed_at).astimezone(UTC) >= day_start
        ),
        Decimal("0"),
    )
    running_equity = equity - sum((position.realized_pnl for position in closed), Decimal("0"))
    peak_equity = running_equity
    drawdown = Decimal("0")
    for position in closed:
        running_equity += position.realized_pnl
        peak_equity = max(peak_equity, running_equity)
        if peak_equity > 0:
            drawdown = max(drawdown, (peak_equity - running_equity) / peak_equity)
    return daily_pnl, drawdown


async def _notify_cycle_funds(
    projector: ArtifactProjector,
    balances: Mapping[str, Decimal],
    settings: FundsAlertSettings | None,
    notify: Callable[[str], Awaitable[None]] | None,
    now: datetime,
) -> str | None:
    if settings is None:
        return None
    message = build_due_funds_alert_from_balances(
        projector.state_dir, settings, balances, now
    )
    if message is None or notify is None:
        return None
    try:
        await notify(message)
    except Exception as error:
        return f"funds_alert_transport_failed:{type(error).__name__}"
    return None


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
                await _update_profitability_best_effort(state_dir)
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


async def _run_validation_only_loop(
    assets: list[str],
    state_dir: Path,
    interval_seconds: int,
    notification_settings: NotificationSettings,
    funds_alert_settings: FundsAlertSettings,
    pnl_summary: PnlSummaryCounter,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
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

    async def notify_one(message: str) -> None:
        await _notify_all([message], notification_settings)

    try:
        while True:
            started_at = datetime.now(UTC)
            gateway = KrakenGateway(
                _default_execution_exchange,
                validate_only=True,
            )
            try:
                strategy = _load_yaml(state_dir / "strategy.yaml")
                cycle = await run_live_cycle(
                    assets,
                    state,
                    projector,
                    market_data,
                    strategy,
                    gateway,
                    validate_only=True,
                    funds_alert_settings=funds_alert_settings,
                    notify=notify_one,
                    now=lambda: started_at,
                )
                heartbeat = {
                    "timestamp": started_at.isoformat(),
                    "assets": assets,
                    "strategy_version": strategy.get("version", "01"),
                    "mode": "live",
                    "execution": "kraken-validation-only",
                    **dict(cycle.heartbeat),
                }
                await _write_json(state_dir / "heartbeat.json", heartbeat)
                orders = [json_value(order) for order in cycle.new_orders]
                messages = build_cycle_notifications([], orders, notification_settings)
                messages.extend(
                    build_summary_notifications(
                        state_dir, [], notification_settings, pnl_summary, started_at
                    )
                )
                await _notify_all(messages, notification_settings)
                await _update_profitability_best_effort(state_dir)
                print(
                    "Hermes validation cycle "
                    f"status={cycle.status} reason={cycle.heartbeat['reason']} "
                    f"orders={len(cycle.new_orders)}",
                    flush=True,
                )
                consecutive_failures = 0
            except SchemaError:
                raise
            except Exception as error:
                consecutive_failures += 1
                await _write_json(
                    state_dir / "heartbeat.json",
                    {
                        "timestamp": started_at.isoformat(),
                        "assets": assets,
                        "mode": "live",
                        "execution": "kraken-validation-only",
                        "status": "error",
                        "reason": f"cycle_failed:{type(error).__name__}",
                        "error": str(error),
                        "consecutive_failures": consecutive_failures,
                    },
                )
                await _notify_all(
                    [build_error_notification(error, consecutive_failures)],
                    notification_settings,
                )
                if consecutive_failures >= 5:
                    raise RuntimeError(
                        "circuit breaker opened after 5 consecutive validation failures"
                    ) from error
            finally:
                await gateway.close()
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
    if not execution_settings.validate_only or execution_settings.allow_real_orders:
        raise TradingModeError(
            "real order worker remains blocked until qualification and explicit promotion"
        )
    await _run_validation_only_loop(
        assets,
        state_dir,
        interval_seconds,
        notification_settings,
        funds_alert_settings,
        pnl_summary,
    )
