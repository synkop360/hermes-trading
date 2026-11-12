"""Bounded public archive acquisition for the frozen daily-breakout study."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile
import csv
import hashlib
import io
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/runs/daily-breakout-v1/data'


def fetch(asset,month):
    name=f'{asset}USDT-1d-{month}.zip'
    url='https://data.binance.vision/data/spot/monthly/klines/'+asset+'USDT/1d/'+name
    path=OUT/name
    checksum_path=OUT/(name+'.CHECKSUM')
    if path.exists() and checksum_path.exists():
        data=path.read_bytes();checksum=checksum_path.read_text().split()[0]
    else:
        with urlopen(url,timeout=25) as response:data=response.read(2_000_000)
        with urlopen(url+'.CHECKSUM',timeout=25) as response:checksum=response.read().decode().split()[0]
    if hashlib.sha256(data).hexdigest()!=checksum:
        raise ValueError('Checksum mismatch: '+name)
    path.write_bytes(data);checksum_path.write_text(checksum+'\n')
    rows=[]
    with ZipFile(io.BytesIO(data)) as archive:
        for row in csv.reader(io.StringIO(archive.read(archive.namelist()[0]).decode())):
            stamp=int(row[0]);divisor=1_000_000 if stamp>10**14 else 1000
            opened=datetime.fromtimestamp(stamp/divisor,UTC)
            if opened.strftime('%Y-%m')!=month or opened.hour or opened.minute or opened.second:
                raise ValueError('Invalid archive timestamp: '+name)
            rows.append([int(opened.timestamp()*1000),opened.isoformat(),asset+'USDT',*row[1:6]])
    return asset,rows,{'url':url,'sha256':checksum,'bytes':len(data),'rows':len(rows)}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    months=[f'{year}-{month:02d}' for year in range(2018,2027) for month in range(1,13) if (year,month)<=(2026,8)]
    gathered={'BTC':[],'ETH':[]};manifest=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(fetch,asset,month) for asset in gathered for month in months]
        for number,future in enumerate(futures,1):
            asset,rows,source=future.result()
            gathered[asset].extend(rows);manifest.append(source)
            if number%24==0:print('verified',number,'of',len(futures),'archives',flush=True)
    coverage={}
    for asset,rows in gathered.items():
        rows.sort(key=lambda r:r[0])
        dates=[datetime.fromisoformat(row[1]) for row in rows]
        expected_start=datetime(2018,1,1,tzinfo=UTC);expected_end=datetime(2026,9,1,tzinfo=UTC)
        if dates[0]!=expected_start or dates[-1]+timedelta(days=1)!=expected_end or any(b-a!=timedelta(days=1) for a,b in zip(dates,dates[1:])):
            raise ValueError('Missing or duplicate daily bars: '+asset)
        path=OUT/(asset+'USDT_1d.csv')
        with path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['unix','date','symbol','open','high','low','close','volume']);writer.writerows(rows)
        coverage[asset]={'rows':len(rows),'first_open':dates[0].isoformat(),'last_close':expected_end.isoformat(),'csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (OUT/'sources.json').write_text(json.dumps({'retrieved_at':datetime.now(UTC).isoformat(),'archives':manifest,'coverage':coverage},indent=2)+'\n')
    print('Complete:',coverage,flush=True)


if __name__=='__main__':main()
