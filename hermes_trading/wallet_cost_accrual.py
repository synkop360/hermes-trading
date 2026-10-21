"""Deterministic, source-bound cumulative EUR operating costs for paper wallets.

Input coverage is asserted by a separate source adapter; reading a file does
not extend that coverage. No provider API, model call or FX conversion occurs.
"""
from datetime import timedelta
from decimal import Decimal,DecimalException
import json,os
from pathlib import Path

from hermes_trading.reflection_supervision import _connect,_hash,_json,_now,_time
from hermes_trading.wallet_metrics import _decimal,COSTS
from hermes_trading.paper_publication import _temporary,_sync_dir
from hermes_trading.paired_packet_storage import store_record,load_packet
from hermes_trading.supervisor_usage_feed import validate_usage_snapshot

_LOCAL_USAGE=object()


def _provenance(row):
    if not isinstance(row,dict) or not isinstance(row.get('source'),str) or not row['source'].strip():
        raise ValueError('cost source requires explicit provenance')


def _amount(value,quality):
    if not isinstance(quality,str) or quality not in {'actual','estimated','missing'}:raise ValueError('cost source quality is invalid')
    if value is None:return None
    amount=_decimal(value)
    if amount<0:raise ValueError('operating charges cannot be negative')
    return None if quality=='missing' else amount


def _build(account,inputs,at,supervisor_usage=None):
    if not isinstance(account,dict) or account.get('schema_version')!=1 or type(account.get('schema_version')) is not int:
        raise ValueError('invalid cost wallet account')
    if not isinstance(inputs,dict):raise ValueError('cost sources require an object')
    start=_time(account['activated_at'])
    if start>at:raise ValueError('cost inception is in the future')
    if inputs.get('schema_version')!=1 or type(inputs.get('schema_version')) is not int:
        raise ValueError('invalid cost source schema')
    if account.get('currency')!='EUR' or inputs.get('currency')!='EUR' or inputs.get('experiment_id')!=account.get('experiment_id'):
        raise ValueError('cost source identity/currency differs from wallet')
    if _time(inputs.get('scope_start'))!=start:raise ValueError('cost source inception differs')
    categories=inputs.get('categories')
    if not isinstance(categories,dict) or set(categories)-COSTS:raise ValueError('invalid operating cost categories')
    result={'schema_version':1,'experiment_id':account['experiment_id'],'currency':'EUR',
            'scope_start':start.isoformat(),'as_of':at.isoformat(),'totals':{},'source_coverage':{}}
    events={};coverage={};plan={}
    duration=at-start
    elapsed=Decimal(duration.days)+(Decimal(duration.seconds)+Decimal(duration.microseconds)/Decimal('1000000'))/Decimal('86400')
    for name in sorted(COSTS):
        row=categories.get(name)
        if row is None:
            result['totals'][name]={'amount':None,'quality':'missing','source':'category_not_configured'}
            plan[name]=None;continue
        _provenance(row);mode=row.get('mode');amount=None;quality='missing'
        if not isinstance(mode,str):raise ValueError('cost source mode is invalid')
        if mode=='recurring':
            plan[name]=row
            rate=_amount(row.get('daily_rate'),row.get('quality'))
            if rate is not None:amount=rate*elapsed;quality='estimated'
            result['source_coverage'][name]={'basis':'declared_daily_rate','quality':'estimated'}
        elif mode=='spot_zero':
            if name!='funding' or row['source']!='unleveraged_spot_no_borrowing' or row.get('quality')!='actual':
                raise ValueError('funding zero requires explicit unleveraged spot provenance')
            plan[name]=row;amount=Decimal('0');quality='actual'
            result['source_coverage'][name]={'basis':'unleveraged_spot_no_borrowing'}
        elif mode in {'events','supervised'}:
            if name=='funding':raise ValueError('borrowed/derivative funding needs a separate signed adapter')
            if mode=='supervised':
                if name!='ai' or row['source']!='supervised_ai_with_subscription_v1':raise ValueError('supervised source requires AI provenance')
                stream=validate_usage_snapshot(supervisor_usage,account,row.get('policy_hash'),now=at)
                subscription=row.get('subscription');_provenance(subscription)
                rate=_amount(subscription.get('daily_rate'),subscription.get('quality'))
                plan[name]=row
                result['supervisor_usage_hash']=_hash(supervisor_usage)
            else:
                stream=row;rate=None
                plan[name]={key:value for key,value in row.items() if key not in {'events','coverage_end'}}
            if _time(stream.get('coverage_start'))!=start:raise ValueError('event coverage starts after inception')
            end=_time(stream.get('coverage_end'));coverage[name]=end.isoformat()
            if not start<=end<=at:raise ValueError('event coverage is future-dated or predates inception')
            items=stream.get('events')
            if not isinstance(items,list):raise ValueError('event coverage requires explicit event list')
            total=Decimal('0');unknown=False;estimated=False
            for event in items:
                if not isinstance(event,dict) or not isinstance(event.get('id'),str) or not event['id']:
                    raise ValueError('cost event requires a stable identity')
                key=(name,event['id'])
                if key in events:raise ValueError('duplicate cost event')
                if event.get('currency','EUR')!='EUR':raise ValueError('cost event currency requires explicit EUR conversion')
                if not start<=_time(event.get('occurred_at'))<=end:raise ValueError('event outside asserted coverage')
                events[key]=_json(event)
                charge=_amount(event.get('amount'),event.get('quality'))
                if charge is None:unknown=True
                else:total+=charge
                estimated=estimated or event.get('quality')=='estimated'
            stale=at-end>timedelta(minutes=5)
            result['source_coverage'][name]={'coverage_end':end.isoformat(),'status':'stale' if stale else 'current',
                                            'events':len(items),'unpriced':unknown}
            if not unknown and not stale:amount=total;quality='estimated' if estimated else 'actual'
            if mode=='supervised':
                result['source_coverage'][name]['subscription_basis']='declared_standalone_daily_rate'
                result['source_coverage'][name]['subscription_known']=rate is not None
                if rate is None or amount is None:amount=None;quality='missing'
                else:amount+=rate*elapsed;quality='estimated'
        else:raise ValueError('unsupported cost source mode')
        result['totals'][name]={'amount':None if amount is None else str(amount),'quality':quality,'source':row['source']}
    result['accrued_through']=at.isoformat()
    # A fresh projection cannot extend an event sensor's underlying coverage.
    if coverage:result['as_of']=min(_time(stamp) for stamp in coverage.values()).isoformat()
    result['source_hash']=_hash(inputs)
    return result,plan,events,coverage


