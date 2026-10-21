"""Isolated fixed-quantity spot/perpetual research accounting; no order access."""
import math

HOUR = 3600000


def simulate(spot, perp, mark, funding, start, end, *, initial=10000.,
             spot_fee=.001, future_fee=.00055, slip=.001, pessimistic=False):
    """Bars map millisecond timestamps to [open, high, low, close].

    Unsafe paths retain hypothetical terminal results strictly for diagnosis.
    They must never be treated as executable returns by a screening caller.
    """
    if end <= start or start % HOUR or end % HOUR or initial <= 0:
        raise ValueError('Invalid interval or capital')
    if not all(math.isfinite(x) and 0 <= x < 1 for x in (spot_fee,future_fee,slip)):
        raise ValueError('Invalid costs')
    for t in range(start,end+1,HOUR):
        for data in (spot,perp,mark):
            if t not in data:
                raise ValueError(f'Missing price at {t}')
            o,h,l,c=data[t]
            if not all(math.isfinite(x) and x>0 for x in (o,h,l,c)) or not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError('Invalid OHLC')
    s0=spot[start][0]*(1+slip)
    f0=perp[start][0]*(1-slip)
    q=initial/(s0*(2+spot_fee)+f0*future_fee)
    collateral=q*s0
    funding_total=0.
    entry_fees=q*(s0*spot_fee+f0*future_fee)
    min_margin=float('inf'); first_failure=None
    entry_equity=(collateral+q*spot[start][0]*(1-slip)*(1-spot_fee)
                  +q*(f0-perp[start][0]*(1+slip)*(1+future_fee)))
    peak=initial; drawdown=max(0.,1-entry_equity/initial); path=[[start,entry_equity,'entry_liquidation_value']]
    for t in range(start,end,HOUR):
        payment=0.
        if start<t<end and t in funding:
            rate=funding[t]
            if not math.isfinite(rate):
                raise ValueError('Invalid funding')
            reference=mark[t][0]
            if pessimistic:
                reference=mark[t][2] if rate>=0 else mark[t][1]
            payment=q*rate*reference
        high=mark[t][1]
        # Positive credit is unavailable until after the conservative stress check.
        margin=collateral+min(payment,0)+q*(f0-high)-q*high*(.10+future_fee)
        min_margin=min(min_margin,margin)
        if margin<=0 and first_failure is None:
            first_failure=t
        collateral+=payment;funding_total+=payment
        equity=(collateral+q*spot[t][3]*(1-slip)*(1-spot_fee)
                +q*(f0-perp[t][3]*(1+slip)*(1+future_fee)))
        peak=max(peak,equity);drawdown=max(drawdown,1-equity/peak)
        path.append([t+HOUR,equity,'hour_close'])
    sx=spot[end][0]*(1-slip);fx=perp[end][0]*(1+slip)
    exit_margin=collateral+q*(f0-mark[end][0])-q*mark[end][0]*(.10+future_fee)
    min_margin=min(min_margin,exit_margin)
    if exit_margin<=0 and first_failure is None:
        first_failure=end
    final=collateral+q*sx*(1-spot_fee)+q*(f0-fx*(1+future_fee))
    drawdown=max(drawdown,1-final/max(peak,final))
    path.append([end,final,'exit_open_fill'])
    return {'initial_equity':initial,'final_equity':final,'net_return':final/initial-1,
            'quantity':q,'funding':funding_total,
            'fees':entry_fees+q*(sx*spot_fee+fx*future_fee),
            'hedged_price_pnl_after_slippage':q*((sx-s0)+(f0-fx)),
            'max_drawdown':drawdown,'minimum_margin_surplus':min_margin,
            'unsafe':first_failure is not None,'first_margin_failure':first_failure,
            'hypothetical_terminal_if_unsafe':first_failure is not None,'equity':path}
