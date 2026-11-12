"""Fixed signal event study including entries rejected by the trading gates."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import hashlib
import json

from hermes_trading.research import load_csv, snapshots, symbol_strategy
from hermes_trading.signal_quality import outcome, spaced_indices, preceding_control, aggregate
from hermes_trading.strategy import evaluate_strategy, _atr
from hermes_trading.positions import PositionEngine, SimulationSettings, _expected_payoff_covers_costs, _round_quantity
from hermes_trading.execution_policy import initial_stop_price
from hermes_trading.persistence import StateRepository

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/runs/cost-signal-audit'
ASSETS = ['BTC','ETH','LINK','SOL','XRP']
WINDOWS = {'development':('2025-10-01','2026-03-01'), 'evaluation':('2026-03-01','2026-06-01'),
           'final_check':('2026-06-01','2026-09-01'), 'older_stress':('2022-01-01','2022-07-27')}
HOURS = (1,4,12,24)


def instant(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def covariates(snapshot, atr):
    when = snapshot.candle.close_timestamp
    closes = snapshot.price['closes']
    fraction = atr/snapshot.candle.close
    band = sum(fraction>=x for x in (D('.0025'),D('.005'),D('.01')))
    trend = closes[-1]>sum(closes[-20:])/20 and sum(closes[-20:])>sum(closes[-21:-1])
    return (when.isocalendar().year,when.isocalendar().week,when.hour//4,band,trend)


def size_allowed(engine, snapshot, config, balance):
    settings = SimulationSettings.from_strategy(config)
    price = snapshot.candle.close*(1+settings.slippage_rate)
    stop = initial_stop_price(snapshot,config,price)
    engine.equity = D(balance)
    quantity = _round_quantity(engine._entry_notional(config,settings,price,stop)/price,settings)
    return quantity>0 and quantity>=settings.minimum_order_quantity and quantity*price>=settings.minimum_order_quote


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candidate_path = ROOT/'research/candidates/corrected_rsi_15m.json'
    strategy = json.loads(candidate_path.read_text())['strategy']
    plan_path = ROOT/'docs/superpowers/plans/2026-09-13-cost-and-signal-audit.md'
    (OUT/'protocol_plan.md').write_bytes(plan_path.read_bytes())
    sources = [ROOT/'hermes_trading'/name for name in ('signal_quality.py','research.py','strategy.py','positions.py','execution_policy.py')]+[Path(__file__)]
    protocol = {'windows':WINDOWS,'hours':HOURS,'candidate_sha256':sha(candidate_path),
                'code_hashes':{str(p.relative_to(ROOT)):sha(p) for p in sources},
                'plan_sha256':sha(plan_path),'fee_fraction_per_leg':'.008','slippage_fraction_per_leg':'.001',
                'account_fees_verified':False,'data_hashes':{}}
    state=StateRepository.open(':memory:')
    state.migrate()
    engine=PositionEngine(state)
    all_summaries={}
    try:
        for label,(first,last) in WINDOWS.items():
            start,end=instant(first),instant(last)
            folder=ROOT/('market_data_cache' if label=='older_stress' else 'research/runs/return-validation/recent_data')
            rows, counts = [], {}
            for asset in ASSETS:
                symbol=asset+'/USDT'
                path=folder/(asset+'USDT_15min.csv')
                protocol['data_hashes'][str(path.relative_to(ROOT))]=sha(path)
                candles=load_csv(path,start=start-timedelta(days=30),end=end)
                config=symbol_strategy(strategy,symbol)
                settings=SimulationSettings.from_strategy(config)
                signals,available,groups,info=[],{},defaultdict(list),{}
                print('evaluating',label,symbol,flush=True)
                for index,snapshot in enumerate(snapshots({symbol:candles},'15m')):
                    when=snapshot.candle.close_timestamp
                    if when<start or when>=end or len(snapshot.price['closes'])<21:
                        continue
                    atr=_atr(snapshot)
                    if atr is None:continue
                    is_signal=evaluate_strategy(snapshot,config,None).action=='enter'
                    if is_signal:signals.append(index)
                    # Common 24h eligibility makes every horizon use the same cohort.
                    full=outcome(candles,index,96)
                    if full is None:continue
                    key=covariates(snapshot,atr)
                    available[index]=full
                    if not is_signal:
                        groups[key].append(index)
                    else:
                        info[index]={'bucket':key,'passes_cost':_expected_payoff_covers_costs(config,settings,snapshot),
                                     'minimum_50':size_allowed(engine,snapshot,config,50),
                                     'minimum_300':size_allowed(engine,snapshot,config,300)}
                spaced=set(spaced_indices(candles,signals,24))
                counts[symbol]={'signals':len(signals),'full_horizon_signals':len(info),
                                'censored_horizon_signals':len(signals)-len(info),
                                'nonoverlap_full_horizon_signals':len(spaced.intersection(info))}
                for index,features in info.items():
                    control=preceding_control(groups[features['bucket']],index,minimum_distance=96)
                    for hours in HOURS:
                        measured=available[index] if hours==24 else outcome(candles,index,hours*4)
                        reference=None if control is None else (available[control] if hours==24 else outcome(candles,control,hours*4))
                        rows.append({'asset':symbol,'signal_at':candles[index].close_timestamp.isoformat(),
                                     'hours':hours,'nonoverlap':index in spaced,
                                     'passes_cost':features['passes_cost'],'minimum_50':features['minimum_50'],'minimum_300':features['minimum_300'],
                                     'matching_bucket':features['bucket'],**measured,
                                     'control_at':candles[control].close_timestamp.isoformat() if control is not None else None,
                                     'control_gross_return':reference['gross_return'] if reference else None,
                                     'control_net_return':reference['net_return'] if reference else None})
            with (OUT/(label+'_events.jsonl')).open('w',encoding='utf-8') as handle:
                for row in rows:handle.write(json.dumps(row)+'\n')
            subsets = {'all':lambda r:True,'nonoverlap':lambda r:r['nonoverlap'],
                       'cost_rejected':lambda r:not r['passes_cost'], 'cost_passed':lambda r:r['passes_cost'],
                       'below_minimum_50':lambda r:not r['minimum_50'], 'below_minimum_300':lambda r:not r['minimum_300']}
            summary={'coverage_counts':counts,'subsets':{}}
            for name,predicate in subsets.items():
                summary['subsets'][name]={str(hours):aggregate([r for r in rows if r['hours']==hours and predicate(r)],start,end) for hours in HOURS}
            all_summaries[label]=summary
            (OUT/(label+'_summary.json')).write_text(json.dumps(summary,indent=2)+'\n')
            print('finished',label,'signals',sum(c['signals'] for c in counts.values()),flush=True)
        (OUT/'signal_results.json').write_text(json.dumps(all_summaries,indent=2)+'\n')
        (OUT/'signal_protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
        print('Signal study complete:',OUT,flush=True)
    finally:
        state.close()


if __name__=='__main__':
    main()
