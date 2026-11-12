"""Read-only local ledger and public market audit. Never stores credentials."""
from pathlib import Path
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D
import json
import os
import time
from urllib.request import urlopen
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/runs/cost-signal-audit'
PAIRS = 'XBTUSDT,ETHUSDT,LINKUSDT,SOLUSDT,XRPUSDT'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    values = dict(os.environ)
    for path in [ROOT/'.env',Path(os.environ.get('LOCALAPPDATA',''))/'hermes/.env']:
        if path.is_file():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value = line.split('=',1)
                    values.setdefault(key.strip(),value.strip().strip('"').strip("'"))
    credentials = bool(values.get('EXCHANGE_API_KEY') and values.get('EXCHANGE_API_SECRET'))
    result = {'retrieved_at':datetime.now(UTC).isoformat(),
              'account_fee_status':'unverified; user reports unknown',
              'credentials_configured':credentials,'private_account_requests':0,
              'configured_mode':values.get('HERMES_TRADING_MODE','unknown'),
              'hosting_cost_status':'unknown; user reports unknown',
              'ai_cost_status':'unknown; user reports unknown',
              'published_assumption':{'taker_fraction':'.008','maker_fraction':'.004',
                                      'source':'https://www.kraken.com/features/fee-schedule'},
              'configured_monthly_hosting_usd':None,'cost_ledger':{},'public_observations':[]}
    result['configured_ai_endpoint_host'] = urlparse(values.get('OPENAI_BASE_URL','https://api.openai.com')).hostname
    raw = values.get('HERMES_RAILWAY_MONTHLY_COST_USD')
    if raw:
        try:
            result['configured_monthly_hosting_usd'] = str(D(raw))
        except Exception:
            result['configured_monthly_hosting_usd'] = 'invalid'
    path = ROOT/'state/cost_events.jsonl'
    groups = defaultdict(lambda:{'count':0,'known_total':D(0),'missing_count':0})
    dates, invalid = [], 0
    usage=defaultdict(lambda:{'records':0,'input_tokens':0,'output_tokens':0,'missing_usage_fields':0})
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                e=json.loads(line)
                key=(e.get('category'),e.get('currency'),e.get('quality'),e.get('source'))
                g=groups[key]
                g['count']+=1
                if e.get('amount') is None:g['missing_count']+=1
                else:g['known_total']+=D(str(e['amount']))
                dates.append(e['occurred_at'])
                if e.get('category')=='openai':
                    metadata=e.get('metadata',{})
                    item=usage[str(metadata.get('model','unknown'))]
                    item['records']+=1
                    for name in ('input_tokens','output_tokens'):
                        if metadata.get(name) is None:item['missing_usage_fields']+=1
                        else:item[name]+=int(metadata[name])
            except (ValueError,KeyError):
                invalid+=1
    result['cost_ledger']={'path':'state/cost_events.jsonl','first':min(dates,default=None),'last':max(dates,default=None),
                          'invalid_rows':invalid,'groups':[{'category':k[0],'currency':k[1],'quality':k[2],'source':k[3],
                             **{n:str(v) if isinstance(v,D) else v for n,v in g.items()}} for k,g in groups.items()]}
    result['ai_usage_by_model']=dict(usage)
    for endpoint in ['AssetPairs','Ticker','Ticker','Ticker']:
        if endpoint=='Ticker' and result['public_observations']:
            time.sleep(2)
        url='https://api.kraken.com/0/public/'+endpoint+'?pair='+PAIRS
        observation={'url':url,'observed_at':datetime.now(UTC).isoformat()}
        try:
            with urlopen(url,timeout=20) as response: payload=json.load(response)
            if payload.get('error'):
                observation['status']='exchange_returned_error'
            else:
                observation['status']='ok'
                observation['response']=payload['result']
                if endpoint=='Ticker':
                    observation['spreads_bps']={k:float((D(v['a'][0])-D(v['b'][0]))*D(20000)/(D(v['a'][0])+D(v['b'][0]))) for k,v in payload['result'].items()}
        except Exception as error:
            observation['status']=type(error).__name__
        result['public_observations'].append(observation)
    (OUT/'account_cost_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Saved account cost audit; fees and bills remain unverified. No private requests made.')


if __name__=='__main__':
    main()
