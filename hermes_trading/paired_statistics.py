"""Paired calendar statistics; numerical evidence is not profit qualification."""
from datetime import timedelta
import math
import numpy as np

from hermes_trading.reflection_supervision import _now, _time
from hermes_trading.wallet_metrics import COSTS, _decimal

BOOTSTRAP_SEED = 20261010
BOOTSTRAP_RESAMPLES = 10000


def chronological_growth_blocks(daily_equity):
    """Non-overlapping90-day consistency windows, not independent replicates."""
    result=[]
    for begin in range(0,len(daily_equity)-90,90):
        row={'start_day':begin,'end_day':begin+90}
        for index,arm in enumerate(('adaptive','control')):
            left,right=(_decimal(daily_equity[day][index]) for day in (begin,begin+90))
            if left<=0 or right<=0:raise ValueError('positive block endpoint equity required')
            growth=right/left-1
            row[arm+'_growth']=str(growth)
            row[arm+'_positive']=growth>0
        result.append(row)
    return result


def paired_circular_bootstrap(daily_log_changes, *, block_days=7,
                              resamples=BOOTSTRAP_RESAMPLES, seed=BOOTSTRAP_SEED):
    raw = np.asarray(daily_log_changes, dtype=object)
    if raw.ndim != 2 or raw.shape[1] != 2 or raw.shape[0] < 2:
        raise ValueError('aligned adaptive/control daily series required')
    if any(isinstance(value, (bool, np.bool_)) for row in raw.tolist() for value in row):
        raise ValueError('boolean daily changes are invalid')
    if raw.dtype.kind in {'b','c'}:
        raise ValueError('real numeric daily changes required')
    try:
        data = raw.astype(float)
    except (TypeError, ValueError) as error:
        raise ValueError('numeric daily changes required') from error
    n = len(data)
    if not np.isfinite(data).all():
        raise ValueError('finite daily changes required')
    if type(block_days) is not int or not 1 <= block_days < n:
        raise ValueError('block length must be positive and smaller than the series')
    if type(resamples) is not int or not 2 <= resamples <= 100000:
        raise ValueError('bounded resample count required')
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('explicit bounded integer seed required')
    full, remainder = divmod(n, block_days)
    count = full + bool(remainder)
    if count * resamples > 5000000:
        raise ValueError('bootstrap request exceeds the bounded workspace')
    try:
        with np.errstate(over='raise', invalid='raise'):
            data = np.column_stack((data, data[:, 0] - data[:, 1]))
            origins = np.arange(n)
            blocks = sum((data[(origins + offset) % n] for offset in range(block_days)),
                         np.zeros_like(data))
            rng = np.random.Generator(np.random.PCG64(seed))
            starts = rng.integers(0, n, size=(resamples, count))
            draws = blocks[starts[:, :full]].sum(axis=1)
            if remainder:
                short = sum((data[(origins + offset) % n] for offset in range(remainder)),
                            np.zeros_like(data))
                draws += short[starts[:, -1]]
            draws /= n
            observed = data.mean(axis=0)
            lower = np.quantile(draws, .05, axis=0, method='linear')
            spread = draws.std(axis=0, ddof=1)
            average = draws.mean(axis=0)
    except FloatingPointError as error:
        raise ValueError('bootstrap statistics exceed the numeric range') from error
    return {'method':'paired_circular_moving_block', 'days':n, 'block_days':block_days,
            'resamples':resamples, 'seed':seed, 'generator':'PCG64',
            'confidence_convention':'one_sided_95_percentile_lower_5pct_linear',
            'statistical_coverage_validated':False,
            **{name:{'mean_daily_log_growth':float(observed[index]),
                     'lower_95':float(lower[index]), 'bootstrap_mean':float(average[index]),
                     'bootstrap_standard_error':float(spread[index])}
               for index,name in enumerate(('adaptive','control','adaptive_minus_control'))}}


