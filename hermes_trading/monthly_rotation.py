"""Isolated monthly rotation research; never imported by the live strategy."""
from datetime import timedelta


def choose(data, index):
    if index < 91:
        return None
    scores = {}
    for asset in ('BTC', 'ETH'):
        history = data[asset][index-91:index]
        if len(history) != 91 or any(b.open_timestamp != a.close_timestamp for a,b in zip(history,history[1:])):
            return None
        scores[asset] = float(history[-1].close/history[0].close)-1
    best = max(('BTC','ETH'), key=lambda asset:scores[asset])
    return best if scores[best] > 0 else None


def simulate(data, start, end, fee=.008, slippage=.001, initial=10000.):
    btc, eth = data['BTC'], data['ETH']
    if len(btc) != len(eth) or not btc:
        raise ValueError('Unaligned assets')
    for i,(a,b) in enumerate(zip(btc,eth)):
        if a.open_timestamp != b.open_timestamp or a.close_timestamp != b.close_timestamp:
            raise ValueError('Unaligned timestamps')
        if a.close_timestamp-a.open_timestamp != timedelta(days=1):
            raise ValueError('Not daily data')
        if i and btc[i-1].close_timestamp != a.open_timestamp:
            raise ValueError('Missing daily bar')
    indices=[i for i,c in enumerate(btc) if start <= c.open_timestamp < end]
    if not indices or btc[indices[0]].open_timestamp != start or btc[indices[-1]].close_timestamp != end:
        raise ValueError('Incomplete evaluation period')
    cash=initial; quantity=0.; asset=None; entry=None; trades=[]; curve=[]
    peak=initial; drawdown=0.; active_months=set()
    sell_factor=(1-slippage)*(1-fee)
    def close_position(price, when, reason):
        nonlocal cash,quantity,asset,entry
        proceeds=quantity*price*sell_factor
        trades.append({**entry,'exit_at':when.isoformat(),'exit_reason':reason,'exit_proceeds':proceeds,'pnl':proceeds-entry['cost'],'net_return':proceeds/entry['cost']-1})
        cash+=proceeds; quantity=0.; asset=None; entry=None
    for i in indices:
        candle=btc[i]
        if candle.open_timestamp.day == 1:
            target=choose(data,i)
            if target != asset:
                if asset is not None:
                    close_position(float(data[asset][i].open),candle.open_timestamp,'rotation')
                if target is not None:
                    asset=target
                    price=float(data[asset][i].open)
                    quantity=cash/(price*(1+slippage)*(1+fee))
                    entry={'asset':asset,'entry_at':candle.open_timestamp.isoformat(),'cost':cash,'quantity':quantity}
                    cash=0.
        if asset is not None:
            active_months.add(candle.open_timestamp.strftime('%Y-%m'))
        equity=cash+(quantity*float(data[asset][i].close)*sell_factor if asset else 0)
        peak=max(peak,equity);drawdown=max(drawdown,1-equity/peak)
        curve.append({'at':candle.close_timestamp.isoformat(),'equity':equity,'asset':asset})
    if asset is not None:
        close_position(float(data[asset][indices[-1]].close),end,'end_of_period')
    annual={};prior=initial
    for year in sorted({btc[i].open_timestamp.year for i in indices}):
        values=[r['equity'] for i,r in zip(indices,curve) if btc[i].open_timestamp.year==year]
        annual[str(year)]=values[-1]/prior-1;prior=values[-1]
    normal=[t for t in trades if t['exit_reason']=='rotation']
    return {'initial_equity':initial,'final_equity':cash,'net_return':cash/initial-1,'max_drawdown':drawdown,
            'active_months':len(active_months),'completed_holdings':len(normal),'forced_end_holdings':len(trades)-len(normal),
            'win_rate_completed':sum(t['pnl']>0 for t in normal)/len(normal) if normal else None,
            'annual_returns':annual,'trades':trades,'daily_equity':curve}


def benchmark(data,start,end,weights,fee=.008,slippage=.001,initial=10000.):
    bars={a:[c for c in data[a] if start<=c.open_timestamp<end] for a in weights}
    quantities={a:initial*w/(float(bars[a][0].open)*(1+slippage)*(1+fee)) for a,w in weights.items()}
    peak=initial;dd=0.;equity=initial
    for i in range(len(next(iter(bars.values())))):
        equity=sum(quantities[a]*float(bars[a][i].close)*(1-slippage)*(1-fee) for a in weights)
        peak=max(peak,equity);dd=max(dd,1-equity/peak)
    return {'net_return':equity/initial-1,'max_drawdown':dd}
