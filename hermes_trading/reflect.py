from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

from hermes_trading.adapters import market_analysis
from hermes_trading.costs import CostLedger, openai_usage_event
from hermes_trading.score import net_return, score
from hermes_trading.model_runner import ReflectionModelRunner
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.providers import HermesCatalog, ModelCandidate, ProviderRouter
from hermes_trading.strategy_registry import StrategyRegistry


DEFAULT_OPENAI_REFLECTION_MODEL = "gpt-5.6-luna"
# Hard ceiling on how many variables one asset can change in a single
# reflection cycle when goal.yaml sets one_variable_only: false. Enforced in
# code (run_hermes_reflection), not just prompt wording -- a model response
# proposing more than this many changes has the extras dropped rather than
# applied, so this is a real cap on blast radius per cycle, independent of
# what any proposal_generator returns.
MAX_CHANGES_PER_ASSET_PER_CYCLE = 3
SUPPORTED_STRATEGY_VARIABLES = {
    "entry.threshold",
    "stop_loss_pct",
    "position_size_r",
    "exit.stop_atr",
    "exit.take_profit_atr",
    "exit.max_bars",
}
# Variables that must be rounded to whole numbers once the numeric proposal is
# validated (RSI thresholds and bar counts have no fractional meaning here).
_INTEGER_STRATEGY_VARIABLES = {"entry.threshold", "exit.max_bars"}
STRATEGY_VARIABLE_ALIASES = {
    "entry_threshold": "entry.threshold",
}
STRATEGY_VARIABLE_BOUNDS: dict[str, tuple[float, float]] = {
    "entry.threshold": (15.0, 35.0),
    "stop_loss_pct": (0.3, 3.0),
    "position_size_r": (0.1, 1.0),
    "exit.stop_atr": (0.5, 4.0),
    "exit.take_profit_atr": (1.0, 15.0),
    "exit.max_bars": (1.0, 24.0),
}
STRATEGY_VARIABLE_MAX_STEP: dict[str, float] = {
    "entry.threshold": 2.0,
    "stop_loss_pct": 0.2,
    "position_size_r": 0.1,
    "exit.stop_atr": 0.5,
    "exit.take_profit_atr": 7.0,
    "exit.max_bars": 4.0,
}
# Modes that never submit real Kraken orders (mirrors loop.py::resolve_trading_config's
# classification). Duplicated here rather than imported from hermes_trading.loop to
# keep this module's import surface light -- loop.py pulls in asyncio, ccxt-backed
# exchange access, etc. that reflect.py has no other reason to import.
_TRAINING_MODE_VALUES = {"paper", "training", "train", "demo"}


def _is_training_mode(env: dict[str, str]) -> bool:
    mode = str(env.get("HERMES_TRADING_MODE", "paper")).strip().lower()
    return mode in _TRAINING_MODE_VALUES


def _offered_reflection_variables(env: dict[str, str]) -> list[str]:
    """Variables the Hermes/LLM prompt is allowed to offer.

    ``position_size_r`` only affects RiskEngine.authorize_entry() on the live
    execution path (risk.py); PositionEngine, used whenever mode != live,
    sizes every simulated trade at a fixed quote_notional and never reads it
    (positions.py::_entry). Offering it in training mode wastes a whole
    one-variable-per-cycle reflection budget on a change with no effect.
    """
    variables = set(SUPPORTED_STRATEGY_VARIABLES)
    if _is_training_mode(env):
        variables.discard("position_size_r")
    return sorted(variables)


@dataclass(frozen=True)
class ReflectionPolicy:
    min_new_decisions: int = 30
    min_new_trades: int = 5
    min_closed_trades: int = 20
    cooldown_hours: float = 12.0
    max_consecutive_losses: int = 3
    score_threshold: float = -0.15
    max_age_hours: float = 24.0
    max_entry_threshold: float = 35.0


def _env_int(
    env: dict[str, str],
    name: str,
    default: int,
    minimum: int = 0,
) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")

    return value


def _env_float(
    env: dict[str, str],
    name: str,
    default: float,
) -> float:
    try:
        return float(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def resolve_reflection_policy(
    env: dict[str, str] | None = None,
) -> ReflectionPolicy:
    values = dict(os.environ if env is None else env)

    return ReflectionPolicy(
        min_new_decisions=_env_int(
            values,
            "HERMES_REFLECT_MIN_NEW_DECISIONS",
            30,
        ),
        min_new_trades=_env_int(
            values,
            "HERMES_REFLECT_MIN_NEW_TRADES",
            5,
        ),
        min_closed_trades=_env_int(values, "HERMES_REFLECT_MIN_CLOSED_TRADES", 20),
        cooldown_hours=_env_float(values, "HERMES_REFLECT_COOLDOWN_HOURS", 12.0),
        max_consecutive_losses=_env_int(
            values,
            "HERMES_REFLECT_MAX_CONSECUTIVE_LOSSES",
            3,
            minimum=1,
        ),
        score_threshold=_env_float(
            values,
            "HERMES_REFLECT_SCORE_THRESHOLD",
            -0.15,
        ),
        max_age_hours=_env_float(
            values,
            "HERMES_REFLECT_MAX_AGE_HOURS",
            24.0,
        ),
        max_entry_threshold=_env_float(
            values,
            "HERMES_REFLECT_MAX_ENTRY_THRESHOLD",
            35.0,
        ),
    )

def _reflection_state_path(state_dir: Path) -> Path:
    return state_dir / "reflection_state.json"


def _load_reflection_state(
    state_dir: Path,
) -> dict[str, dict[str, Any]]:
    path = _reflection_state_path(state_dir)

    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    symbols = payload.get("symbols", {})

    return symbols if isinstance(symbols, dict) else {}


def _write_reflection_state(
    state_dir: Path,
    symbols: dict[str, dict[str, Any]],
) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    }

    _reflection_state_path(state_dir).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _legacy_reflection_timestamp(state_dir: Path, asset: str) -> datetime | None:
    for record in reversed(_load_jsonl(state_dir / "hypotheses.jsonl")):
        mode = str(record.get("mode", ""))
        # hermes_multi_asset_* rows are per-cycle summaries (logged even when
        # nothing changed) and never carry an "asset" field -- they must not
        # be treated as a wildcard match for every asset below, or a run that
        # never had a single genuine per-asset reflection ends up re-poisoning
        # its own cooldown every cycle and can never become eligible again.
        if not mode.startswith("hermes_") or mode.startswith("hermes_multi_asset"):
            continue
        record_asset = str(record.get("asset", "")).upper()
        if record_asset and record_asset != asset.upper():
            continue
        timestamp = _parse_timestamp(record.get("timestamp"))
        if timestamp is not None:
            return timestamp
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _record_timestamp(record: dict[str, Any]) -> datetime | None:
    for field in ("timestamp", "decided_at", "closed_at", "created_at", "updated_at"):
        parsed = _parse_timestamp(record.get(field))
        if parsed is not None:
            return parsed
    return None


@dataclass(frozen=True)
class ReflectionHealthSettings:
    enabled: bool
    stale_after_hours: float
    cooldown_seconds: int


def resolve_reflection_health_settings(env: dict[str, str] | None = None) -> ReflectionHealthSettings:
    values = env if env is not None else os.environ
    enabled = str(values.get("HERMES_REFLECTION_STALE_ALERT_ENABLED", "true")).strip().lower() != "false"
    return ReflectionHealthSettings(
        enabled=enabled,
        stale_after_hours=float(values.get("HERMES_REFLECTION_STALE_ALERT_HOURS", "36")),
        cooldown_seconds=int(values.get("HERMES_REFLECTION_STALE_ALERT_COOLDOWN_SECONDS", "21600")),
    )


