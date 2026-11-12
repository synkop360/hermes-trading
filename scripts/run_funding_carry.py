"""Execute the predeclared quarterly research screen, without optimizing."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hermes_trading.funding_carry import simulate

DATA=ROOT/'research/runs/bybit-carry-v1'
OUT=DATA/'study-v2'
if OUT.exists():
    raise RuntimeError('Immutable run already exists; use an explicitly versioned correction')
OUT.mkdir()
plan=ROOT/'docs/superpowers/plans/2026-09-28-bybit-carry-study.md'
sources=[plan,Path(__file__),ROOT/'hermes_trading/funding_carry.py']
coverage=json.loads((DATA/'price_coverage.json').read_text())
for name,info in coverage.items():
    assert info['missing_count']==0
    assert hashlib.sha256((DATA/(name+'.json')).read_bytes()).hexdigest()==info['sha256']
for manifest in ['download_manifest.json','price_download_manifest.json']:
    for r in json.loads((DATA/manifest).read_text()):
        assert hashlib.sha256((DATA/r['path']).read_bytes()).hexdigest()==r['sha256']
inputs=sources+[DATA/(n+'.json') for n in coverage]+[DATA/(s+'_funding.json') for s in ['BTCUSDT','ETHUSDT']]
freeze={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
(OUT/'freeze.json').write_text(json.dumps(freeze,indent=2)+'\n')
for p in sources:(OUT/p.name).write_bytes(p.read_bytes())
datasets={}
for symbol in ['BTCUSDT','ETHUSDT']:
    d={k:{int(r[0]):list(map(float,r[1:5])) for r in json.loads((DATA/f'{symbol}_{k}.json').read_text())} for k in ['spot','perp','mark']}
    fr=json.loads((DATA/f'{symbol}_funding.json').read_text())
    d['funding']={int(r['fundingRateTimestamp']):float(r['fundingRate']) for r in fr}
    assert len(d['funding'])==len(fr)
    ts=sorted(d['funding']);assert all(b-a==28800000 for a,b in zip(ts,ts[1:]))
    # Prove normalized records agree with the archived raw funding pages.
    raw={int(r['fundingRateTimestamp']):float(r['fundingRate'])
         for p in DATA.glob(symbol+'_[0-9][0-9].json')
         for r in json.loads(p.read_text())['result']['list']}
    assert all(raw[t]==v for t,v in d['funding'].items())
    datasets[symbol]=d
rows=[]
for year in range(2022,2027):
    for month in (1,4,7,10):
        if (year,month)>(2026,4):continue
        a=datetime(year,month,1,1,tzinfo=timezone.utc)
        b=datetime(year+(month==10),1 if month==10 else month+3,1,1,tzinfo=timezone.utc)
        start,end=int(a.timestamp()*1000),int(b.timestamp()*1000)
        for symbol,d in datasets.items():
            expected=set(range(((start//28800000)+1)*28800000,end,28800000))
            assert expected<=d['funding'].keys()
            for mode,kwargs in [('base',{}),('stress',{'slip':.003}),('funding_bound',{'pessimistic':True}),('kraken_cost',{'spot_fee':.008,'future_fee':.0005})]:
                r=simulate(**d,start=start,end=end,initial=5000.,**kwargs)
                equity=r.pop('equity')
                (OUT/f'{year}Q{(month-1)//3+1}_{symbol}_{mode}_equity.json').write_text(json.dumps(equity))
                rows.append({'year':year,'quarter':(month-1)//3+1,'symbol':symbol,'mode':mode,**r})
(OUT/'quarters.json').write_text(json.dumps(rows,indent=2)+'\n')
summary={}
for label,years in [('development',[2022]),('evaluation',[2023,2024]),('recent',[2025,2026])]:
    summary[label]={}
    for mode in ['base','stress','funding_bound','kraken_cost']:
        rs=[r for r in rows if r['year'] in years and r['mode']==mode]
        quarters=[mean(r['net_return'] for r in rs if (r['year'],r['quarter'])==q) for q in sorted({(r['year'],r['quarter']) for r in rs})]
        summary[label][mode]={'quarters':len(quarters),'mean_quarter_return':mean(quarters),
                             'winning_quarters':sum(x>0 for x in quarters),'unsafe_sleeves':sum(r['unsafe'] for r in rs),
                             'worst_sleeve_drawdown':max(r['max_drawdown'] for r in rs),
                             'hypothetical_returns_if_any_unsafe':any(r['unsafe'] for r in rs)}
reasons=[]
if any(r['unsafe'] for r in rows):reasons.append('collateral_failure')
if any(r['max_drawdown']>.1 for r in rows if r['mode']=='base'):reasons.append('drawdown_above_gate')
for block in ['evaluation','recent']:
    for mode in ['base','stress']:
        if summary[block][mode]['mean_quarter_return']<=0:reasons.append(block+'_'+mode+'_nonpositive')
result={'decision':'reject' if reasons else 'provisional_further_validation_required','reasons':reasons,'summary':summary}
(OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