def refresh_wallet_costs(state_dir,*,now=None,supervisor_usage=_LOCAL_USAGE):
    state_dir=Path(state_dir);source=state_dir/'wallet_cost_sources.json';journal=state_dir/'wallet_cost_accrual.sqlite'
    if not source.exists():
        if journal.exists():raise ValueError('initialized cost sources are missing')
        return None
    account=json.loads((state_dir/'wallet_account.json').read_text(encoding='utf-8'))
    inputs=json.loads(source.read_text(encoding='utf-8'));at=_now(now)
    categories=inputs.get('categories',{}) if isinstance(inputs,dict) else {}
    ai=categories.get('ai',{}) if isinstance(categories,dict) else {}
    if isinstance(ai,dict) and ai.get('mode')=='supervised' and supervisor_usage is _LOCAL_USAGE:
        path=state_dir/'supervisor_usage_snapshot.json'
        supervisor_usage=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    try:result,plan,events,coverage=_build(account,inputs,at,supervisor_usage)
    except DecimalException as error:raise ValueError('cost arithmetic is invalid') from error
    db=_connect(journal)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY,account_hash TEXT,plan_hash TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS events (category TEXT,event_id TEXT,payload TEXT,PRIMARY KEY(category,event_id))')
        db.execute('CREATE TABLE IF NOT EXISTS coverage (category TEXT PRIMARY KEY,covered_through TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS observations (at TEXT PRIMARY KEY,source_hash TEXT,payload TEXT)')
        binding=db.execute('SELECT account_hash,plan_hash FROM binding WHERE id=1').fetchone()
        expected=(_hash(account),_hash(plan))
        if binding is None:db.execute('INSERT INTO binding VALUES (1,?,?)',expected)
        elif tuple(binding)!=expected:raise ValueError('observed cost account or recurring/source plan changed')
        observed_events={}
        for category,event_id,payload in db.execute('SELECT category,event_id,payload FROM events'):
            observed_events[(category,event_id)]=payload
            if events.get((category,event_id))!=payload:raise ValueError('observed cost event changed or disappeared')
        for (category,event_id),payload in events.items():
            previous=db.execute('SELECT covered_through FROM coverage WHERE category=?',(category,)).fetchone()
            if (category,event_id) not in observed_events and previous is not None and _time(json.loads(payload)['occurred_at'])<=_time(previous[0]):
                raise ValueError('new event contradicts previously complete coverage; reconciliation required')
            db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?)',(category,event_id,payload))
        for category,stamp in coverage.items():
            previous=db.execute('SELECT covered_through FROM coverage WHERE category=?',(category,)).fetchone()
            if previous is not None and _time(stamp)<_time(previous[0]):raise ValueError('cost coverage regressed')
            db.execute('INSERT OR REPLACE INTO coverage VALUES (?,?)',(category,stamp))
        previous=db.execute('SELECT at FROM observations ORDER BY at DESC LIMIT 1').fetchone()
        if previous is not None and at<_time(previous[0]):raise ValueError('cost observation time regressed')
        frozen=db.execute('SELECT source_hash,payload FROM observations WHERE at=?',(at.isoformat(),)).fetchone()
        if frozen is not None and (frozen[0],_json(load_packet(db,frozen[1])))!=(result['source_hash'],_json(result)):
            raise ValueError('conflicting cost evidence at frozen timestamp')
        db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?)',(at.isoformat(),result['source_hash'],store_record(db,result)))
        db.commit()
        # Retain the exclusive journal lock through publication: an older
        # refresh must never overwrite a newer already-published projection.
        target=state_dir/'wallet_costs.json';temporary=_temporary(target,(_json(result)+'\n').encode())
        os.replace(temporary,target);_sync_dir(state_dir)
    except BaseException:
        db.rollback();raise
    finally:db.close()
    return result
