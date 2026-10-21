"""Received-quote execution for prospective taker-only paper observations."""
from datetime import timedelta
from decimal import Decimal
from hermes_trading.reflection_supervision import _time
from hermes_trading.strategy import _decimal_setting,_is_maker_entry,_is_maker_exit


def require_taker_contract(strategy):
    if _is_maker_entry(strategy.get('entry')) or _is_maker_exit(strategy.get('exit')):
        raise ValueError('prospective maker execution requires separately validated fill evidence')


def require_taker_strategy(strategy):
    require_taker_contract(strategy)
    for override in strategy.get('symbols',{}).values():
        require_taker_contract(override)


def execution_quote(snapshot,at,*,not_before=None,risk_event=False):
    price=snapshot.price or {}
    received=_time(price.get('received_at'))
    # An operational stop is a current control event, not a new chart signal.
    # Its artificial candle cannot require a quote received after the event.
    signal_time=received if risk_event else snapshot.candle.close_timestamp
    if not_before is not None:signal_time=max(signal_time,not_before)
    if received<signal_time:raise ValueError('execution quote precedes its signal')
    if not timedelta(0)<=at-received<=timedelta(minutes=5):
        raise ValueError('execution quote is stale or future-dated')
    bid=_decimal_setting(price.get('bid'),'execution bid');ask=_decimal_setting(price.get('ask'),'execution ask')
    if bid<=0 or ask<=0 or bid>ask:raise ValueError('execution quote is nonpositive or crossed')
    return bid,ask
