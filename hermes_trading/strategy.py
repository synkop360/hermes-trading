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
            if (
                "take_profit_atr" in exit_rule
                and position.take_profit_price is not None
                and candle.high >= position.take_profit_price
            ):
                # Frozen at entry (positions.py::_entry) so a rolling ATR
                # contraction during the hold can't shrink the target back
                # toward entry price and turn this into a cost-blind exit.
                return StrategySignal("exit", snapshot.asset, "take_profit_atr", position.take_profit_price, candle_id)
            max_bars = _optional_positive_setting(exit_rule.get("max_bars"))
            if max_bars is not None and _bars_held(position.opened_at, candle.close_timestamp, snapshot.timeframe) >= int(max_bars):
                return StrategySignal("exit", snapshot.asset, "max_bars", candle.close, candle_id)
        # Opt-in: with trailing_atr configured, explicit_exit (RSI recovery)
        # otherwise always wins the race against the trailing stop -- it fires
        # the instant RSI clears the threshold + cost floor, which in practice
        # is well before price has moved far enough for a wider trailing band
        # to ever bind, capping every winner at whatever the RSI mean-reversion
        # target gives regardless of trailing_atr's value (confirmed against
        # real trade history: 100% of wins closed via explicit_exit, 0% via a
        # ratcheted stop). Setting trailing_overrides_explicit_exit hands exits
        # entirely to the (continuously ratcheting) stop_price and max_bars
        # instead, letting a winner run past the RSI target -- default is
        # unset/false, preserving today's behavior for every existing caller.
        trailing_overrides_explicit_exit = (
            isinstance(exit_rule, dict)
            and "trailing_atr" in exit_rule
            and bool(exit_rule.get("trailing_overrides_explicit_exit", False))
        )
        if trailing_overrides_explicit_exit:
            # Opt-in refinement: an unconditional override (the default, when
            # this isn't set) buys payoff ratio at the cost of win rate --
            # confirmed on real backtest data (2109 baseline/consultant trades
            # vs. 1051 trailing-exit trades, same historical window): 100% of
            # explicit_exit-enabled wins vs. only ~25% of trailing-only exits
            # end up profitable, because most RSI-recovery bounces are short
            # (explicit_exit was the only mechanism reliably banking one
            # before it reversed). trailing_activation_atr lets explicit_exit
            # keep closing quick, modest bounces normally -- preserving win
            # rate -- and only hands control to the trailing stop once a
            # trade has already moved at least this many ATRs favorably,
            # which is the point a bounce has arguably become a real trend
            # worth letting run. Unset (the default) means immediate
            # override from the first hold cycle, identical to today.
            activation_atr = _optional_positive_setting(exit_rule.get("trailing_activation_atr"))
            if activation_atr is not None:
                atr = _atr(snapshot)
                favorable_move_price = candle.close - position.entry_price
                trailing_overrides_explicit_exit = atr is not None and favorable_move_price >= atr * activation_atr
        # Opt-in: banks profit on a fraction of the position via explicit_exit
        # (preserving win rate) while the remainder keeps riding the
        # ratcheting trailing stop (preserving payoff ratio) -- the two
        # single-dial hybrids above (unconditional override / activation_atr)
        # were confirmed on real 180-day backtest data to be a smooth
        # monotonic trade-off between win rate and payoff ratio with no
        # combined sweet spot; splitting the position structurally instead of
        # picking one governance rule for the whole trade is the mechanism
        # that can actually get both at once. Requires trailing_atr (the
        # remainder needs a trailing stop to ride, or "let it run" is a
        # no-op). position.partial_exit_count tracks whether this specific
        # position has already banked its partial close -- once it has,
        # explicit_exit is permanently disabled for the remainder (mirrors
        # trailing_overrides_explicit_exit's unconditional case), so a
        # position can only partial-close once, not repeatedly nibble itself
        # away on successive RSI-recovery bounces.
        partial_close_pct = None
        if isinstance(exit_rule, dict) and "partial_close_pct" in exit_rule:
            partial_close_pct = _decimal_setting(exit_rule["partial_close_pct"], "partial_close_pct")
            if partial_close_pct <= 0 or partial_close_pct >= 1:
                raise ValueError("partial_close_pct must be greater than zero and less than one")
            if "trailing_atr" not in exit_rule:
                raise ValueError("exit.partial_close_pct requires exit.trailing_atr to be configured")
            if bool(exit_rule.get("trailing_overrides_explicit_exit", False)):
                # A base-config trailing_overrides_explicit_exit: true is
                # checked FIRST, unconditionally, above -- it would
                # permanently suppress explicit_exit/partial_exit from the
                # very first hold cycle, making partial_close_pct silently
                # dead code (confirmed the hard way: a misconfigured
                # deployment scored statistically identical to the plain
                # full-override variant on a real backtest). Fail loudly
                # instead of silently no-op'ing a live deployment --
                # partial_close_pct's own partial_exit_count check below is
                # what's supposed to turn the override on, but only *after*
                # the remainder has already banked once.
                raise ValueError(
                    "exit.partial_close_pct is incompatible with a base "
                    "exit.trailing_overrides_explicit_exit: true -- that "
                    "suppresses explicit_exit before partial_close_pct's own "
                    "partial_exit_count gate ever runs, making it dead code. "
                    "Leave trailing_overrides_explicit_exit unset; "
                    "partial_close_pct enables the override itself, but only "
                    "after the first partial close."
                )
            if position.partial_exit_count > 0:
                trailing_overrides_explicit_exit = True
        if (
            not trailing_overrides_explicit_exit
            and isinstance(exit_rule, dict)
            and _rsi_matches(snapshot, exit_rule, above=True)
        ):
            required_move = _minimum_favorable_move_pct(strategy, exit_rule)
            favorable_move = (candle.close - position.entry_price) / position.entry_price
            if favorable_move >= required_move:
                reason = "partial_exit" if partial_close_pct is not None else "explicit_exit"
                return StrategySignal("exit", snapshot.asset, reason, candle.close, candle_id)
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


