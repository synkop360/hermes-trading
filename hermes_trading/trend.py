"""Fixed, causal trend/pullback entry; no fitting or external services."""
from decimal import Decimal


def trend_pullback_matches(snapshot, rule):
    values = [Decimal(str(x)) for x in (snapshot.price or {}).get('closes', ())]
    fast, slow = int(rule.get('fast_period', 20)), int(rule.get('slow_period', 100))
    if fast < 2 or slow <= fast:
        raise ValueError('trend periods require 2 <= fast < slow')
    if len(values) < slow + 1:
        return False
    fast_now = sum(values[-fast:]) / fast
    fast_before = sum(values[-fast-1:-1]) / fast
    slow_now = sum(values[-slow:]) / slow
    slow_before = sum(values[-slow-1:-1]) / slow
    tolerance = Decimal(str(rule.get('pullback_fraction', '0.015')))
    return (slow_now > slow_before and fast_now > slow_now
            and values[-1] > slow_now and values[-2] <= fast_before
            and values[-2] >= fast_before * (1-tolerance)
            and values[-1] > fast_now and values[-1] > values[-2])
