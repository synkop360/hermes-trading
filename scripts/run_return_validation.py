"""Run fixed research candidates. No live orders or running state is accessed."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import csv
import hashlib
import io
import json
from urllib.request import urlopen
from zipfile import ZipFile
import yaml
from hermes_trading.research import conservative_strategy, load_csv, aggregate_candles, run_backtest, rolling_90

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ['BTC','ETH','LINK','SOL','XRP']


def instant(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def candidates(original):
    folder = ROOT/'research/candidates'
    folder.mkdir(parents=True,exist_ok=True)
    snapshot = folder/'original_strategy.yaml'
    if snapshot.exists() and snapshot.read_bytes() != original.read_bytes():
        raise ValueError('immutable original snapshot differs; refusing overwrite')
    if not snapshot.exists():
        snapshot.write_bytes(original.read_bytes())
    fixed = conservative_strategy(yaml.safe_load(snapshot.read_text()))
    fixed['version'] = 'fixed-rsi-recovery-1'
    base = conservative_strategy({'version':'baseline-rsi-1','entry':{'indicator':'rsi','threshold':'30'},
        'exit':{'indicator':'rsi','threshold':'55','max_bars':48},'stop_loss_pct':'2.5','position_size_r':'0.5',
        'simulation':{'risk_per_trade_pct':'0.006'}})
    trend = conservative_strategy({'version':'fixed-trend-pullback-1','entry':{'indicator':'trend_pullback','fast_period':20,'slow_period':100,'pullback_fraction':'0.015'},
        'exit':{'stop_atr':'3','trailing_atr':'4','max_bars':120,'trailing_overrides_explicit_exit':True},
        'stop_loss_pct':'5','position_size_r':'0.5','simulation':{'risk_per_trade_pct':'0.006'}})
    result = {'baseline_rsi_15m':(base,'15m'),'corrected_rsi_15m':(fixed,'15m'),'trend_pullback_4h':(trend,'4h')}
    metadata_path=folder/'kraken_market_constraints.json'
    if not metadata_path.exists():
        url='https://api.kraken.com/0/public/AssetPairs?pair=XBTUSDT,ETHUSDT,LINKUSDT,SOLUSDT,XRPUSDT'
        with urlopen(url,timeout=20) as response:
            response=json.load(response)
        if response.get('error'):
            raise ValueError(response['error'])
        metadata_path.write_text(json.dumps({'url':url,'retrieved_at':datetime.now(UTC).isoformat(),'response':response},indent=2)+'\n')
    metadata=json.loads(metadata_path.read_text())['response']['result']
    for name,(strategy,timeframe) in result.items():
        for asset in ASSETS:
            market=metadata[('XBT' if asset=='BTC' else asset)+'USDT']
            strategy.setdefault('symbols',{}).setdefault(asset+'/USDT',{}).setdefault('simulation',{}).update(
                minimum_order_quote=market['costmin'],minimum_order_quantity=market['ordermin'],quantity_decimals=market['lot_decimals'])
        payload = json.dumps({'timeframe':timeframe,'strategy':strategy},indent=2,sort_keys=True)+'\n'
        target=folder/(name+'.json')
        if target.exists() and target.read_text()!=payload:
            raise ValueError('frozen candidate changed; create a new candidate version')
        target.write_text(payload)
    (folder/'manifest.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir()) if p.name!='manifest.json'},indent=2)+'\n')
    return result


def fetch_recent(folder):
    """Bounded monthly public spot downloads, with published checksum verification."""
    folder.mkdir(parents=True,exist_ok=True)
    manifest=[]
    months=[f'2025-{m:02d}' for m in range(9,13)]+[f'2026-{m:02d}' for m in range(1,9)]
    for asset in ASSETS:
        destination=folder/(asset+'USDT_15min.csv')
        rows=[]
        for month in months:
            name=f'{asset}USDT-15m-{month}.zip'
            url=f'https://data.binance.vision/data/spot/monthly/klines/{asset}USDT/15m/{name}'
            cached=folder/name
            try:
                if cached.exists():
                    data=cached.read_bytes()
                    checksum=(folder/(name+'.CHECKSUM')).read_text().split()[0]
                else:
                    with urlopen(url,timeout=20) as response:
                        data=response.read(8_000_000)
                    with urlopen(url+'.CHECKSUM',timeout=20) as response:
                        checksum=response.read().decode().split()[0]
                    if hashlib.sha256(data).hexdigest()!=checksum:
                        raise ValueError('checksum mismatch')
                    cached.write_bytes(data)
                    (folder/(name+'.CHECKSUM')).write_text(checksum)
                if hashlib.sha256(data).hexdigest()!=checksum:
                    raise ValueError('cached checksum mismatch')
                with ZipFile(io.BytesIO(data)) as archive:
                    raw=archive.read(archive.namelist()[0]).decode()
                for row in csv.reader(io.StringIO(raw)):
                    timestamp=int(row[0])
                    divisor=1_000_000 if timestamp>10**14 else 1000
                    opened=datetime.fromtimestamp(timestamp/divisor,UTC)
                    rows.append([int(opened.timestamp()*1000),opened.isoformat(),asset+'USDT',*row[1:6]])
                manifest.append({'url':url,'sha256':checksum,'bytes':len(data)})
                print('downloaded',name,flush=True)
            except Exception as error:
                manifest.append({'url':url,'error':str(error)})
                print('unavailable',name,str(error),flush=True)
                # Missing current/future months are expected; do not fabricate coverage.
        with destination.open('w',newline='') as handle:
            writer=csv.writer(handle)
            writer.writerow(['unix','date','symbol','open','high','low','close','volume'])
            writer.writerows(rows)
    (folder/'sources.json').write_text(json.dumps(manifest,indent=2)+'\n')


def buy_hold(data,start,end):
    # Fully invested equal-weight basket benchmark, deliberately not subject to strategy halt.
    selected={a:[c for c in cs if c.open_timestamp>=start and c.close_timestamp<=end] for a,cs in data.items()}
    selected={a:cs for a,cs in selected.items() if cs}
    if not selected:
        return {}
    quantities={a:D('50')/len(selected)/D('1.008')/(cs[0].open*D('1.001')) for a,cs in selected.items()}
    events={}
    latest={a:cs[0].open for a,cs in selected.items()}
    for a,cs in selected.items():
        for c in cs:
            events.setdefault(c.close_timestamp,{})[a]=c.close
    curve=[(start,D('50'))]
    peak=D('50')
    dd=D(0)
    for when,prices in sorted(events.items()):
        latest.update(prices)
        equity=sum((quantities[a]*p*D('.999')*D('.992') for a,p in latest.items()),D(0))
        peak=max(peak,equity)
        dd=max(dd,(peak-equity)/peak)
        curve.append((when,equity))
    return {'ending_equity':str(curve[-1][1]),'net_return':float(curve[-1][1]/50-1),'max_drawdown':float(dd),'rolling_90':rolling_90(curve)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,default=ROOT/'market_data_cache')
    parser.add_argument('--original',type=Path,default=ROOT/'research/candidates/original_strategy.yaml')
    parser.add_argument('--fetch-recent',action='store_true')
    parser.add_argument('--recent-only',action='store_true')
    args=parser.parse_args()
    output=ROOT/'research/runs/return-validation'
    output.mkdir(parents=True,exist_ok=True)
    configs=candidates(args.original)
    if args.fetch_recent:
        fetch_recent(output/'recent_data')
    datasets=[]
    if not args.recent_only:
        datasets.append(('cached_2022',args.cache,[('train','2022-01-01','2022-04-01'),('validation','2022-04-01','2022-06-01'),('holdout','2022-06-01','2022-07-27'),('continuous','2022-01-01','2022-07-27')]))
    if (output/'recent_data/BTCUSDT_15min.csv').exists():
        datasets.append(('recent_public',output/'recent_data',[('train','2025-10-01','2026-03-01'),('validation','2026-03-01','2026-06-01'),('holdout','2026-06-01','2026-09-01'),('continuous','2025-10-01','2026-09-01')]))
    all_results={}
    for dataset,folder,windows in datasets:
        earliest=instant(windows[0][1])-timedelta(days=30)
        raw={asset+'/USDT':load_csv(folder/(asset+'USDT_15min.csv'),start=earliest,end=instant(windows[-1][2])) for asset in ASSETS if (folder/(asset+'USDT_15min.csv')).exists()}
        raw={a:cs for a,cs in raw.items() if cs}
        if not raw:
            continue
        coverage={a:{'first':cs[0].open_timestamp.isoformat(),'last':cs[-1].close_timestamp.isoformat(),'bars':len(cs),'gaps':sum(b.open_timestamp!=a.close_timestamp for a,b in zip(cs,cs[1:]))} for a,cs in raw.items()}
        results={'coverage':coverage,'windows':{}}
        for label,start_text,end_text in windows:
            start,end=instant(start_text),instant(end_text)
            # Require every asset to cover each nominal window, otherwise label unavailable.
            if any(cs[0].open_timestamp>start or cs[-1].close_timestamp<end for cs in raw.values()):
                results['windows'][label]={'unavailable':'source coverage does not cover the complete window'}
                continue
            window={}
            window_data={a:[c for c in cs if start-timedelta(days=30)<=c.open_timestamp<end] for a,cs in raw.items()}
            for name,(strategy,timeframe) in configs.items():
                data=window_data if timeframe=='15m' else {a:aggregate_candles(cs,'15m',timeframe) for a,cs in window_data.items()}
                print('running',dataset,label,name,flush=True)
                window[name]=run_backtest(data,strategy,timeframe,start,end)
                results['windows'][label]=window
                (output/(dataset+'.json')).write_text(json.dumps(results,indent=2)+'\n')
            window['cash']={'ending_equity':'50','net_return':0,'max_drawdown':0,'rolling_90':rolling_90([(start+timedelta(days=i),D(50)) for i in range((end-start).days+1)])}
            window['buy_hold_equal_weight']=buy_hold(raw,start,end)
            results['windows'][label]=window
            (output/(dataset+'.json')).write_text(json.dumps(results,indent=2)+'\n')
        all_results[dataset]=results
    (output/'results.json').write_text(json.dumps(all_results,indent=2)+'\n')
    print('Results:',output/'results.json',flush=True)


if __name__=='__main__':
    main()
