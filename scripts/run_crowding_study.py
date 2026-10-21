"""One immutable proxy-venue run of the registered crowding event screen."""
import hashlib,json,random,sys
from datetime import datetime,timezone
from pathlib import Path
from statistics import mean,median

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from hermes_trading.crowding_study import direction,discrete_return,HOUR,DAY
DATA=ROOT/'research/runs/crowding-proxy-v1';OUT=DATA/'study-v1'
if OUT.exists():raise RuntimeError('Immutable run exists; never overwrite')
OUT.mkdir()
def stamp(s):return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()*1000)
BEGIN=stamp('2022-01-01');END=stamp('2026-09-01')
plan=ROOT/'docs/superpowers/plans/2026-09-28-crowding-reversal-study.md'
sources=[plan,Path(__file__),ROOT/'hermes_trading/crowding_study.py',ROOT/'tests/test_crowding_study.py']
inputs=sources+[DATA/f'{s}_{k}.json' for s in ['SOLUSDT','XRPUSDT'] for k in ['perp','mark','funding']]
for name in ['download_manifest.json','price_download_manifest.json']:
    for r in json.loads((DATA/name).read_text()):
        assert hashlib.sha256((DATA/r['path']).read_bytes()).hexdigest()==r['sha256']
coverage=json.loads((DATA/'price_coverage.json').read_text())
for name,r in coverage.items():
    assert r['missing_count']==0
    assert hashlib.sha256((DATA/(name+'.json')).read_bytes()).hexdigest()==r['sha256']
(OUT/'freeze.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},indent=2)+'\n')
for p in sources:(OUT/p.name).write_bytes(p.read_bytes())
rows=[];audit={}
for symbol in ['SOLUSDT','XRPUSDT']:
    p={int(r[0]):list(map(float,r[1:5])) for r in json.loads((DATA/f'{symbol}_perp.json').read_text())}
    m={int(r[0]):list(map(float,r[1:5])) for r in json.loads((DATA/f'{symbol}_mark.json').read_text())}
    fr=json.loads((DATA/f'{symbol}_funding.json').read_text());f={int(r['fundingRateTimestamp']):float(r['fundingRate']) for r in fr}
    assert len(fr)==len(f)
    archived={int(r['fundingRateTimestamp']):float(r['fundingRate']) for path in DATA.glob(symbol+'_[0-9][0-9].json') for r in json.loads(path.read_text())['result']['list']}
    assert all(archived[t]==v for t,v in f.items())
    expected=set(range(BEGIN,END,8*HOUR))
    if symbol=='SOLUSDT':expected.update(range(stamp('2022-11-10T10:00'),stamp('2022-12-20T08:00')+1,2*HOUR))
    assert set(f)==expected,'Funding grid mismatch; no missing payments may be filled'
    daily={d:sum(f[t] for t in range(d,d+DAY,HOUR) if t in f) for d in range(BEGIN,END,DAY)}
    last_exit=0;counts={'feature_incomplete':0,'cooldown':0,'no_signal':0,'boundary_censored':0}
    for decision in range(BEGIN,END,DAY):
        if decision<last_exit:counts['cooldown']+=1;continue
        if decision-DAY not in p or decision-HOUR not in p:counts['feature_incomplete']+=1;continue
        ret=p[decision-HOUR][3]/p[decision-DAY][0]-1
        side=direction(daily,decision,ret)
        if side is None:counts['feature_incomplete']+=1;continue
        if not side:counts['no_signal']+=1;continue
        dt=datetime.fromtimestamp(decision/1000,timezone.utc)
        block='development' if dt.year==2022 else 'evaluation' if dt.year<=2024 else 'recent'
        cutoff={'development':stamp('2023-01-01'),'evaluation':stamp('2025-01-01'),'recent':END}[block]
        entry=decision+HOUR;exit=entry+72*HOUR
        if exit>cutoff:counts['boundary_censored']+=1;continue
        base=discrete_return(p,m,f,entry,exit,side)
        stress=discrete_return(p,m,f,entry,exit,side,slip=.003)
        rows.append({'symbol':symbol,'block':block,'decision':decision,'entry':entry,'exit':exit,
                     'quarter':f'{dt.year}Q{(dt.month-1)//3+1}','side':side,
                     'signal_day_funding':daily[decision-DAY],'signal_day_price_return':ret,
                     **base,'stress_net_return':stress['net_return']})
        last_exit=exit
    audit[symbol]=counts

def stats(rs):
    if not rs:return {'events':0,'mean_net':None,'interval_95':None}
    vals=[r['net_return'] for r in rs];qs=sorted({r['quarter'] for r in rs});ci=None
    if len(rs)>=20 and len(qs)>=6:
        rng=random.Random(20260928);means=[]
        buckets={q:[r['net_return'] for r in rs if r['quarter']==q] for q in qs}
        for _ in range(2000):means.append(mean(v for q in rng.choices(qs,k=len(qs)) for v in buckets[q]))
        means.sort();ci=[means[49],means[1949]]
    return {'events':len(rs),'occupied_quarters':len(qs),'mean_net':mean(vals),'median_net':median(vals),
            'win_fraction':mean(v>0 for v in vals),'mean_stress':mean(r['stress_net_return'] for r in rs),
            'mean_price':mean(r['price_return'] for r in rs),'mean_funding':mean(r['funding_return'] for r in rs),
            'mean_fee':mean(r['fee_return'] for r in rs),'worst_event':min(vals),'best_event':max(vals),'interval_95':ci}
summary={b:stats([r for r in rows if r['block']==b]) for b in ['development','evaluation','recent']}
reasons=[]
for b in ['evaluation','recent']:
    s=summary[b]
    if s['events']<20 or s.get('occupied_quarters',0)<6:reasons.append(b+'_sparse')
    if s['mean_net'] is None or s['mean_net']<=0:reasons.append(b+'_nonpositive')
    if s.get('mean_stress') is None or s['mean_stress']<=0:reasons.append(b+'_stress_nonpositive')
    if not s['interval_95'] or s['interval_95'][0]<=0:reasons.append(b+'_uncertainty_gate_failed')
result={'decision':'do_not_advance' if reasons else 'provisional_screen_pass_not_paper_ready',
        'reasons':reasons,'summary':summary,'exclusions':audit,
        'by_side':{str(side):stats([r for r in rows if r['side']==side]) for side in [-1,1]},
        'by_asset':{s:stats([r for r in rows if r['symbol']==s]) for s in ['SOLUSDT','XRPUSDT']}}
(OUT/'events.json').write_text(json.dumps(rows,indent=2)+'\n');(OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'decision':result['decision'],'reasons':reasons,'summary':summary},indent=2))