def aligned_calendar_equity(pairs, *, start, days=180,
                            max_gap=timedelta(minutes=5), boundary_tolerance=timedelta(minutes=2),starting_balance='100'):
    """Stream common settled observations; never interpolate a missing boundary."""
    start = _now(start)
    if type(days) is not int or not 1 <= days <= 180:
        raise ValueError('bounded calendar window required')
    if max_gap <= timedelta(0) or not timedelta(0) <= boundary_tolerance < timedelta(days=1):
        raise ValueError('invalid calendar coverage policy')
    end = start + timedelta(days=days)
    boundaries = [start + timedelta(days=day) for day in range(days + 1)]
    daily, stamps = [], []
    last_at, last_values = None, None
    capital=_decimal(starting_balance)
    if capital not in (50,100,150,200):raise ValueError('unsupported primary capital')
    peak = [capital,capital]
    drawdown = [_decimal(0), _decimal(0)]
    observations = estimated = 0

    def close_boundary():
        boundary = boundaries[len(daily)]
        if last_at is None or not boundary-boundary_tolerance <= last_at <= boundary:
            raise ValueError('missing calendar boundary')
        daily.append([str(value) for value in last_values])
        stamps.append(last_at.isoformat())

    for left, right in pairs:
        at = _time(left.get('at'))
        if at != _time(right.get('at')):
            raise ValueError('observations are not aligned')
        if not start-boundary_tolerance <= at <= end:
            continue
        if last_at is not None:
            if at <= last_at:
                raise ValueError('observations must be unique and increasing')
            if at-last_at > max_gap:
                raise ValueError('observation gap')
        while len(daily) <= days and at > boundaries[len(daily)]:
            close_boundary()
        values = []
        for index,row in enumerate((left,right)):
            if row.get('currency') != 'EUR' or row.get('valuation_model') != 'long_spot_net_v1':
                raise ValueError('primary EUR paper valuation required')
            if _decimal(row.get('starting_balance')) != capital or _decimal(row.get('external_flow')) != 0:
                raise ValueError('primary capital or external flow differs')
            if row.get('net_equity') is None or row.get('missing'):
                raise ValueError('unknown net equity or cost coverage')
            value = _decimal(row['net_equity'])
            if value <= 0:
                raise ValueError('nonpositive equity: insolvent')
            costs = row.get('cost_provenance', {})
            if set(costs) != COSTS:
                raise ValueError('complete expense provenance required')
            total = _decimal(0)
            for name,event in costs.items():
                if event.get('quality') not in {'actual','estimated'} or not event.get('source'):
                    raise ValueError('unknown expense provenance')
                amount = _decimal(event.get('amount'))
                if name != 'funding' and amount < 0:
                    raise ValueError('negative operating expense')
                total += amount
                estimated += event['quality'] == 'estimated'
            if total != _decimal(row.get('known_accrued_costs')):
                raise ValueError('expense totals do not reconcile')
            if value != _decimal(row.get('trading_cash')) + _decimal(row.get('open_liquidation_value')) - total:
                raise ValueError('net liquidation equity does not reconcile')
            peak[index] = max(peak[index], value)
            drawdown[index] = max(drawdown[index], (peak[index]-value)/peak[index])
            values.append(value)
        last_at, last_values = at, values
        observations += 1
        if len(daily) <= days and at == boundaries[len(daily)]:
            close_boundary()
    while len(daily) <= days:
        close_boundary()
    try:
        logs = [[math.log(float(_decimal(right[index]))) - math.log(float(_decimal(left[index])))
                 for index in (0,1)] for left,right in zip(daily,daily[1:])]
        if not all(math.isfinite(value) for row in logs for value in row):
            raise ValueError('daily statistics exceed numeric range')
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError('daily statistics exceed numeric range') from error
    return {'daily_equity':daily, 'daily_log_changes':logs, 'actual_boundary_times':stamps,
            'observations':observations, 'estimated_cost_components':estimated,
            'max_drawdown':[str(value) for value in drawdown],
            'window_start':start.isoformat(), 'window_end':end.isoformat()}
