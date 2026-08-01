from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from hermes_trading.domain import Position, decimal_from
from hermes_trading.market_data import MarketSnapshot, timeframe_interval


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
    if candle is None:
        return StrategySignal("no_trade", snapshot.asset, snapshot.reason, Decimal("0"), "")
    if snapshot.availability != "available":
        return StrategySignal("no_trade", snapshot.asset, snapshot.reason, candle.close, snapshot.candle_identity.candle_id)

    candle_id = snapshot.candle_identity.candle_id
    if position is not None:
        if candle.low <= position.stop_price:
            return StrategySignal("exit", snapshot.asset, "stop_loss", position.stop_price, candle_id)
        exit_rule = strategy.get("exit")
        if isinstance(exit_rule, dict):
            take_profit_pct = _optional_positive_setting(exit_rule.get("take_profit_pct"))
            if take_profit_pct is not None:
                take_profit = position.entry_price * (Decimal("1") + take_profit_pct / Decimal("100"))
                if candle.high >= take_profit:
                    return StrategySignal("exit", snapshot.asset, "take_profit", take_profit, candle_id)
            take_profit_atr = _optional_positive_setting(exit_rule.get("take_profit_atr"))
            if take_profit_atr is not None:
                atr = _atr(snapshot)
                if atr is not None:
                    take_profit = position.entry_price + atr * take_profit_atr
                    if candle.high >= take_profit:
                        return StrategySignal("exit", snapshot.asset, "take_profit_atr", take_profit, candle_id)
            max_bars = _optional_positive_setting(exit_rule.get("max_bars"))
            if max_bars is not None and _bars_held(position.opened_at, candle.close_timestamp, snapshot.timeframe) >= int(max_bars):
                return StrategySignal("exit", snapshot.asset, "max_bars", candle.close, candle_id)
        if isinstance(exit_rule, dict) and _rsi_matches(snapshot, exit_rule, above=True):
            return StrategySignal("exit", snapshot.asset, "explicit_exit", candle.close, candle_id)
        return StrategySignal("hold", snapshot.asset, "position_open", candle.close, candle_id)

    entry = strategy.get("entry")
    if not isinstance(entry, dict):
        return StrategySignal("no_trade", snapshot.asset, "entry_not_configured", candle.close, candle_id)
    if entry.get("direction", "long") != "long":
        return StrategySignal("no_trade", snapshot.asset, "entry_not_supported", candle.close, candle_id)
    indicator = entry.get("indicator")
    if indicator == "rsi" and _rsi_matches(snapshot, entry, above=False):
        return StrategySignal("enter", snapshot.asset, "rsi_entry", candle.close, candle_id)
    if indicator == "rsi_recovery" and _rsi_recovery_matches(snapshot, entry):
        return StrategySignal("enter", snapshot.asset, "rsi_recovery", candle.close, candle_id)
    if indicator not in {"rsi", "rsi_recovery"}:
        return StrategySignal("no_trade", snapshot.asset, "entry_not_supported", candle.close, candle_id)
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


def _rsi_recovery_matches(snapshot: MarketSnapshot, rule: dict[str, Any]) -> bool:
    closes = (snapshot.price or {}).get("closes")
    if not isinstance(closes, (list, tuple)) or len(closes) < 16:
        return False
    values = [_decimal_setting(value, "close") for value in closes]
    threshold = _decimal_setting(rule.get("threshold", "25"), "RSI threshold")
    previous = _rsi(values[-16:-1])
    current = _rsi(values[-15:])
    recovered = previous <= threshold and current > threshold and values[-1] > values[-2]
    if not recovered:
        return False
    if not bool(rule.get("require_ema_rising", False)):
        ema_rising = True
    else:
        period = int(_decimal_setting(rule.get("trend_ema_period", "200"), "trend_ema_period"))
        if period < 2 or len(values) < period + 1:
            return False
        previous_ema = _ema(values[-(period + 1):-1], period)
        current_ema = _ema(values[-period:], period)
        ema_rising = current_ema > previous_ema
    if not ema_rising:
        return False
    max_adx = _optional_positive_setting(rule.get("max_adx"))
    return max_adx is None or (_adx(snapshot) is not None and _adx(snapshot) <= max_adx)


def _rsi(values: list[Decimal]) -> Decimal:
    changes = [current - previous for previous, current in zip(values, values[1:])]
    gains = sum((max(change, Decimal("0")) for change in changes), Decimal("0"))
    losses = sum((-min(change, Decimal("0")) for change in changes), Decimal("0"))
    return Decimal("100") if losses == 0 else Decimal("100") - Decimal("100") / (Decimal("1") + gains / losses)


def _ema(values: list[Decimal], period: int) -> Decimal:
    if len(values) != period:
        raise ValueError("EMA requires exactly one full period")
    multiplier = Decimal("2") / Decimal(period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def _ohlc(snapshot: MarketSnapshot) -> list[tuple[Decimal, Decimal, Decimal]]:
    candles = (snapshot.price or {}).get("candles")
    if not isinstance(candles, (list, tuple)):
        return []
    result: list[tuple[Decimal, Decimal, Decimal]] = []
    for candle in candles:
        if not isinstance(candle, dict):
            return []
        try:
            result.append((_decimal_setting(candle["high"], "high"), _decimal_setting(candle["low"], "low"), _decimal_setting(candle["close"], "close")))
        except (KeyError, ValueError):
            return []
    return result


def _atr(snapshot: MarketSnapshot, period: int = 14) -> Decimal | None:
    candles = _ohlc(snapshot)
    if len(candles) < period + 1:
        return None
    recent = candles[-(period + 1):]
    ranges = [max(high - low, abs(high - previous_close), abs(low - previous_close)) for (_, _, previous_close), (high, low, _) in zip(recent, recent[1:])]
    return sum(ranges, Decimal("0")) / Decimal(period)


def _adx(snapshot: MarketSnapshot, period: int = 14) -> Decimal | None:
    candles = _ohlc(snapshot)
    if len(candles) < period * 2 + 1:
        return None
    plus_dm: list[Decimal] = []
    minus_dm: list[Decimal] = []
    true_ranges: list[Decimal] = []
    for (previous_high, previous_low, previous_close), (high, low, _) in zip(candles, candles[1:]):
        up_move, down_move = high - previous_high, previous_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else Decimal("0"))
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else Decimal("0"))
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    dx: list[Decimal] = []
    for index in range(period - 1, len(true_ranges)):
        tr = sum(true_ranges[index - period + 1:index + 1], Decimal("0"))
        if tr == 0:
            continue
        plus = Decimal("100") * sum(plus_dm[index - period + 1:index + 1], Decimal("0")) / tr
        minus = Decimal("100") * sum(minus_dm[index - period + 1:index + 1], Decimal("0")) / tr
        denominator = plus + minus
        if denominator > 0:
            dx.append(Decimal("100") * abs(plus - minus) / denominator)
    return sum(dx[-period:], Decimal("0")) / Decimal(period) if len(dx) >= period else None


def _optional_positive_setting(value: object) -> Decimal | None:
    if value is None:
        return None
    result = _decimal_setting(value, "exit setting")
    if result <= 0:
        raise ValueError("exit settings must be positive")
    return result


def _bars_held(opened_at: str, close_timestamp: datetime, timeframe: str) -> int:
    opened = datetime.fromisoformat(opened_at)
    return max(0, int((close_timestamp - opened).total_seconds() // timeframe_interval(timeframe).total_seconds()))


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
