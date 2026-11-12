"""Frozen daily breakout event-study rules; not a live strategy module."""
from datetime import datetime,timedelta
from decimal import Decimal as D
import numpy as np


def features(candles,index):
    if index<104 or index>=len(candles):return None
    history=candles[index-104:index+1]
    if any(b.open_timestamp!=a.close_timestamp or a.close_timestamp-a.open_timestamp!=timedelta(days=1) for a,b in zip(history,history[1:])):
        return None
    if history[-1].close_timestamp-history[-1].open_timestamp!=timedelta(days=1):return None
    closes=[c.close for c in history]
    sma=sum(closes[-100:])/100
    earlier_sma=sum(closes[-105:-5])/100
    trend=closes[-1]>sma and sma>earlier_sma
    breakout=closes[-1]>max(c.high for c in history[-21:-1])
    atr=sum(max(b.high-b.low,abs(b.high-a.close),abs(b.low-a.close)) for a,b in zip(history[-15:-1],history[-14:]))/14
    fraction=atr/closes[-1]
    atr_band=sum(fraction>=x for x in (D('.01'),D('.02'),D('.04')))
    previous_return=closes[-1]/closes[-91]-1
    regime='rising' if previous_return>D('.10') else 'falling' if previous_return<D('-.10') else 'sideways'
    return {'signal':bool(trend and breakout),'trend':bool(trend),'breakout':bool(breakout),
            'atr_band':atr_band,'regime':regime,'atr_fraction':float(fraction)}


def quarter(when):
    return when.year,(when.month-1)//3+1


def quarter_interval(rows,metric):
    selected=[r for r in rows if r.get(metric) is not None]
    keys=sorted({quarter(datetime.fromisoformat(r['signal_at'])) for r in selected})
    if len(selected)<20 or len(keys)<6:return None
    totals=np.zeros(len(keys));counts=np.zeros(len(keys));lookup={key:i for i,key in enumerate(keys)}
    for r in selected:
        i=lookup[quarter(datetime.fromisoformat(r['signal_at']))]
        totals[i]+=r[metric];counts[i]+=1
    samples=np.random.default_rng(20260914).integers(0,len(keys),size=(2000,len(keys)))
    estimates=totals[samples].sum(axis=1)/counts[samples].sum(axis=1)
    return [float(x) for x in np.quantile(estimates,[.025,.975])]


def summarize(rows):
    if not rows:return {'count':0,'matched':0}
    matched=[r for r in rows if r.get('paired_difference') is not None]
    result={'count':len(rows),'matched':len(matched),
            'unique_controls':len({(r['asset'],r['control_at']) for r in matched}),
            'occupied_quarters':len({quarter(datetime.fromisoformat(r['signal_at'])) for r in rows}),
            'matched_quarters':len({quarter(datetime.fromisoformat(r['signal_at'])) for r in matched}),
            'net_win_rate':sum(r['net_return']>0 for r in rows)/len(rows)}
    for key in ('gross_return','net_return','favorable_excursion','adverse_excursion'):
        result['mean_'+key]=float(np.mean([r[key] for r in rows]))
        result['median_'+key]=float(np.median([r[key] for r in rows]))
    result['mean_net_interval_95']=quarter_interval(rows,'net_return')
    result['paired_difference']=float(np.mean([r['paired_difference'] for r in matched])) if matched else None
    result['paired_difference_interval_95']=quarter_interval(rows,'paired_difference')
    return result


def verdict(results):
    checks=[]
    for name in ('evaluation','final_check'):
        period=results[name];r=period['primary']['7']
        negative=r.get('mean_net_return',0)<=0 if r['count'] else False
        negative=negative or (r.get('paired_difference') is not None and r['paired_difference']<=0)
        enough=r['count']>=30 and r['matched']>=20 and r.get('matched_quarters',0)>=6
        intervals=[r.get('mean_net_interval_95'),r.get('paired_difference_interval_95')]
        supported=all(value is not None and value[0]>0 for value in intervals)
        assets_positive=all(period['per_asset'][asset]['7'].get('mean_net_return',0)>0 for asset in ('BTC/USDT','ETH/USDT'))
        status='reject' if negative else 'pass' if enough and supported and assets_positive else 'inconclusive'
        checks.append({'period':name,'status':status,'sufficient_samples':enough,'positive_interval_bounds':supported,'both_assets_positive':assets_positive})
    status='reject' if any(c['status']=='reject' for c in checks) else 'pass' if all(c['status']=='pass' for c in checks) else 'inconclusive'
    return {'status':status,'checks':checks,'next_action':'propose executable backtest only' if status=='pass' else 'do not advance this version'}