def _last_hypothesis_at(state_dir: Path) -> datetime | None:
    """Every run_hermes_reflection call appends a hypothesis record, whether or
    not it applied a change -- unlike reflection_state.json's per-asset
    last_reflection_at, which only updates on an applied change. This is the
    correct "was reflection invoked at all" signal for staleness detection.
    """
    for record in reversed(_load_jsonl(state_dir / "hypotheses.jsonl")):
        timestamp = _parse_timestamp(record.get("timestamp"))
        if timestamp is not None:
            return timestamp
    return None


def _load_reflection_health_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _write_reflection_health_state(path: Path, status: str, now: datetime) -> None:
    path.write_text(
        json.dumps({"status": status, "last_alert_at": now.isoformat()}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _reflection_alert_cooldown_elapsed(state: dict[str, Any], now: datetime, cooldown_seconds: int) -> bool:
    raw = state.get("last_alert_at")
    if not raw:
        return True
    last_alert_at = _parse_timestamp(raw)
    return last_alert_at is None or (now - last_alert_at).total_seconds() >= cooldown_seconds


def build_due_reflection_health_alert(
    state_dir: Path,
    settings: ReflectionHealthSettings,
    now: datetime | None = None,
) -> str | None:
    """Alert when reflection hasn't run in a long time, and send one resolve
    message when it resumes. Mirrors funds.py's alert/cooldown/resolve state
    machine, simplified to the two states this needs (ok/stale)."""
    if not settings.enabled:
        return None
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_at = _last_hypothesis_at(state_dir)
    if last_at is None:
        return None
    hours_since = (current_time - last_at).total_seconds() / 3600.0
    is_stale = hours_since >= settings.stale_after_hours

    health_path = state_dir / "reflection_health.json"
    state = _load_reflection_health_state(health_path)
    previous_status = str(state.get("status") or "ok")

    if not is_stale:
        if previous_status == "stale":
            _write_reflection_health_state(health_path, "ok", current_time)
            return "Hermes Trading | Reflection resumed: hypotheses.jsonl is updating again."
        return None

    if previous_status == "ok" or _reflection_alert_cooldown_elapsed(state, current_time, settings.cooldown_seconds):
        _write_reflection_health_state(health_path, "stale", current_time)
        return (
            f"Hermes Trading | Reflection has not run in {hours_since:.1f}h "
            f"(threshold {settings.stale_after_hours:.0f}h) -- check the in-process trigger "
            "(HERMES_REFLECTION_INPROCESS_EVERY_CYCLES) and the external hermes-trading-watch cron."
        )
    return None


def _regime_marker_path(state_dir: Path) -> Path:
    return state_dir / "reflection-regime-started-at.txt"


def load_regime_started_at(
    state_dir: Path,
    env: dict[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> datetime:
    """Timestamp marking the start of the current strategy "regime".

    Reflection's score/consecutive_losses metrics are scoped to trades closed
    at or after this point (see ``_records_since`` and its callers below), so
    a losing streak from a since-replaced entry/exit indicator can't
    perpetually veto the current one -- mirrors
    qualification_runner.py::run_from_state's training-started-at pattern:
    an env override takes precedence, otherwise a marker file is read and, if
    absent, created defaulting to now (so the very first read after this
    scoping ships "starts the clock" instead of silently doing nothing).
    """
    env = dict(os.environ if env is None else env)
    current = now or datetime.now(timezone.utc)
    configured = env.get("HERMES_REFLECTION_REGIME_STARTED_AT", "").strip()
    marker_path = _regime_marker_path(state_dir)
    try:
        value = configured or (
            marker_path.read_text(encoding="utf-8").strip() if marker_path.exists() else current.isoformat()
        )
        started_at = datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        started_at = current
    if not configured and not marker_path.exists():
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(started_at.isoformat() + "\n", encoding="utf-8")
    return started_at


def mark_regime_start(state_dir: Path, *, now: datetime | None = None) -> datetime:
    """Reset the regime marker to now.

    Call this after a structural strategy change (entry/exit indicator,
    direction) so score/consecutive_losses stop being computed over trades
    from a strategy logic that no longer exists. Exposed for
    scripts/mark_reflection_regime.py.
    """
    started_at = now or datetime.now(timezone.utc)
    marker_path = _regime_marker_path(state_dir)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(started_at.isoformat() + "\n", encoding="utf-8")
    return started_at


def _records_since(
    records: list[dict[str, Any]],
    timestamp: datetime | None,
) -> list[dict[str, Any]]:
    if timestamp is None:
        return records

    result = []

    for record in records:
        record_time = _record_timestamp(record)

        if record_time is not None and record_time >= timestamp:
            result.append(record)

    return result


def _records_after(
    records: list[dict[str, Any]],
    timestamp: datetime | None,
) -> list[dict[str, Any]]:
    if timestamp is None:
        return records

    result = []

    for record in records:
        record_time = _record_timestamp(record)

        if record_time is not None and record_time > timestamp:
            result.append(record)

    return result

def _records_for_asset(
    records: list[dict[str, Any]],
    asset: str,
) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if str(record.get("asset", "")).upper() == asset.upper()
    ]


def _configured_assets(
    strategy: dict[str, Any],
    goal: dict[str, Any],
) -> list[str]:
    symbols = strategy.get("symbols")

    if isinstance(symbols, dict) and symbols:
        return sorted(str(asset) for asset in symbols)

    goal_assets = goal.get("assets")

    if isinstance(goal_assets, list):
        return sorted(
            {
                str(asset).strip().upper()
                for asset in goal_assets
                if str(asset).strip()
            }
        )

    asset = str(goal.get("asset", "")).strip().upper()

    return [asset] if asset else []

def _consecutive_losses(
    trades: list[dict[str, Any]],
) -> int:
    losses = 0

    for trade in reversed(trades):
        pnl = net_return(trade)

        if pnl < 0:
            losses += 1
        else:
            break

    return losses


def _is_no_trade_decision(decision: dict[str, Any]) -> bool:
    """True when a decision record represents a no-trade cycle.

    Real ``decisions.jsonl`` records (persisted from ``domain.Decision``)
    carry an ``action`` field (``"no_trade"``, ``"enter"``, ``"exit"``,
    ``"hold"``). Older callers/tests instead used a ``trade_closed`` boolean
    with no ``action`` field -- keep that as a fallback so they still work.
    """
    action = decision.get("action")
    if action is not None:
        return action == "no_trade"
    return not bool(decision.get("trade_closed"))


def _asset_needs_reflection(
    asset: str,
    trades: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    last_reflection_at: datetime | None,
    goal: dict[str, Any],
    policy: ReflectionPolicy,
    now: datetime,
    regime_started_at: datetime | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    new_trades = _records_after(trades, last_reflection_at)
    new_decisions = _records_after(decisions, last_reflection_at)
    no_trade_decisions = [
        decision
        for decision in new_decisions
        if _is_no_trade_decision(decision)
    ]
    recent_entry_values = [
        float(decision["entry_value"])
        for decision in no_trade_decisions
        if decision.get("entry_value") is not None
    ]
    # Real no-trade decisions always carry a `reason` (e.g.
    # "expected_payoff_below_costs", "entry_condition_not_met") -- track which
    # one dominates so the accessibility guard below can route to the
    # variable that actually gates entries instead of always assuming RSI.
    no_trade_reason_counts = Counter(
        str(decision["reason"]) for decision in no_trade_decisions if decision.get("reason")
    )

    # Eligibility (has_trade_history below) intentionally stays on the full,
    # unscoped trade history -- an asset with a long track record should still
    # be trusted to run automated no-trade-drought tuning even while its
    # regime-scoped sample is still empty. Only the *performance judgment*
    # metrics (score, consecutive_losses) are scoped to the current regime, so
    # a losing streak from a since-replaced indicator can't keep vetoing the
    # current one every cycle.
    scored_trades = _records_since(trades, regime_started_at)
    consecutive_losses = _consecutive_losses(scored_trades)
    current_score = score(scored_trades, goal) if scored_trades else -0.2

    hours_since_reflection: float | None = None

    if last_reflection_at is not None:
        hours_since_reflection = (
            now - last_reflection_at
        ).total_seconds() / 3600.0

    reasons = []

    # A brand-new asset (total_trades below policy.min_closed_trades, e.g. LINK
    # or SOL the day they're added) must not be permanently excluded from the
    # no-trade-drought path just for lacking history it can never accumulate
    # without that path's help first -- that was a real bootstrap deadlock
    # (zero trades -> ineligible -> entry.threshold never loosens -> zero
    # trades, forever). Once an asset does have min_closed_trades or more,
    # trade_sample_eligible's own len(new_trades) >= min_new_trades bar
    # already requires real evidence, so nothing here weakens that judgment
    # for an asset with an established track record.
    trade_sample_eligible = len(new_trades) >= policy.min_new_trades
    no_trade_drought_eligible = (
        len(new_trades) == 0
        and len(no_trade_decisions) >= policy.min_new_decisions
    )
    eligible_sample = trade_sample_eligible or no_trade_drought_eligible
    cooling_down = hours_since_reflection is not None and hours_since_reflection < policy.cooldown_hours

    if trade_sample_eligible and len(new_decisions) >= policy.min_new_decisions:
        reasons.append(
            f"{len(new_decisions)} new decisions"
        )

    if trade_sample_eligible:
        reasons.append(
            f"{len(new_trades)} new trades"
        )

    if no_trade_drought_eligible:
        reasons.append(
            f"{len(no_trade_decisions)} no-trade decisions"
        )

    if trade_sample_eligible and consecutive_losses >= policy.max_consecutive_losses:
        reasons.append(
            f"{consecutive_losses} consecutive losses"
        )

    if trade_sample_eligible and current_score <= policy.score_threshold:
        reasons.append(
            f"score {current_score} <= {policy.score_threshold}"
        )

    has_new_data = bool(new_trades or new_decisions)

    if (
        trade_sample_eligible
        and hours_since_reflection is not None
        and hours_since_reflection >= policy.max_age_hours
    ):
        reasons.append(
            f"{hours_since_reflection:.1f} hours since last reflection"
        )

    # Première exécution : demander un minimum de données.
    if last_reflection_at is None and eligible_sample:
        if len(decisions) >= policy.min_new_decisions:
            reasons.append("initial symbol reflection")

    if cooling_down:
        reasons = []

    metrics = {
        "asset": asset,
        "new_decisions": len(new_decisions),
        "new_trades": len(new_trades),
        "new_no_trade_decisions": len(no_trade_decisions),
        "total_decisions": len(decisions),
        "total_trades": len(trades),
        "scored_trades": len(scored_trades),
        "eligible_sample": eligible_sample,
        "cooldown_active": cooling_down,
        "consecutive_losses": consecutive_losses,
        "score": current_score,
        "hours_since_reflection": hours_since_reflection,
        "dominant_no_trade_reason": (
            no_trade_reason_counts.most_common(1)[0][0] if no_trade_reason_counts else None
        ),
    }

    if recent_entry_values:
        metrics.update(
            {
                "min_recent_entry_value": round(min(recent_entry_values), 4),
                "avg_recent_entry_value": round(sum(recent_entry_values) / len(recent_entry_values), 4),
                "max_recent_entry_value": round(max(recent_entry_values), 4),
            }
        )

    return bool(reasons), reasons, metrics


def _entry_threshold(strategy: dict[str, Any]) -> float | None:
    try:
        return float(strategy.get("entry", {}).get("threshold"))
    except (TypeError, ValueError):
        return None


def _no_trade_accessibility_proposal(
    asset: str,
    effective_strategy: dict[str, Any],
    proposal: dict[str, Any] | None,
    metrics: dict[str, Any],
    policy: ReflectionPolicy,
) -> dict[str, Any] | None:
    if int(metrics.get("new_trades", 0)) > 0:
        return proposal

    if int(metrics.get("new_no_trade_decisions", 0)) < policy.min_new_decisions:
        return proposal

    if metrics.get("dominant_no_trade_reason") == "expected_payoff_below_costs":
        # positions.py::_expected_payoff_covers_costs blocks entries before RSI
        # is ever evaluated, so entry.threshold cannot reopen these no-trade
        # droughts -- exit.take_profit_atr is the variable that actually gates
        # them. See _payoff_gate_accessibility_proposal.
        payoff_proposal = _payoff_gate_accessibility_proposal(asset, effective_strategy)
        if payoff_proposal is not None:
            return payoff_proposal

    current_threshold = _entry_threshold(effective_strategy)
    min_recent_entry_value = metrics.get("min_recent_entry_value")

    if current_threshold is None:
        return proposal

    if min_recent_entry_value is None:
        if proposal is not None:
            return proposal
        target = min(policy.max_entry_threshold, current_threshold + 2.0)
        if target <= current_threshold:
            return proposal
        return {
            "asset": asset,
            "variable": "entry.threshold",
            "value": target,
            "reason": (
                "no-trade accessibility guard: recent live decisions did not include "
                "indicator values, so the training entry threshold is raised by the "
                "maximum validated step."
            ),
        }

    if float(min_recent_entry_value) <= current_threshold:
        return proposal

    if proposal and proposal.get("variable") == "entry.threshold":
        try:
            if float(proposal.get("value")) > float(min_recent_entry_value):
                return proposal
        except (TypeError, ValueError):
            pass

    target = min(
        policy.max_entry_threshold,
        current_threshold + 2.0,
        math.ceil(float(min_recent_entry_value) + 1.0),
    )
    target = max(current_threshold + 1.0, target)

    return {
        "asset": asset,
        "variable": "entry.threshold",
        "value": target,
        "reason": (
            "no-trade RSI accessibility guard: recent no-trade decisions all observed RSI "
            f"above the current threshold {current_threshold}, with minimum RSI "
            f"{float(min_recent_entry_value):.4f}; raising the training entry threshold "
            "is the only supported one-parameter change that can make entries reachable."
        ),
    }


def _payoff_gate_accessibility_proposal(
    asset: str,
    effective_strategy: dict[str, Any],
) -> dict[str, Any] | None:
    """Propose raising exit.take_profit_atr when the ATR payoff gate is what's
    blocking entries, instead of the entry.threshold guard above.

    A larger take_profit_atr raises the ATR-implied take-profit percentage
    (``atr * take_profit_atr / close``), which is exactly what
    positions.py::_expected_payoff_covers_costs compares against round-trip
    trading costs. Returns None (falls back to the caller's existing
    proposal/guard) when take_profit_atr isn't configured or is already at
    its validated maximum.
    """
    exit_rule = effective_strategy.get("exit")
    if not isinstance(exit_rule, dict) or "take_profit_atr" not in exit_rule or "trailing_atr" in exit_rule:
        return None
    try:
        current = float(exit_rule["take_profit_atr"])
    except (TypeError, ValueError):
        return None
    high = STRATEGY_VARIABLE_BOUNDS["exit.take_profit_atr"][1]
    step = STRATEGY_VARIABLE_MAX_STEP["exit.take_profit_atr"]
    target = min(high, current + step)
    if target <= current:
        return None
    return {
        "asset": asset,
        "variable": "exit.take_profit_atr",
        "value": target,
        "reason": (
            "no-trade accessibility guard: recent no-trade decisions are dominated by "
            "expected_payoff_below_costs, which blocks entries before RSI is evaluated. "
            "entry.threshold cannot fix this; raising the ATR take-profit multiplier is "
            "the only supported one-parameter change that reopens entries."
        ),
    }


def _deep_merge(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    merged = yaml.safe_load(yaml.safe_dump(base))

    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _deep_merge(
                merged[key],
                value,
            )
        else:
            merged[key] = yaml.safe_load(
                yaml.safe_dump(value)
            )

    return merged


def _resolve_asset_strategy(
    strategy: dict[str, Any],
    asset: str,
) -> dict[str, Any]:
    symbols = strategy.get("symbols", {})

    base = {
        key: value
        for key, value in strategy.items()
        if key != "symbols"
    }

    override = {}

    if isinstance(symbols, dict):
        candidate = symbols.get(asset, {})

        if isinstance(candidate, dict):
            override = candidate

    return _deep_merge(base, override)

def _market_analysis_snapshot(
    effective_strategy: dict[str, Any],
    metrics: dict[str, Any],
    decisions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_decision = decisions[-1] if decisions else {}
    latest_trade = trades[-1] if trades else {}
    return {
        "entry_threshold": _entry_threshold(effective_strategy),
        "exit_take_profit_atr": effective_strategy.get("exit", {}).get("take_profit_atr"),
        "score": metrics.get("score"),
        "consecutive_losses": metrics.get("consecutive_losses"),
        "dominant_no_trade_reason": metrics.get("dominant_no_trade_reason"),
        "recent_reference_price": latest_decision.get("reference_price"),
        "recent_decision_reason": latest_decision.get("reason"),
        "recent_trade_realized_pnl": latest_trade.get("realized_pnl"),
        "new_decisions_since_last_reflection": metrics.get("new_decisions"),
        "new_trades_since_last_reflection": metrics.get("new_trades"),
    }


def _fetch_market_analysis(
    asset: str,
    snapshot: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any] | None:
    """Advisory-only local-model opinion for the reflection prompt below.

    Returns None when the feature is off (default -- opt in with
    HERMES_MARKET_ANALYSIS_ENABLED=true). When enabled, any failure (model
    unreachable, malformed response, ...) is captured as an {"error": ...}
    dict rather than raised, so a flaky local model can never break a
    reflection cycle -- see market_analysis.fetch's docstring for why this
    stays advisory-only and out of the live trading decision entirely.
    """
    if str(env.get("HERMES_MARKET_ANALYSIS_ENABLED", "false")).strip().lower() not in {"1", "true", "yes"}:
        return None
    try:
        return market_analysis.fetch(asset, snapshot, env)
    except Exception as error:
        return {"error": str(error)}


def _build_asset_reflection_prompt(
    asset: str,
    effective_strategy: dict[str, Any],
    goal: dict[str, Any],
    trades: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    trigger_reasons: list[str],
    metrics: dict[str, Any],
    env: dict[str, str],
    market_analysis_opinion: dict[str, Any] | None = None,
) -> str:
    variable_options = ", ".join(f'"{name}"' for name in _offered_reflection_variables(env))
    market_analysis_section = ""
    if market_analysis_opinion and "error" not in market_analysis_opinion:
        market_analysis_section = (
            "\nA local market-analysis model offered this opinion. It is a small, "
            "unverified model -- weigh it lightly against the trade/decision data "
            f"above, which is ground truth and this is not:\n{json.dumps(market_analysis_opinion)}\n"
        )
    # goal.yaml's one_variable_only (default true) picks between two distinct
    # JSON response schemas -- not just wording. Each proposed variable still
    # goes through _validate_strategy_change's per-variable bounds/max-step
    # guard exactly as before; MAX_CHANGES_PER_ASSET_PER_CYCLE additionally
    # caps how many of them a single cycle can apply, independent of what the
    # model returns.
    allow_multiple = goal.get("one_variable_only", True) is False
    if allow_multiple:
        schema_instructions = (
            "You are reviewing exactly one trading symbol.\n"
            f"Propose between one and {MAX_CHANGES_PER_ASSET_PER_CYCLE} strategy changes for this symbol.\n"
            "Return exactly one JSON object without Markdown or extra text.\n\n"
            "Required keys:\n"
            f'- "asset": exactly "{asset}"\n'
            '- "changes": a JSON array of 1 to '
            f"{MAX_CHANGES_PER_ASSET_PER_CYCLE} objects, each with:\n"
            f'    - "variable": exactly one of {variable_options}\n'
            '    - "value": the proposed numeric value\n'
            '    - "reason": concise justification based only on the supplied data\n\n'
            "Do not include the symbol in a variable path.\n"
            "Do not propose changes for another symbol.\n"
            "Do not propose the same variable twice.\n"
            "Prefer conservative incremental adjustments.\n\n"
        )
    else:
        schema_instructions = (
            "You are reviewing exactly one trading symbol.\n"
            "Propose at most one strategy change for this symbol.\n"
            "Return exactly one JSON object without Markdown or extra text.\n\n"
            "Required keys:\n"
            f'- "variable": exactly one of {variable_options}\n'
            '- "value": the proposed numeric value\n'
            '- "reason": concise justification based only on the supplied data\n'
            f'- "asset": exactly "{asset}"\n\n'
            "Do not include the symbol in the variable path.\n"
            "Do not propose changes for another symbol.\n"
            "Do not change more than one variable.\n"
            "Prefer a conservative incremental adjustment.\n\n"
        )
    return (
        schema_instructions
        + f"Symbol:\n{asset}\n\n"
        f"Reflection triggers:\n{json.dumps(trigger_reasons)}\n\n"
        f"Symbol metrics:\n{json.dumps(metrics)}\n\n"
        "Effective symbol strategy after applying global defaults:\n"
        f"{yaml.safe_dump(effective_strategy, sort_keys=False)}\n"
        f"Goal:\n{yaml.safe_dump(goal, sort_keys=False)}\n"
        f"Recent symbol trades:\n{json.dumps(trades[-25:])}\n"
        "Recent symbol decisions, including no-trade decisions:\n"
        f"{json.dumps(decisions[-100:])}\n"
        f"{market_analysis_section}"
    )

def generate_deterministic_asset_proposal(
    asset: str,
    prompt: str,
    env: dict[str, str],
    *,
    effective_strategy: dict[str, Any],
    metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    goal: dict[str, Any],
    policy: ReflectionPolicy,
    **_ignored: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Pure-Python proposal generator needing no external process or API call.

    Used as the in-process reflection trigger's proposal source (loop.py runs
    inside the Railway container, which has no ``hermes`` CLI, no local model,
    and typically no OpenAI key) -- it must never shell out or make a network
    call. Reuses the exact same accessibility-guard and scoring logic the
    LLM path relies on, so it produces proposals the rest of the pipeline
    (bounds clamping, one-variable-only, hypothesis logging) already knows
    how to validate and apply.
    """
    model_info = {"provider": "deterministic", "model": None, "errors": []}

    if metrics.get("dominant_no_trade_reason") == "expected_payoff_below_costs":
        payoff_proposal = _payoff_gate_accessibility_proposal(asset, effective_strategy)
        if payoff_proposal is not None:
            return payoff_proposal, model_info

    current_threshold = _entry_threshold(effective_strategy)
    if current_threshold is None:
        return None, model_info

    low, high = STRATEGY_VARIABLE_BOUNDS["entry.threshold"]
    step = STRATEGY_VARIABLE_MAX_STEP["entry.threshold"]
    target_return = float(goal.get("target_return_30d", 0.05))
    realized_return = _realized_return(trades)
    current_score = score(trades, goal) if trades else -0.2
    winners_miss_costs = not _average_winning_trade_clears_costs(trades, effective_strategy)

    if realized_return < target_return and (current_score < 0 or winners_miss_costs):
        after = max(low, current_threshold - step)
        reason = (
            "deterministic in-process reflection: realized return missed target and "
            + (
                "winning trades don't clear round-trip transaction costs"
                if winners_miss_costs and current_score >= 0
                else "the score is negative"
            )
            + ", so entry was tightened to trade more selectively."
        )
    elif realized_return < target_return:
        after = min(min(high, policy.max_entry_threshold), current_threshold + step)
        reason = "deterministic in-process reflection: realized return missed target, so entry was loosened."
    else:
        after = max(low, current_threshold - step)
        reason = "deterministic in-process reflection: realized return met target, so entry was tightened."

    if after == current_threshold:
        return None, {
            **model_info,
            "outcome": "no_change",
            "reason": "deterministic review chose no change: entry threshold is already at the applicable bound",
        }

    return (
        {"asset": asset, "variable": "entry.threshold", "value": after, "reason": reason},
        model_info,
    )


def _generate_asset_proposal(
    asset: str,
    prompt: str,
    env: dict[str, str],
    **_ignored: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Hermes automatic selection is always tried before free candidates and OpenAI."""
    state_dir = Path(env["HERMES_REFLECTION_STATE_DIR"])
    repository = StateRepository.open(state_dir / "trading.db")
    try:
        repository.migrate()
        cache_path = Path(env.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "provider_models_cache.json"
        fallback_model = env.get("HERMES_OPENAI_FALLBACK_MODEL", DEFAULT_OPENAI_REFLECTION_MODEL)
        ledger = CostLedger(state_dir)
        local_model = env.get("HERMES_LOCAL_MODEL", "").strip()
        local_run = (
            lambda request: _run_local_model_prompt(request, local_model, env)
        ) if local_model else None
        openai_run = (
            lambda request: _run_openai_prompt(request, fallback_model, env, cost_ledger=ledger)
        ) if _has_openai_api_key(env) else None
        pinned_provider = env.get("HERMES_REFLECTION_PINNED_PROVIDER", "").strip()
        pinned_model = env.get("HERMES_REFLECTION_PINNED_MODEL", "").strip()
        pinned_candidate = (
            ModelCandidate(provider=pinned_provider, model=pinned_model, free=False, source="pinned")
            if pinned_provider and pinned_model
            else None
        )
        result = ReflectionModelRunner(
            ProviderRouter(repository, HermesCatalog.load(cache_path)),
            local_run=local_run,
            openai_run=openai_run,
            cost_ledger=ledger,
            pinned_candidate=pinned_candidate,
        ).run(prompt)
        proposal = json.loads(result.output)
        if not isinstance(proposal, dict):
            raise ValueError("Hermes proposal must be a JSON object")
        proposal["asset"] = asset
        return proposal, {"provider": result.provider, "model": result.model, "errors": []}
    except Exception as error:
        return None, {"provider": None, "model": None, "errors": [{"error": str(error)}]}
    finally:
        repository.close()


def _load_yaml(path: Path) -> dict[str, Any]:
    # Explicit encoding -- see hermes_trading.loop._load_yaml's comment: a
    # UTF-8 BOM decodes as mojibake instead of being stripped when
    # read_text() falls back to a non-UTF-8 locale encoding (cp1252 on
    # Windows), corrupting the first YAML key.
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[-limit:] if limit else rows


def _next_version(version: str) -> str:
    return f"{int(version) + 1:02d}"


def _archive_current(state_dir: Path, strategy: dict[str, Any]) -> None:
    history = state_dir / "history"
    history.mkdir(exist_ok=True)
    current_number = int(strategy.get("version", "1"))
    _write_yaml(history / f"v{current_number:04d}.yaml", strategy)


def _append_hypothesis(state_dir: Path, payload: dict[str, Any]) -> None:
    with (state_dir / "hypotheses.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _append_market_analysis_log(state_dir: Path, asset: str, opinion: dict[str, Any]) -> None:
    """Records every attempted market-analysis call (success or error),
    independent of whether the resulting proposal gets applied -- unlike
    hypotheses.jsonl, which only carries market_analysis on applied changes,
    this answers "when did we last actually call the consultant model" with
    a direct file read instead of cross-referencing the model server's own
    logs (see docs/COMMANDS.md's baseline-vs-consultant comparison section
    for why that gap mattered)."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "asset": asset,
        "status": "error" if "error" in opinion else "ok",
        **opinion,
    }
    with (state_dir / "market_analysis.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _has_openai_api_key(env: dict[str, str]) -> bool:
    return bool(env.get("OPENAI_API_KEY") or env.get("HERMES_OPENAI_API_KEY"))


def _realized_return(trades: list[dict[str, Any]]) -> float:
    return sum(net_return(trade) for trade in trades)


def _max_drawdown(trades: list[dict[str, Any]]) -> float:
    peak = None
    worst = 0.0
    running_equity = 100.0
    for trade in trades:
        if trade.get("equity") is not None:
            try:
                running_equity = float(trade["equity"])
            except (TypeError, ValueError):
                running_equity *= 1.0 + net_return(trade)
        else:
            running_equity *= 1.0 + net_return(trade)
        peak = running_equity if peak is None else max(peak, running_equity)
        if peak:
            worst = max(worst, (peak - running_equity) / peak)
    return worst


def _round_trip_cost_pct(strategy: dict[str, Any]) -> float:
    """Simulated round-trip transaction cost (both legs taker) plus the minimum edge, as a fraction."""
    simulation = strategy.get("simulation")
    simulation = simulation if isinstance(simulation, dict) else {}
    taker_fee_rate = float(simulation.get("taker_fee_rate", 0.004))
    slippage_rate = float(simulation.get("slippage_rate", 0.001))
    minimum_edge_pct = float(simulation.get("minimum_expected_edge_pct", 0.2))
    return 2 * (taker_fee_rate + slippage_rate) + minimum_edge_pct / 100


def _average_winning_trade_clears_costs(trades: list[dict[str, Any]], strategy: dict[str, Any]) -> bool:
    """False when winners are too small (or absent) to cover round-trip transaction costs.

    A strategy can look fine by score/realized-return alone while its winners are
    consistently smaller than fees + slippage -- the average trade is a "win" on
    direction but a loss after costs. Loosening entry in that state only produces
    more of the same losing trades, so this feeds the same tightening branch as a
    negative score.
    """
    wins = [net_return(trade) for trade in trades if net_return(trade) > 0]
    if not wins:
        return False
    return (sum(wins) / len(wins)) >= _round_trip_cost_pct(strategy)


def run_fallback_reflection(state_dir: Path) -> dict[str, Any]:
    from hermes_trading.reflection_supervision import assert_writer_owner
    assert_writer_owner(state_dir, "deterministic_worker")
    from hermes_trading.execution_policy import strategy_is_frozen
    if strategy_is_frozen(_load_yaml(state_dir / "strategy.yaml")):
        return {"status": "frozen", "changes": []}
    strategy_path = state_dir / "strategy.yaml"
    goal = _load_yaml(state_dir / "goal.yaml")
    strategy = _load_yaml(strategy_path)
    trades = _load_jsonl(state_dir / "trades.jsonl")

    updated = yaml.safe_load(yaml.safe_dump(strategy))
    realized_return = _realized_return(trades)
    drawdown = _max_drawdown(trades)
    current_score = score(trades, goal)

    if drawdown > float(goal.get("max_drawdown", 0.08)):
        variable = "stop_loss_pct"
        before = float(updated.get("stop_loss_pct", 2.0))
        after = max(0.2, round(before - 0.2, 2))
        updated["stop_loss_pct"] = after
        reason = "drawdown exceeded the maximum, so the fallback tightened risk."
    else:
        variable = "entry.threshold"
        before = int(updated.setdefault("entry", {}).get("threshold", 30))
        target = float(goal.get("target_return_30d", 0.05))
        winners_miss_costs = not _average_winning_trade_clears_costs(trades, strategy)
        if realized_return < target and (current_score < 0 or winners_miss_costs):
            # Underperforming AND (losing money OR winners too small to clear
            # round-trip transaction costs): entries are too frequent or too
            # low-quality relative to trading costs. Loosening the entry filter
            # here would only generate more losing trades, so tighten instead.
            after = max(2, before - 2)
            reason = (
                "realized return missed the target and average winning trades don't clear "
                "round-trip transaction costs, so the fallback tightened entry to trade more selectively."
                if winners_miss_costs and current_score >= 0
                else "realized return missed the target and the score is negative, "
                "so the fallback tightened entry to trade more selectively."
            )
        elif realized_return < target:
            after = before + 2
            reason = "realized return missed the target, so the fallback loosened entry."
        else:
            after = max(2, before - 2)
            reason = "realized return met the target, so the fallback tightened entry."
        updated["entry"]["threshold"] = after

    _archive_current(state_dir, strategy)
    updated["version"] = _next_version(str(strategy.get("version", "01")))
    _write_yaml(strategy_path, updated)
    _append_hypothesis(
        state_dir,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "fallback",
            "strategy_version": updated["version"],
            "score": current_score,
            "changed": variable,
            "before": before,
            "after": after,
            "reason": reason,
        },
    )
    return updated


def _extract_openai_text(payload: dict[str, Any]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"]).strip()
    chunks = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(str(text))
    if not chunks:
        raise ValueError("OpenAI response did not contain text output")
    return "\n".join(chunks).strip()


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and message.get("content"):
                return str(message["content"]).strip()
            if choice.get("text"):
                return str(choice["text"]).strip()
    raise ValueError("local model response did not contain text output")


def _run_local_model_prompt(
    prompt: str,
    model: str,
    env: dict[str, str],
) -> str:
    port = env.get("HERMES_LOCAL_MODEL_PORT", "12434").strip() or "12434"
    timeout = int(env.get("HERMES_REFLECTION_TIMEOUT_SECONDS", "60"))
    response = httpx.post(
        f"http://127.0.0.1:{port}/engines/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return _extract_chat_completion_text(response.json())


def _run_openai_prompt(
    prompt: str,
    model: str,
    env: dict[str, str],
    *,
    cost_ledger: CostLedger | None = None,
) -> str:
    api_key = env.get("OPENAI_API_KEY") or env.get("HERMES_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured")
    response = httpx.post(
        env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": prompt,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if cost_ledger is not None:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        cost_ledger.record(
            openai_usage_event(
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                model=model,
                pricing=_openai_pricing(env),
            )
        )
    return _extract_openai_text(payload)


def _openai_pricing(env: dict[str, str]) -> dict[str, str] | None:
    version = env.get("HERMES_OPENAI_PRICING_VERSION")
    input_per_1m = env.get("HERMES_OPENAI_INPUT_COST_PER_1M")
    output_per_1m = env.get("HERMES_OPENAI_OUTPUT_COST_PER_1M")
    if not (version and input_per_1m and output_per_1m):
        return None
    return {
        "version": version,
        "input_per_1m": input_per_1m,
        "output_per_1m": output_per_1m,
    }


def _normalize_strategy_proposal(
    strategy: dict[str, Any],
    proposal: dict[str, Any],
) -> tuple[str, str | None]:
    variable = str(proposal["variable"]).strip()
    asset = proposal.get("asset")

    if asset is not None:
        asset = str(asset).strip().upper() or None

    if variable.startswith("symbols."):
        scoped_path = variable.removeprefix("symbols.")

        matches = [
            supported
            for supported in SUPPORTED_STRATEGY_VARIABLES
            if scoped_path.endswith(f".{supported}")
        ]

        if len(matches) != 1:
            raise ValueError(
                f"Unable to resolve symbol-scoped variable: {variable}"
            )

        normalized = matches[0]
        parsed_asset = scoped_path[
            : -(len(normalized) + 1)
        ].upper()

        if asset is not None and asset != parsed_asset:
            raise ValueError(
                "Hermes proposal asset mismatch: "
                f"{asset!r} != {parsed_asset!r}"
            )

        variable = normalized
        asset = parsed_asset

    variable = STRATEGY_VARIABLE_ALIASES.get(variable, variable)

    if variable not in SUPPORTED_STRATEGY_VARIABLES:
        raise ValueError(
            f"Hermes proposed unsupported variable: {variable}"
        )

    if asset:
        symbols = strategy.get("symbols", {})

        if not isinstance(symbols, dict):
            raise ValueError(
                "strategy.symbols must be a mapping"
            )

        if asset not in symbols:
            raise ValueError(
                f"Hermes proposed unknown strategy asset: {asset}"
            )

    return variable, asset


def _variable_path(variable: str) -> tuple[str, ...]:
    """Split a supported variable name into its nested-mapping path.

    ``"stop_loss_pct"`` is a root-level key (``("stop_loss_pct",)``);
    ``"entry.threshold"`` and the ``exit.*`` variables live one level deep
    (``("entry", "threshold")``, ``("exit", "stop_atr")``, ...).
    """
    return tuple(variable.split("."))


def _get_nested(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _set_nested(mapping: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = mapping
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value


def _proposal_change_items(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalizes a proposal into a flat list of single-variable change dicts.

    A proposal_generator either returns the classic single {"variable",
    "value", ...} shape (every existing generator: _generate_asset_proposal,
    generate_deterministic_asset_proposal, the accessibility guards) or, when
    the reflection prompt allowed it (goal.yaml one_variable_only: false), a
    {"asset": ..., "changes": [...]} shape with one to
    MAX_CHANGES_PER_ASSET_PER_CYCLE entries. Both normalize to this same list
    shape so run_hermes_reflection only needs one code path to apply/log/
    reject changes through -- entries beyond the cap are dropped, not
    applied, regardless of how many the model actually returned.
    """
    changes = proposal.get("changes")
    if not isinstance(changes, list):
        return [proposal]
    asset = proposal.get("asset")
    items: list[dict[str, Any]] = []
    for entry in changes[:MAX_CHANGES_PER_ASSET_PER_CYCLE]:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        item.setdefault("asset", asset)
        items.append(item)
    return items


def _apply_strategy_proposal(
    strategy: dict[str, Any],
    proposal: dict[str, Any],
) -> tuple[dict[str, Any], str, str | None]:
    if "value" not in proposal:
        raise ValueError(
            "Hermes proposal is missing value"
        )

    updated = yaml.safe_load(yaml.safe_dump(strategy))
    variable, asset = _normalize_strategy_proposal(
        strategy,
        proposal,
    )

    target = updated

    if asset:
        target = updated["symbols"][asset]

    _validate_strategy_change(strategy, variable, asset, proposal["value"])
    value = _normalized_strategy_value(variable, proposal["value"])
    _set_nested(target, _variable_path(variable), value)

    return updated, variable, asset


def _normalized_strategy_value(variable: str, value: Any) -> Any:
    if variable not in _INTEGER_STRATEGY_VARIABLES:
        return value
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else value


def _validate_strategy_change(strategy: dict[str, Any], variable: str, asset: str | None, value: Any) -> None:
    candidate = float(value)
    low, high = STRATEGY_VARIABLE_BOUNDS[variable]
    if not low <= candidate <= high:
        raise ValueError(f"{variable} must stay between {low} and {high}")
    current = _get_nested(_resolve_asset_strategy(strategy, asset or ""), _variable_path(variable))
    max_step = STRATEGY_VARIABLE_MAX_STEP[variable]
    if current is not None and abs(candidate - float(current)) > max_step:
        raise ValueError(f"{variable} change exceeds the maximum step {max_step}")
@dataclass
class ReflectionCycleSchedule:
    every_cycles: int
    cycles: int = 0

    def record_cycle(self) -> bool:
        if self.every_cycles <= 0:
            return False
        self.cycles += 1
        if self.cycles < self.every_cycles:
            return False
        self.cycles = 0
        return True


def run_hermes_reflection(
    state_dir: Path,
    env: dict[str, str] | None = None,
    *,
    proposal_generator: Callable[..., tuple[dict[str, Any] | None, dict[str, Any]]] | None = None,
    market_analysis_fetcher: Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any] | None] | None = None,
    writer: str | None = None,
) -> dict[str, Any]:
    from hermes_trading.reflection_supervision import assert_writer_owner
    actor = writer or ("deterministic_worker" if proposal_generator is generate_deterministic_asset_proposal else "external_llm")
    owner_policy = assert_writer_owner(state_dir, actor)
    if owner_policy is not None and owner_policy["owner"] == "external_llm":
        from hermes_trading.reflection_supervision import WriterNotAuthorized
        raise WriterNotAuthorized("configured external supervision requires the global review coordinator")
    from hermes_trading.execution_policy import strategy_is_frozen
    if strategy_is_frozen(_load_yaml(state_dir / "strategy.yaml")):
        return {"status": "frozen", "changes": []}
    generate_proposal = proposal_generator or _generate_asset_proposal
    fetch_market_analysis = market_analysis_fetcher or _fetch_market_analysis
    env = dict(os.environ if env is None else env)
    env["HERMES_REFLECTION_STATE_DIR"] = str(state_dir)
    policy = resolve_reflection_policy(env)
    regime_started_at = load_regime_started_at(state_dir, env)

    strategy_path = state_dir / "strategy.yaml"
    strategy = _load_yaml(strategy_path)
    goal = _load_yaml(state_dir / "goal.yaml")

    all_trades = _load_jsonl(
        state_dir / "trades.jsonl"
    )
    all_decisions = _load_jsonl(
        state_dir / "decisions.jsonl"
    )

    assets = _configured_assets(
        strategy,
        goal,
    )

    if not assets:
        raise ValueError(
            "No configured assets found in strategy.yaml or goal.yaml"
        )

    # Garantit une section d'override pour chaque symbole configuré.
    updated = yaml.safe_load(yaml.safe_dump(strategy))
    symbols = updated.setdefault("symbols", {})

    if not isinstance(symbols, dict):
        raise ValueError(
            "strategy.symbols must be a mapping"
        )

    for asset in assets:
        symbols.setdefault(asset, {})

    reflection_state = _load_reflection_state(
        state_dir
    )

    now = datetime.now(timezone.utc)
    applied_changes = []
    cycle_results = []

    # Pass 1: evaluate every asset's eligibility up front (cheap, no LLM calls)
    # so a systemic problem -- e.g. the ATR payoff gate locking out several
    # assets at once -- can be recognized and fixed together in pass 2,
    # instead of each asset independently rediscovering the same diagnosis on
    # its own cooldown.
    evaluations: dict[str, dict[str, Any]] = {}
    for asset in assets:
        asset_trades = _records_for_asset(all_trades, asset)
        asset_decisions = _records_for_asset(all_decisions, asset)
        asset_state = reflection_state.get(asset, {})
        last_reflection_at = _parse_timestamp(asset_state.get("last_reflection_at"))
        if last_reflection_at is None:
            last_reflection_at = _legacy_reflection_timestamp(state_dir, asset)
        should_reflect, trigger_reasons, metrics = _asset_needs_reflection(
            asset=asset,
            trades=asset_trades,
            decisions=asset_decisions,
            last_reflection_at=last_reflection_at,
            goal=goal,
            policy=policy,
            now=now,
            regime_started_at=regime_started_at,
        )
        evaluations[asset] = {
            "should_reflect": should_reflect,
            "trigger_reasons": trigger_reasons,
            "metrics": metrics,
            "last_reflection_at": last_reflection_at,
            "asset_trades": asset_trades,
            "asset_decisions": asset_decisions,
            # Same regime scope as the score/consecutive_losses metrics above,
            # so the LLM prompt and generate_proposal() judge performance
            # against the current strategy only, not a since-replaced one.
            "scoped_trades": _records_since(asset_trades, regime_started_at),
        }

    def _payoff_locked_out(evaluation: dict[str, Any]) -> bool:
        metrics = evaluation["metrics"]
        return (
            metrics.get("dominant_no_trade_reason") == "expected_payoff_below_costs"
            and int(metrics.get("new_trades", 0)) == 0
        )

    systemic_assets = {asset for asset, evaluation in evaluations.items() if _payoff_locked_out(evaluation)}
    has_qualifying_evidence = any(
        evaluations[asset]["should_reflect"] for asset in systemic_assets
    )

    for asset in assets:
        evaluation = evaluations[asset]
        should_reflect = evaluation["should_reflect"]
        trigger_reasons = evaluation["trigger_reasons"]
        metrics = evaluation["metrics"]
        scoped_trades = evaluation["scoped_trades"]
        asset_decisions = evaluation["asset_decisions"]
        last_reflection_at = evaluation["last_reflection_at"]

        systemic_override = (
            not should_reflect
            and has_qualifying_evidence
            and asset in systemic_assets
            and (last_reflection_at is None or (now - last_reflection_at).total_seconds() >= 3600)
        )

        if not should_reflect and not systemic_override:
            cycle_results.append(
                {
                    "asset": asset,
                    "status": "skipped",
                    "metrics": metrics,
                }
            )
            continue

        effective_strategy = _resolve_asset_strategy(
            updated,
            asset,
        )

        if systemic_override:
            # A sibling asset already earned eligibility this cycle with the
            # same payoff-gate lockout; apply the same accessibility fix here
            # rather than waiting out this asset's own cooldown to rediscover it.
            proposal = _payoff_gate_accessibility_proposal(asset, effective_strategy)
            model_info = {"provider": "systemic_accessibility_guard", "model": None, "errors": []}
            trigger_reasons = trigger_reasons + ["shared payoff-gate lockout diagnosis from a sibling asset"]
            market_analysis_opinion = None
        else:
            try:
                market_analysis_opinion = fetch_market_analysis(
                    asset,
                    _market_analysis_snapshot(effective_strategy, metrics, asset_decisions, scoped_trades),
                    env,
                )
            except Exception as error:
                # A custom market_analysis_fetcher (injected by a caller) is not
                # guaranteed to have _fetch_market_analysis's own try/except --
                # this cycle must proceed regardless of what an advisory,
                # optional opinion source does.
                market_analysis_opinion = {"error": str(error)}
            if market_analysis_opinion is not None:
                _append_market_analysis_log(state_dir, asset, market_analysis_opinion)
            prompt = _build_asset_reflection_prompt(
                asset=asset,
                effective_strategy=effective_strategy,
                goal=goal,
                trades=scoped_trades,
                decisions=asset_decisions,
                trigger_reasons=trigger_reasons,
                metrics=metrics,
                env=env,
                market_analysis_opinion=market_analysis_opinion,
            )

            proposal, model_info = generate_proposal(
                asset,
                prompt,
                env,
                effective_strategy=effective_strategy,
                metrics=metrics,
                trades=scoped_trades,
                goal=goal,
                policy=policy,
            )
            proposal = _no_trade_accessibility_proposal(
                asset,
                effective_strategy,
                proposal,
                metrics,
                policy,
            )

        if proposal is None and model_info.get("outcome") == "no_change" and not model_info.get("errors"):
            no_change = {
                "asset": asset,
                "status": "no_change",
                "triggers": trigger_reasons,
                "metrics": metrics,
                "provider": model_info.get("provider"),
                "model": model_info.get("model"),
                "reason": model_info.get("reason", "review completed without a strategy change"),
            }
            cycle_results.append(no_change)
            _append_hypothesis(
                state_dir,
                {"timestamp": now.isoformat(), "mode": "hermes_asset_no_change", **no_change},
            )
            continue

        if proposal is None:
            cycle_results.append(
                {
                    "asset": asset,
                    "status": "model_failed",
                    "triggers": trigger_reasons,
                    "metrics": metrics,
                    "errors": model_info["errors"],
                }
            )

            _append_hypothesis(
                state_dir,
                {
                    "timestamp": now.isoformat(),
                    "mode": "hermes_asset_failed",
                    "asset": asset,
                    "provider": model_info.get("provider"),
                    "model": model_info.get("model"),
                    "triggers": trigger_reasons,
                    "metrics": metrics,
                    "errors": model_info["errors"],
                },
            )
            continue

        # Normally one item (today's classic single-variable proposal, or an
        # accessibility guard's override); more than one only when goal.yaml
        # set one_variable_only: false and the model returned a "changes"
        # list -- see _proposal_change_items. Every item goes through the
        # exact same apply/log/reject path below, threading `updated`
        # forward so a later item's max-step guard sees an earlier item's
        # change within this same cycle.
        asset_last_change: dict[str, Any] | None = None
        for proposal_item in _proposal_change_items(proposal):
            if (
                _is_training_mode(env)
                and str(proposal_item.get("variable", "")).strip().split(".")[-1] == "position_size_r"
            ):
                # position_size_r only affects RiskEngine.authorize_entry() on the
                # live path; PositionEngine (training/simulated mode) ignores it
                # entirely, so applying this proposal would be a no-op that still
                # burns this asset's reflection budget.
                cycle_results.append(
                    {
                        "asset": asset,
                        "status": "proposal_rejected",
                        "error": "position_size_r has no effect in training/simulated mode",
                        "proposal": proposal_item,
                    }
                )
                _append_hypothesis(
                    state_dir,
                    {
                        "timestamp": now.isoformat(),
                        "mode": "hermes_asset_rejected",
                        "asset": asset,
                        "error": "position_size_r_inert_in_training_mode",
                        "proposal": proposal_item,
                    },
                )
                continue

            try:
                candidate, variable, normalized_asset = (
                    _apply_strategy_proposal(
                        updated,
                        proposal_item,
                    )
                )
            except Exception as exc:
                cycle_results.append(
                    {
                        "asset": asset,
                        "status": "proposal_rejected",
                        "error": str(exc),
                        "proposal": proposal_item,
                    }
                )

                _append_hypothesis(
                    state_dir,
                    {
                        "timestamp": now.isoformat(),
                        "mode": "hermes_asset_rejected",
                        "asset": asset,
                        "error": str(exc),
                        "proposal": proposal_item,
                    },
                )
                continue

            updated = candidate

            change = {
                "asset": normalized_asset,
                "variable": variable,
                "value": proposal_item["value"],
                "reason": proposal_item.get("reason", ""),
                "triggers": trigger_reasons,
                "metrics": metrics,
                "provider": model_info["provider"],
                "model": model_info["model"],
                "market_analysis": market_analysis_opinion,
            }

            applied_changes.append(change)
            asset_last_change = change

            cycle_results.append(
                {
                    "asset": asset,
                    "status": "changed",
                    **change,
                }
            )

        if asset_last_change is not None:
            reflection_state[asset] = {
                "last_reflection_at": now.isoformat(),
                "last_variable": asset_last_change["variable"],
                "last_value": asset_last_change["value"],
                "last_score": metrics["score"],
            }

    if not applied_changes:
        _append_hypothesis(
            state_dir,
            {
                "timestamp": now.isoformat(),
                "mode": "hermes_multi_asset_no_change",
                "results": cycle_results,
            },
        )

        _write_reflection_state(
            state_dir,
            reflection_state,
        )

        return strategy

    updated["version"] = _next_version(
        str(strategy.get("version", "01"))
    )

    trading_mode = env.get("HERMES_TRADING_MODE", "training").strip().lower()
    if trading_mode in {"live", "validation-only", "validation_only"}:
        providers = {str(change.get("provider")) for change in applied_changes if change.get("provider")}
        models = {str(change.get("model")) for change in applied_changes if change.get("model")}
        StrategyRegistry(state_dir).propose_challenger(
            updated,
            based_on_version=str(strategy.get("version", "01")),
            provider=next(iter(providers)) if len(providers) == 1 else "mixed",
            model=next(iter(models)) if len(models) == 1 else "mixed",
            proposed_at=now,
        )
    else:
        _archive_current(
            state_dir,
            strategy,
        )
        _write_yaml(
            strategy_path,
            updated,
        )

    _write_reflection_state(
        state_dir,
        reflection_state,
    )

    for change in applied_changes:
        _append_hypothesis(
            state_dir,
            {
                "timestamp": now.isoformat(),
                "mode": "hermes_asset",
                "strategy_version": updated["version"],
                "changed": change["variable"],
                "after": change["value"],
                "reason": change["reason"],
                "asset": change["asset"],
                "triggers": change["triggers"],
                "metrics": change["metrics"],
                "provider": change["provider"],
                "model": change["model"],
                "market_analysis": change["market_analysis"],
            },
        )

    _append_hypothesis(
        state_dir,
        {
            "timestamp": now.isoformat(),
            "mode": "hermes_multi_asset_cycle",
            "strategy_version": updated["version"],
            "change_count": len(applied_changes),
            "changes": applied_changes,
            "results": cycle_results,
        },
    )

    return updated

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument("--hermes", action="store_true")
    parser.add_argument("--state-dir", default=str(Path(__file__).resolve().parents[1] / "state"))
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.hermes:
        updated = run_hermes_reflection(state_dir)
    else:
        updated = run_fallback_reflection(state_dir)
    print(yaml.safe_dump(updated, sort_keys=False))


if __name__ == "__main__":
    main()
