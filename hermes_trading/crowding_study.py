"""Fixed crowding-reversal research primitives, without trading access."""
import math

HOUR=3600000
DAY=24*HOUR


def direction(daily_funding, decision, price_return):
    """None means incomplete features; zero means valid features without signal."""
    times=[decision-i*DAY for i in range(1,62)]
    if any(t not in daily_funding for t in times):
        return None
    values=[daily_funding[t] for t in times]
    if not all(math.isfinite(v) for v in values+[price_return]):
        raise ValueError('Nonfinite feature')
    signal=values[0];reference=sorted(values[1:])
    if signal>0 and signal>reference[53] and price_return<0:return -1
    if signal<0 and signal<reference[5] and price_return>0:return 1
    return 0


def discrete_return(prices,marks,funding,entry,exit,side,*,fee=.00055,slip=.001):
    """Unit coin P&L normalized by adverse entry notional; discrete funding only.

    Caller must validate funding schedule completeness, including interval changes.
    Hourly mark open approximates the settlement mark; this is not a margin model.
    """
    if side not in (-1,1) or exit<=entry or entry%HOUR or exit%HOUR:
        raise ValueError('Invalid event')
    if not all(math.isfinite(v) and 0<=v<1 for v in (fee,slip)):
        raise ValueError('Invalid cost')
    for t in range(entry,exit+1,HOUR):
        for data in (prices,marks):
            if t not in data or not all(math.isfinite(v) and v>0 for v in data[t]):
                raise ValueError('Missing or invalid held price')
    enter=prices[entry][0]*(1+side*slip)
    leave=prices[exit][0]*(1-side*slip)
    paid=0.
    for t,rate in funding.items():
        if entry<t<exit:
            if not math.isfinite(rate):raise ValueError('Invalid funding')
            paid-=side*rate*marks[t][0]
    price_pnl=side*(leave-enter)
    costs=fee*(enter+leave)
    return {'price_return':price_pnl/enter,'funding_return':paid/enter,
            'fee_return':costs/enter,'net_return':(price_pnl+paid-costs)/enter,
            'entry_fill':enter,'exit_fill':leave}
