from pathlib import Path
import sys,json,hashlib
from datetime import datetime,UTC
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hermes_trading.research import load_csv
from hermes_trading.monthly_rotation import simulate,benchmark

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    out=ROOT/'research/runs/monthly-rotation-v1';out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'research/runs/daily-breakout-v1/data'
    manifest=json.loads((source/'sources.json').read_text())
    paths={a:source/f'{a}USDT_1d.csv' for a in ('BTC','ETH')}
    for a,p in paths.items():assert digest(p)==manifest['coverage'][a]['csv_sha256']
    plan=ROOT/'docs/superpowers/plans/2026-09-28-autonomous-strategy-research.md'
    windows={'development':['2019-01-01','2023-01-01'],'evaluation':['2023-01-01','2025-01-01'],'final_check':['2025-01-01','2026-09-01']}
    sources=[Path(__file__),ROOT/'hermes_trading/monthly_rotation.py',ROOT/'hermes_trading/research.py',ROOT/'tests/test_monthly_rotation.py']
    protocol={'windows':windows,'lookback_days':90,'fee':.008,'slippage_base':.001,'slippage_stress':.003,'plan_sha256':digest(plan),'data_hashes':{a:digest(p) for a,p in paths.items()},'source_hashes':{str(p.relative_to(ROOT)):digest(p) for p in sources}}
    frozen=out/'protocol.json'
    if frozen.exists() and json.loads(frozen.read_text())!=protocol:raise ValueError('Frozen run differs; preserve prior version')
    frozen.write_text(json.dumps(protocol,indent=2));(out/'protocol_plan.md').write_bytes(plan.read_bytes())
    for p in sources:
        dest=out/'frozen_source'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(p.read_bytes())
    data={a:load_csv(p,'1d') for a,p in paths.items()};results={}
    for label,(first,last) in windows.items():
        start=datetime.fromisoformat(first).replace(tzinfo=UTC);end=datetime.fromisoformat(last).replace(tzinfo=UTC)
        base=simulate(data,start,end);stress=simulate(data,start,end,slippage=.003)
        (out/f'{label}_base.json').write_text(json.dumps(base,indent=2));(out/f'{label}_stress.json').write_text(json.dumps(stress,indent=2))
        summary={k:v for k,v in base.items() if k not in ('trades','daily_equity')}
        summary['stress_net_return']=stress['net_return']
        summary['benchmarks']={'BTC':benchmark(data,start,end,{'BTC':1.}),'BTC_ETH_50_50':benchmark(data,start,end,{'BTC':.5,'ETH':.5})}
        summary['screen']='reject' if base['net_return']<=0 or stress['net_return']<=0 or base['max_drawdown']>.30 else 'sparse' if base['active_months']<12 or base['completed_holdings']<6 else 'provisional'
        results[label]=summary;print(label,json.dumps(summary),flush=True)
    (out/'results.json').write_text(json.dumps(results,indent=2))

if __name__=='__main__':main()
