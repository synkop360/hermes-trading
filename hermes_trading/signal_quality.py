"""Causal event-study helpers; no trading or portfolio simulation."""
from bisect import bisect_right
from datetime import datetime, timedelta
import numpy as np


def outcome(candles, index, horizon_bars, fee=.008, slippage=.001):
    if horizon_bars < 1 or index < 0 or index+horizon_bars >= len(candles):
        return None
    window = candles[index:index+horizon_bars+1]
    if any(b.open_timestamp != a.close_timestamp for a,b in zip(window,window[1:])):
        return None
    future = window[1:]
    entry, exit_price = float(future[0].open), float(future[-1].close)
    return {'gross_return': exit_price/entry-1,
            'net_return': exit_price*(1-slippage)*(1-fee)/(entry*(1+slippage)*(1+fee))-1,
            'favorable_excursion': max(0., max(float(c.high) for c in future)/entry-1),
            'adverse_excursion': min(0., min(float(c.low) for c in future)/entry-1)}


def spaced_indices(candles, indices, hours):
    result, previous = [], None
    for index in indices:
        when = candles[index].close_timestamp
        if previous is None or when-previous >= timedelta(hours=hours):
            result.append(index)
            previous = when
    return result


def preceding_control(indices, index, minimum_distance=1):
    position = bisect_right(indices,index-minimum_distance)-1
    return indices[position] if position >= 0 else None


def paired_interval(rows, start, end):
    """Two-calendar-week moving-block bootstrap, all assets clustered together."""
    matched = [r for r in rows if r.get('control_gross_return') is not None]
    monday = start.date()-timedelta(days=start.weekday())
    weeks = ((end-timedelta(microseconds=1)).date()-monday).days//7+1
    if len(matched)<5 or weeks<4:
        return None
    sums, counts = np.zeros(weeks), np.zeros(weeks)
    for r in matched:
        week = (datetime.fromisoformat(r['signal_at']).date()-monday).days//7
        sums[week] += r['gross_return']-r['control_gross_return']
        counts[week] += 1
    if np.count_nonzero(counts)<4:
        return None
    rng = np.random.default_rng(20260913)
    values = []
    for _ in range(2000):
        starts = rng.integers(0,weeks-1,size=(weeks+1)//2)
        sample = np.column_stack((starts,starts+1)).ravel()[:weeks]
        count = counts[sample].sum()
        if count:
            values.append(float(sums[sample].sum()/count))
    return list(map(float,np.quantile(values,[.025,.975]))) if values else None


def aggregate(rows, start, end):
    if not rows:
        return {'count':0}
    matched = [r for r in rows if r.get('control_gross_return') is not None]
    result = {'count':len(rows), 'matched':len(matched),
              'unique_controls':len({(r['asset'],r['control_at']) for r in matched}),
              'net_win_rate':sum(r['net_return']>0 for r in rows)/len(rows)}
    for key in ('gross_return','net_return','favorable_excursion','adverse_excursion'):
        result['mean_'+key] = float(np.mean([r[key] for r in rows]))
        result['median_'+key] = float(np.median([r[key] for r in rows]))
    if matched:
        result['matched_calendar_weeks'] = len({datetime.fromisoformat(r['signal_at']).isocalendar()[:2] for r in matched})
        result['paired_gross_difference'] = float(np.mean([r['gross_return']-r['control_gross_return'] for r in matched]))
        result['paired_net_difference'] = float(np.mean([r['net_return']-r['control_net_return'] for r in matched]))
        result['paired_gross_difference_interval_95'] = paired_interval(rows,start,end)
    return result