def _is_maker_exit(exit_rule: dict[str, Any] | None) -> bool:
    """Whether an ``explicit_exit`` (RSI recovery) close should be priced as a maker fill.

    Defaults to maker: this exit fires opportunistically once RSI recovers, not
    urgently like a stop, so it can reasonably be routed as a limit order. Set
    ``exit.fee: taker`` to opt back into market pricing for this reason only;
    stop_loss/take_profit/max_bars always stay taker regardless of this setting.
    """
    fee = exit_rule.get("fee", "maker") if isinstance(exit_rule, dict) else "maker"
    return str(fee).lower() != "taker"


def _is_maker_entry(entry_rule: dict[str, Any] | None) -> bool:
    """Whether an entry fill should be priced as a maker (limit) order.

    Defaults to taker (today's existing behavior, and the historically
    simulated one) -- set ``entry.fee: maker`` to opt in. Unlike
    ``_is_maker_exit``, there is no "urgent" entry reason that must always
    stay taker, so this is a single toggle rather than a per-signal-reason
    check. This simulation does not model limit-fill risk: a maker entry is
    assumed to fill exactly like a taker one, just at the cheaper fee.
    """
    fee = entry_rule.get("fee", "taker") if isinstance(entry_rule, dict) else "taker"
    return str(fee).lower() == "maker"


def _minimum_favorable_move_pct(strategy: dict[str, Any], exit_rule: dict[str, Any]) -> Decimal:
    """Round-trip transaction cost plus the configured minimum edge, as a price fraction.

    The RSI recovery exit has no price target of its own, so without this floor
    it fires the instant RSI crosses back over the threshold -- often on a bounce
    too small to cover fees and slippage, turning a paper "win" into a realized
    loss once costs are applied.
    """
    simulation = strategy.get("simulation")
    simulation = simulation if isinstance(simulation, dict) else {}
    taker_fee_rate = _decimal_setting(simulation.get("taker_fee_rate", "0.004"), "taker_fee_rate")
    maker_fee_rate = _decimal_setting(simulation.get("maker_fee_rate", "0.0025"), "maker_fee_rate")
    slippage_rate = _decimal_setting(simulation.get("slippage_rate", "0.001"), "slippage_rate")
    minimum_edge_pct = _decimal_setting(simulation.get("minimum_expected_edge_pct", "0.2"), "minimum_expected_edge_pct")
    entry_fee_rate = maker_fee_rate if _is_maker_entry(strategy.get("entry")) else taker_fee_rate
    exit_fee_rate = maker_fee_rate if _is_maker_exit(exit_rule) else taker_fee_rate
    round_trip_cost = entry_fee_rate + slippage_rate + exit_fee_rate + slippage_rate
    return round_trip_cost + minimum_edge_pct / Decimal("100")


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
