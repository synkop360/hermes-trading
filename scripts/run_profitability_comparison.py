"""Bounded, cached-data-only research. No exchange client or live state access."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import hashlib
import json

from hermes_trading.research import aggregate_candles, load_csv, run_backtest
from hermes_trading.research_experiments import ExperimentPolicy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/runs/profitability-comparison'
WINDOWS = {'development': ('2025-10-01','2026-03-01'),
           'evaluation': ('2026-03-01','2026-06-01'),
           'final_check': ('2026-06-01','2026-09-01')}
ASSETS = ['BTC','ETH','LINK','SOL','XRP']


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n', encoding='utf-8')


def eligible(result):
    return result['completed_positions'] >= 20 and result['net_return'] > 0 and D(result['expectancy'] or '0') > 0 and not result['halted']


def choose(results, control, names):
    eligible_names = [name for name in names if eligible(results[name])]
    return max(eligible_names, key=lambda name: results[name]['net_return']) if eligible_names else control


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    candidate_dir = OUT/'candidates'
    candidate_dir.mkdir(exist_ok=True)
    seed = json.loads((ROOT/'research/candidates/corrected_rsi_15m.json').read_text())
    metadata = json.loads((ROOT/'research/candidates/kraken_market_constraints.json').read_text())['response']['result']
    for asset in ASSETS:
        seed['strategy']['symbols'][asset+'/USDT']['research'] = {
            'price_tick': metadata[('XBT' if asset=='BTC' else asset)+'USDT']['tick_size']}
    seed['options'] = {}
    candidates, results, selections = {}, {}, []
    raw = {asset+'/USDT': load_csv(ROOT/'research/runs/return-validation/recent_data'/(asset+'USDT_15min.csv')) for asset in ASSETS}
    source_paths = [ROOT/'hermes_trading'/name for name in ('research.py','research_experiments.py','positions.py','strategy.py','execution_policy.py','trend.py')]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in source_paths}
    data_hashes = {asset: digest(ROOT/'research/runs/return-validation/recent_data'/(asset+'USDT_15min.csv')) for asset in ASSETS}
    runner_hash = digest(Path(__file__))
    save(OUT/'protocol.json', {'windows': WINDOWS, 'selection': 'development only, >=20 completed trades, positive net return and expectancy, no halt; otherwise keep control',
        'code_hashes': hashes, 'data_hashes': data_hashes, 'diagnostic_balance': '300', 'runner_sha256': digest(Path(__file__)),
        'plan_sha256': digest(ROOT/'docs/superpowers/plans/2026-09-13-profitability-comparison.md')})

    def add(name, parent=None, *, timeframe=None, options=None, exit_changes=None, remove_exit=(), maker=False):
        candidate = deepcopy(candidates[parent] if parent else seed)
        candidate['strategy']['version'] = name
        if timeframe:
            candidate['timeframe'] = timeframe
            candidate['strategy']['exit']['max_bars'] = 12 if timeframe=='1h' else 48
        candidate['options'].update(options or {})
        if maker:
            candidate['strategy']['entry']['fee'] = 'maker'
            for override in candidate['strategy']['symbols'].values():
                override.setdefault('entry', {})['fee'] = 'maker'
        for rule in [candidate['strategy']['exit']] + [s.setdefault('exit', {}) for s in candidate['strategy']['symbols'].values()]:
            for key in remove_exit:
                rule.pop(key, None)
            if exit_changes:
                rule.update(exit_changes)
        path = candidate_dir/(name+'.json')
        if path.exists() and json.loads(path.read_text()) != candidate:
            raise ValueError('Frozen candidate differs: '+name)
        save(path, candidate)
        candidates[name] = candidate
        return name

    def run(name, window='development', balance=300, dataset=None):
        candidate = candidates[name]
        start, end = WINDOWS[window] if window in WINDOWS else ('2022-01-01','2022-07-27')
        start, end = instant(start), instant(end)
        source = raw if dataset is None else dataset
        data = {a: [c for c in cs if start-timedelta(days=30) <= c.open_timestamp and c.close_timestamp <= end] for a,cs in source.items()}
        if len(data) != 5 or any(not cs or cs[0].open_timestamp > start or cs[-1].close_timestamp < end for cs in data.values()):
            raise ValueError('Incomplete source window')
        if candidate['timeframe'] != '15m':
            data = {a: aggregate_candles(cs, '15m', candidate['timeframe']) for a,cs in data.items()}
        key = f'{name}__{window}__{balance}'
        path = OUT/(key+'.json')
        fingerprint = {'candidate': candidate, 'code_hashes': hashes, 'runner_sha256': runner_hash,
                       'start': start.isoformat(), 'end': end.isoformat(), 'warmup_days': 30, 'drawdown_limit': '.08',
                       'data_hashes': data_hashes if dataset is None else {a: digest(ROOT/'market_data_cache'/(a.replace('/','')+'_15min.csv')) for a in dataset}, 'balance': balance, 'window': window}
        if path.exists():
            cached = json.loads(path.read_text())
            if cached['fingerprint'] == fingerprint:
                result = cached['result']
                results[key] = result
                print('cached', key, flush=True)
                return result
        print('running', key, flush=True)
        result = run_backtest(data, candidate['strategy'], candidate['timeframe'], start, end, D(balance), policy=ExperimentPolicy(candidate['options'], data, candidate['timeframe']))
        save(path, {'fingerprint': fingerprint, 'result': result})
        results[key] = result
        print('finished', key, 'return', round(result['net_return']*100,4), 'trades', result['completed_positions'], flush=True)
        return result

    def stage(label, control, names):
        development = {name: run(name) for name in names}
        selected = choose(development, control, names)
        selections.append({'stage': label, 'control': control, 'candidates': names, 'selected': selected,
                           'eligible': [name for name in names if eligible(development[name])]})
        save(OUT/'selections.json', selections)
        return selected

    control = add('control_taker_15m')
    maker = add('maker_15m', control, options={'maker': True}, maker=True)
    execution = stage('costs', control, [control, maker])
    hourly = add('hourly', execution, timeframe='1h')
    filtered = add('hourly_with_4h_trend', hourly, options={'higher_trend': True})
    timeframe = stage('timeframe', execution, [execution, hourly, filtered])
    full = add('full_exit', timeframe, remove_exit=('partial_close_pct',))
    delayed = add('delayed_trailing', full, options={'delayed_trailing': True})
    target = add('fixed_target', full, options={'fixed_target_only': True}, remove_exit=('trailing_atr','indicator','threshold','trailing_overrides_explicit_exit','trailing_activation_atr'), exit_changes={'take_profit_atr': '4'})
    exit_choice = stage('exits', timeframe, [timeframe, full, delayed, target])
    scalable = add('scalable_cap', exit_choice, options={'scalable_cap': True})
    fallback = add('scalable_full_exit_fallback', scalable, options={'full_exit_fallback': True})
    # Evaluate every frozen alternative, including unsuccessful development alternatives.
    for name in list(candidates):
        for window in WINDOWS:
            run(name, window)
    for name in (exit_choice, scalable, fallback):
        for balance in (50, 150, 500):
            for window in WINDOWS:
                run(name, window, balance)
    older = {asset+'/USDT': load_csv(ROOT/'market_data_cache'/(asset+'USDT_15min.csv')) for asset in ASSETS}
    for name in dict.fromkeys((control, exit_choice, scalable, fallback)):
        run(name, 'older_stress', 300, older)
    save(OUT/'results.json', {'selections': selections, 'results': results,
         'promotion': 'none; retrospective results require independent forward paper validation'})
    print('Complete:', OUT/'results.json', flush=True)


if __name__ == '__main__':
    main()
