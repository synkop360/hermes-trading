"""Price policy shared by simulation and exchange validation.

Fees are conservative published spot tier-one assumptions, not account quotes.
ATR is measured from closed history; targets are frozen when a position opens.
"""
from decimal import Decimal

from hermes_trading.strategy import _atr, _decimal_setting

def initial_stop_price(snapshot, strategy, fill_price: Decimal) -> Decimal:
    rule = strategy.get('exit', {})
    if 'stop_atr' in rule:
        atr = _atr(snapshot)
        multiple = _decimal_setting(rule['stop_atr'], 'stop_atr')
        if atr is None or multiple <= 0:
            raise ValueError('stop_atr requires positive multiplier and closed OHLC history')
        stop = fill_price - atr * multiple
    else:
        fraction = _decimal_setting(strategy.get('stop_loss_pct'), 'stop_loss_pct') / 100
        stop = fill_price * (1 - fraction)
    if not stop.is_finite() or not 0 < stop < fill_price:
        raise ValueError('initial stop must be positive and below entry')
    return stop


def initial_take_profit_price(snapshot, strategy, fill_price: Decimal) -> Decimal | None:
    rule = strategy.get('exit', {})
    if 'trailing_atr' in rule or 'take_profit_atr' not in rule:
        return None
    atr = _atr(snapshot)
    multiple = _decimal_setting(rule['take_profit_atr'], 'take_profit_atr')
    if atr is None or multiple <= 0:
        raise ValueError('take_profit_atr requires positive multiplier and closed OHLC history')
    return fill_price + atr * multiple


def trailing_stop_price(snapshot, strategy, previous_stop: Decimal) -> Decimal:
    rule = strategy.get('exit', {})
    if 'trailing_atr' not in rule:
        return previous_stop
    multiple = _decimal_setting(rule['trailing_atr'], 'trailing_atr')
    if multiple <= 0:
        raise ValueError('trailing_atr must be positive')
    atr = _atr(snapshot)
    if atr is None:
        return previous_stop
    return max(previous_stop, snapshot.candle.close - atr * multiple)


def strategy_is_frozen(strategy) -> bool:
    research = strategy.get('research', {})
    return isinstance(research, dict) and research.get('frozen') is True
