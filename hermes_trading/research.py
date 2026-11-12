"""Deterministic, isolated research using production position and signal rules.

Signals from closed bars enter at the following open. Indicators only see closed
history. No reflection, exchange credentials, or live state is used.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from itertools import groupby
import csv

from hermes_trading.market_data import ClosedCandle, MarketSnapshot, timeframe_interval
from hermes_trading.persistence import StateRepository
from hermes_trading.positions import PositionEngine
from hermes_trading.strategy import StrategySignal, evaluate_strategy


def normalize_settings(value):
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {key: normalize_settings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_settings(item) for item in value]
    return value


def conservative_strategy(source):
    result = normalize_settings(deepcopy(source))
    result.setdefault('entry', {})['fee'] = 'taker'
    result.setdefault('exit', {})['fee'] = 'taker'
    result.setdefault('simulation', {}).update(taker_fee_rate='0.008', maker_fee_rate='0.004', slippage_rate='0.001', max_concurrent_positions=2,
        risk_per_trade_pct='0.0025',max_symbol_exposure_pct='0.05',max_total_exposure_pct='0.10',max_order_quote='5')
    result['reflection'] = {'enabled': False}
    result['research'] = {'frozen': True}
    for override in result.get('symbols', {}).values():
        for leg in ('entry', 'exit'):
            if leg in override:
                override[leg]['fee'] = 'taker'
    return result


def symbol_strategy(strategy, asset):
    result = deepcopy(strategy)
    for key, value in strategy.get('symbols', {}).get(asset, {}).items():
        if isinstance(value, dict):
            result.setdefault(key, {}).update(value)
        else:
            result[key] = value
    return result


def load_csv(path, timeframe='15m', start=None, end=None):
    interval = timeframe_interval(timeframe)
    result = []
    with open(path, newline='', encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            opened = datetime.fromisoformat(row['date'])
            if start is not None and opened < start:
                continue
            if end is not None and opened >= end:
                continue
            result.append(ClosedCandle(opened, opened+interval, *(D(row[x]) for x in ('open','high','low','close','volume'))))
    result.sort(key=lambda x: x.open_timestamp)
    if len({c.open_timestamp for c in result}) != len(result):
        raise ValueError('duplicate source candles')
    return result


def aggregate_candles(candles, source_timeframe, target_timeframe):
    source = timeframe_interval(source_timeframe)
    target = timeframe_interval(target_timeframe)
    count = int(target / source)
    if count < 1 or target != source * count:
        raise ValueError('target must be a multiple of source timeframe')
    groups = defaultdict(list)
    for candle in candles:
        groups[int(candle.open_timestamp.timestamp() // target.total_seconds())].append(candle)
    result = []
    for _, group in sorted(groups.items()):
        group.sort(key=lambda x:x.open_timestamp)
        if len(group) != count or group[0].open_timestamp.timestamp() % target.total_seconds() != 0:
            continue
        if any(b.open_timestamp != a.close_timestamp for a,b in zip(group,group[1:])):
            continue
        result.append(ClosedCandle(group[0].open_timestamp, group[-1].close_timestamp, group[0].open, max(c.high for c in group), min(c.low for c in group), group[-1].close, sum((c.volume or D(0) for c in group), D(0))))
    return result


def snapshots(data, timeframe, history_length=300):
    history = defaultdict(list)
    previous = {}
    events = sorted((c.close_timestamp, asset, c) for asset, candles in data.items() for c in candles)
    for _, asset, candle in events:
        if asset in previous and previous[asset] != candle.open_timestamp:
            history[asset].clear()
        previous[asset] = candle.close_timestamp
        history[asset].append({'timestamp': candle.close_timestamp.isoformat(), 'open': candle.open,
                              'high': candle.high, 'low': candle.low, 'close': candle.close, 'volume': candle.volume})
        history[asset] = history[asset][-history_length:]
        yield MarketSnapshot(asset, timeframe, 'available', 'evaluate', 'research_closed_candle', candle,
            {'closes': tuple(c['close'] for c in history[asset]), 'candles': tuple(history[asset])})


def whole_position_stats(closed, active):
    open_keys = {(p.asset,p.opened_at) for p in active}
    grouped = defaultdict(lambda: D(0))
    for p in closed:
        key = p.asset,p.opened_at
        if key not in open_keys:
            grouped[key] += p.realized_pnl
    pnls = list(grouped.values())
    wins, losses = [p for p in pnls if p>0], [p for p in pnls if p<0]
    return {'completed_positions':len(pnls), 'partial_or_full_closed_legs':len(closed), 'win_rate':len(wins)/len(pnls) if pnls else None,
            'net_pnl':str(sum(pnls,D(0))), 'profit_factor':float(sum(wins,D(0))/-sum(losses,D(0))) if losses else None,
            'expectancy':str(sum(pnls,D(0))/len(pnls)) if pnls else None}


def rolling_90(curve):
    # Daily endpoints avoid presenting highly overlapping intraday samples as trials.
    daily = {}
    for when,equity in curve:
        daily[when.date()] = equity
    returns = []
    for day, equity in sorted(daily.items()):
        before = daily.get(day-timedelta(days=90))
        if before is not None and before > 0:
            returns.append(float(equity/before-1))
    return {'windows':len(returns),'minimum_return':min(returns) if returns else None,'maximum_return':max(returns) if returns else None,
            'mean_return':sum(returns)/len(returns) if returns else None,'doubling_frequency':sum(r>=1 for r in returns)/len(returns) if returns else None}


class _NoArtifacts:
    def project_pending_item(self, state, item):
        pass


def run_backtest(data, strategy, timeframe, start=None, end=None, initial_equity=D('50'), drawdown_limit=D('.08'), *, policy=None):
    state = StateRepository.open(':memory:')
    state.migrate()
    engine = PositionEngine(state, _NoArtifacts()) if policy is None else policy.engine(state, _NoArtifacts())
    strategy = deepcopy(strategy)
    configs = {asset:symbol_strategy(strategy,asset) for asset in data}
    pending, latest, closed, curve = {}, {}, [], []
    cash = peak = initial_equity
    minimum_cash, max_open, max_drawdown = cash, 0, D(0)
    halted = False
    halt_at = None
    reasons = defaultdict(int)
    total_fees = D(0)
    pending_exits = {}

    def equity():
        # Liquidation value includes expected exit costs and entry fees already in cash.
        return cash + sum((p.quantity*latest[p.asset]*D('.999')*D('.992') for p in state.active_positions()), D(0))

    def execute(snapshot, config, signal):
        nonlocal cash,total_fees,minimum_cash,max_open
        result = engine.process_candle(snapshot, config, signal_override=signal, recover_pending=False)
        reasons[result.reason] += 1
        closed.extend(result.closed_positions)
        for order in result.orders:
            value = order.filled_quantity*order.average_fill_price
            cash += value-order.fees if order.side == 'sell' else -value-order.fees
            total_fees += order.fees
        minimum_cash = min(minimum_cash,cash)
        max_open = max(max_open,len(state.active_positions()))

    try:
        for when, batch_iter in groupby(snapshots(data,timeframe), key=lambda s:s.candle.close_timestamp):
            batch = list(batch_iter)
            entered_this_bar = set()
            if end is not None and when > end:
                break
            if start is not None and when <= start:
                continue
            if not curve:
                curve.append((batch[0].candle.open_timestamp,initial_equity))
            # All simultaneous opens are marked before sizing any entry.
            for snapshot in batch:
                latest[snapshot.asset] = snapshot.candle.open
            if policy is not None and not halted:
                policy.prepare_batch(batch, pending, configs, engine, cash, equity())
            for snapshot in batch:
                asset, candle = snapshot.asset,snapshot.candle
                previous = pending.pop(asset,None)
                deferred_exit = pending_exits.pop(asset, None)
                if not halted and deferred_exit is not None and state.open_position(asset) is not None:
                    opening = replace(candle, close_timestamp=candle.open_timestamp+timedelta(microseconds=1), high=candle.open, low=candle.open, close=candle.open)
                    forced = replace(snapshot, candle=opening)
                    position = state.open_position(asset)
                    signal = replace(deferred_exit, reference_price=candle.open, candle_id=forced.candle_identity.candle_id)
                    if candle.open <= position.stop_price:
                        signal = replace(signal, reason='stop_loss')
                    elif policy is not None:
                        signal = policy.exit_signal(forced, position, configs[asset], signal, False)
                    execute(forced, configs[asset], signal)
                    continue
                if halted and state.open_position(asset) is not None:
                    opening = replace(candle, close_timestamp=candle.open_timestamp+timedelta(microseconds=1), high=candle.open, low=candle.open, close=candle.open)
                    forced = replace(snapshot, candle=opening)
                    execute(forced, configs[asset], StrategySignal('exit', asset, 'drawdown_halt', candle.open, forced.candle_identity.candle_id))
                    continue
                if not halted and previous is not None and state.open_position(asset) is None:
                    prior_snapshot, signal = previous
                    if prior_snapshot.candle.close_timestamp != candle.open_timestamp:
                        continue
                    engine.equity = max(D(0),min(equity(),cash/D('.504')))
                    if engine.equity <= 0 or cash < D('1.008'):
                        continue
                    reference = candle.open
                    if policy is not None:
                        if policy.options.get('scalable_cap'):
                            configs[asset]['simulation']['max_order_quote'] = str(policy.order_cap(engine.equity))
                        reference = policy.entry_price(prior_snapshot, candle, configs[asset])
                        if reference is None:
                            continue
                        if policy.options.get('maker'):
                            # Production engine adds configured entry slippage; a resting
                            # limit must fill at its limit, never above it. Keep the
                            # slippage reserve in sizing/cost screening conservative.
                            reference /= 1 + D(configs[asset]['simulation']['slippage_rate'])
                    opening = replace(candle,close_timestamp=candle.open_timestamp+timedelta(microseconds=1),high=reference,low=reference,close=reference,open=reference)
                    execution = replace(prior_snapshot,candle=opening)
                    execute(execution,configs[asset],replace(signal,reference_price=reference,candle_id=execution.candle_identity.candle_id))
                    if state.open_position(asset) is not None:
                        entered_this_bar.add(asset)
            for snapshot in batch:
                latest[snapshot.asset] = snapshot.candle.close
            for snapshot in batch:
                asset = snapshot.asset
                position = state.open_position(asset)
                if position is not None:
                    signal = evaluate_strategy(snapshot,configs[asset],position)
                    if policy is not None:
                        signal = policy.exit_signal(snapshot, position, configs[asset], signal, asset in entered_this_bar)
                        if signal.action == 'exit' and signal.reason not in {'stop_loss','take_profit','take_profit_atr'}:
                            pending_exits[asset] = signal
                            signal = replace(signal, action='hold', reason='exit_queued_next_open')
                    execute(snapshot,configs[asset],signal)
                elif not halted:
                    signal = evaluate_strategy(snapshot,configs[asset],None)
                    if signal.action == 'enter':
                        if policy is None or policy.allow_entry(snapshot):
                            pending[asset] = snapshot,signal
            value = equity()
            peak = max(peak,value)
            drawdown = (peak-value)/peak
            max_drawdown = max(max_drawdown,drawdown)
            if drawdown >= drawdown_limit and not halted:
                halted,halt_at = True,when.isoformat()
                pending.clear()
                # Stop new risk and liquidate existing positions at current marks.
                for snapshot in batch:
                    if state.open_position(snapshot.asset) is not None:
                        forced = replace(snapshot,candle=replace(snapshot.candle,close_timestamp=when+timedelta(microseconds=1)))
                        execute(forced,configs[snapshot.asset],StrategySignal('exit',snapshot.asset,'drawdown_halt',snapshot.candle.close,forced.candle_identity.candle_id))
                value = equity()
                max_drawdown = max(max_drawdown,(peak-value)/peak)
            curve.append((when,value))
        active = state.active_positions()
        stats = whole_position_stats(closed,active)
        value = curve[-1][1] if curve else initial_equity
        days = (curve[-1][0]-curve[0][0]).total_seconds()/86400 if len(curve)>1 else 0
        return {**stats,'ending_equity':str(value),'net_return':float(value/initial_equity-1),'max_drawdown':float(max_drawdown),
                'halted':halted,'halt_at':halt_at,'max_open_positions':max_open,'minimum_cash':str(minimum_cash),
                'open_positions':len(active),'fees':str(total_fees),'days':days,'rolling_90':rolling_90(curve),
                'monthly_cost_sensitivity':{str(cost):float((value-D(str(cost*days/30)))/initial_equity-1) for cost in (0,1,5,10)},
                'reasons':dict(sorted(reasons.items())),
                **({'experiment':policy.summary(closed, active)} if policy is not None else {})}
    finally:
        state.close()
