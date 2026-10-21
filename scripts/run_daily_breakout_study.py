"""Run the single pre-specified daily-breakout hypothesis using cached archives."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collections import defaultdict
from datetime import UTC,datetime,timedelta
import hashlib
import json

from hermes_trading.research import load_csv
from hermes_trading.signal_quality import outcome,spaced_indices,preceding_control
from hermes_trading.daily_breakout_study import features,quarter,summarize,verdict

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/runs/daily-breakout-v1'
WINDOWS={'development':('2019-01-01','2023-01-01'),'evaluation':('2023-01-01','2025-01-01'),'final_check':('2025-01-01','2026-09-01')}
HORIZONS=(3,7,14)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    candidate_path=ROOT/'research/candidates/daily_breakout_v1.json'
    candidate=json.loads(candidate_path.read_text())
    # Implementation is deliberately a single fixed hypothesis, not a config optimizer.
    assert candidate['entry']=={'close_above_prior_high_days':20,'trend_sma_days':100,'sma_rising_lookback_days':5,'fill':'next_daily_open'}
    assert candidate['assets']==['BTC/USDT','ETH/USDT'] and candidate['primary_horizon_days']==7
    sources=json.loads((OUT/'data/sources.json').read_text())
    for asset in ('BTC','ETH'):
        assert digest(OUT/f'data/{asset}USDT_1d.csv')==sources['coverage'][asset]['csv_sha256']
    plan=ROOT/'docs/superpowers/plans/2026-09-14-daily-breakout-study.md'
    code=[ROOT/'hermes_trading'/name for name in ('daily_breakout_study.py','research.py','signal_quality.py','market_data.py')]+[Path(__file__),ROOT/'scripts/fetch_daily_breakout_data.py']
    protocol={'candidate':candidate,'candidate_sha256':digest(candidate_path),'windows':{k:list(v) for k,v in WINDOWS.items()},
              'primary_horizon_days':7,'secondary_horizon_days':[3,14],'fee_fraction_per_leg':'.008','slippage_fraction_per_leg':'.001',
              'plan_sha256':digest(plan),'data_manifest_sha256':digest(OUT/'data/sources.json'),
              'code_hashes':{str(p.relative_to(ROOT)):digest(p) for p in code}}
    frozen=OUT/'protocol.json'
    if frozen.exists() and json.loads(frozen.read_text())!=protocol:
        raise ValueError('Frozen protocol changed: preserve existing run and create a new version')
    frozen.write_text(json.dumps(protocol,indent=2)+'\n')
    (OUT/'protocol_plan.md').write_bytes(plan.read_bytes())
    results={}
    for label,(begin,finish) in WINDOWS.items():
        start=datetime.fromisoformat(begin).replace(tzinfo=UTC);end=datetime.fromisoformat(finish).replace(tzinfo=UTC)
        rows=[];coverage={};benchmarks={}
        for asset in ('BTC','ETH'):
            symbol=asset+'/USDT'
            candles=load_csv(OUT/f'data/{asset}USDT_1d.csv','1d',start=start-timedelta(days=120),end=end)
            signals=[];info={};controls=defaultdict(list);available={}
            print('evaluating',label,symbol,flush=True)
            for i,candle in enumerate(candles):
                when=candle.close_timestamp
                if when<start or when>=end:continue
                f=features(candles,i)
                if f is None:continue
                if f['signal']:signals.append(i)
                complete=outcome(candles,i,14)
                if complete is None:continue
                available[i]=complete
                key=(*quarter(when),f['atr_band'],f['regime'])
                if f['signal']:info[i]=(f,key)
                elif f['trend'] and not f['breakout']:controls[key].append(i)
            spaced=set(spaced_indices(candles,signals,14*24))
            coverage[symbol]={'signals':len(signals),'eligible_signals':len(info),'censored':len(signals)-len(info),
                              'spaced_eligible':len(spaced.intersection(info))}
            actual=[c for c in candles if c.open_timestamp>=start]
            ratio=float(actual[-1].close/actual[0].open)
            benchmarks[symbol]={'raw_buy_hold_return':ratio-1,'net_buy_hold_return':ratio*.999*.992/(1.001*1.008)-1,'cash_return_before_costs':0}
            for i,(f,key) in info.items():
                control=preceding_control(controls[key],i,minimum_distance=14)
                for horizon in HORIZONS:
                    measured=available[i] if horizon==14 else outcome(candles,i,horizon)
                    reference=None if control is None else (available[control] if horizon==14 else outcome(candles,control,horizon))
                    rows.append({'asset':symbol,'signal_at':candles[i].close_timestamp.isoformat(),'horizon_days':horizon,
                                 'primary_cohort':i in spaced,'regime':f['regime'],'matching_bucket':key,**measured,
                                 'control_at':candles[control].close_timestamp.isoformat() if control is not None else None,
                                 'control_gross_return':reference['gross_return'] if reference else None,
                                 'paired_difference':measured['gross_return']-reference['gross_return'] if reference else None})
        with (OUT/f'{label}_events.jsonl').open('w') as handle:
            for row in rows:handle.write(json.dumps(row)+'\n')
        main_rows=[r for r in rows if r['primary_cohort']]
        summary={'coverage':coverage,'primary':{},'all_overlapping':{},'per_asset':{},'regime_diagnostics':{},'annual_diagnostics':{},'benchmarks_context_only':benchmarks}
        for h in HORIZONS:
            summary['primary'][str(h)]=summarize([r for r in main_rows if r['horizon_days']==h])
            summary['all_overlapping'][str(h)]=summarize([r for r in rows if r['horizon_days']==h])
        for asset in ('BTC/USDT','ETH/USDT'):
            summary['per_asset'][asset]={str(h):summarize([r for r in main_rows if r['asset']==asset and r['horizon_days']==h]) for h in HORIZONS}
        for regime in ('rising','falling','sideways'):
            summary['regime_diagnostics'][regime]=summarize([r for r in main_rows if r['horizon_days']==7 and r['regime']==regime])
        for year in sorted({r['signal_at'][:4] for r in rows}):
            summary['annual_diagnostics'][year]=summarize([r for r in main_rows if r['horizon_days']==7 and r['signal_at'].startswith(year)])
        results[label]=summary
        (OUT/f'{label}_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print('finished',label,'primary',summary['primary']['7'],flush=True)
    payload={'results':results,'verdict':verdict(results)}
    (OUT/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    print('Conclusion:',payload['verdict'],flush=True)


if __name__=='__main__':main()
