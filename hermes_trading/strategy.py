from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from hermes_trading.domain import Position, decimal_from
from hermes_trading.market_data import MarketSnapshot


@dataclass(frozen=True)
class StrategySignal:
    action: Literal["enter", "exit", "hold", "no_trade"]
    asset: str
    reason: str
    reference_price: Decimal
    candle_id: str


def evaluate_strategy(
    snapshot: MarketSnapshot, strategy: dict[str, Any], position: Position | None
) -> StrategySignal:
    candle = snapshot.candle
    if candle is None or snapshot.availability != "available":
        return StrategySignal("no_trade", snapshot.asset, snapshot.reason, Decimal("0"), "")

    candle_id = snapshot.candle_identity.candle_id
    if position is not None:
        if candle.low <= position.stop_price:
            return StrategySignal("exit", snapshot.asset, "stop_loss", position.stop_price, candle_id)
        exit_rule = strategy.get("exit")
        if isinstance(exit_rule, dict) and _rsi_matches(snapshot, exit_rule, above=True):
            return StrategySignal("exit", snapshot.asset, "explicit_exit", candle.close, candle_id)
        return StrategySignal("hold", snapshot.asset, "position_open", candle.close, candle_id)

    entry = strategy.get("entry")
    if not isinstance(entry, dict):
        return StrategySignal("no_trade", snapshot.asset, "entry_not_configured", candle.close, candle_id)
    if entry.get("indicator") != "rsi" or entry.get("direction", "long") != "long":
        return StrategySignal("no_trade", snapshot.asset, "entry_not_supported", candle.close, candle_id)
    if _rsi_matches(snapshot, entry, above=False):
        return StrategySignal("enter", snapshot.asset, "rsi_entry", candle.close, candle_id)
    return StrategySignal("no_trade", snapshot.asset, "entry_condition_not_met", candle.close, candle_id)


def _rsi_matches(snapshot: MarketSnapshot, rule: dict[str, Any], *, above: bool) -> bool:
    threshold = _decimal_setting(rule.get("threshold", "30"), "RSI threshold")
    closes = (snapshot.price or {}).get("closes")
    if not isinstance(closes, (list, tuple)) or len(closes) < 15:
        return False
    values = [_decimal_setting(value, "close") for value in closes]
    recent = values[-15:]
    changes = [current - previous for previous, current in zip(recent, recent[1:])]
    gains = sum((max(change, Decimal("0")) for change in changes), Decimal("0"))
    losses = sum((-min(change, Decimal("0")) for change in changes), Decimal("0"))
    rsi = Decimal("100") if losses == 0 else Decimal("100") - Decimal("100") / (Decimal("1") + gains / losses)
    return rsi >= threshold if above else rsi <= threshold


def _decimal_setting(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be a decimal value, not a binary float or boolean")
    try:
        result = decimal_from(value)  # type: ignore[arg-type]
    except Exception as error:
        raise ValueError(f"{name} must be a decimal value") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


__all__ = ["StrategySignal", "evaluate_strategy"]
