"""Fixed-capital long-spot paper valuation and observed calendar performance.

Closed position P&L and fill prices are already net of executed trading costs.
This module neither reconstructs historical quotes nor qualifies profitability.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean, stdev

from hermes_trading.reflection_supervision import _json, _now, _time
from hermes_trading.paired_packet_storage import store_record,load_packet

ZERO=Decimal('0')
COSTS={'hosting','ai','tools','funding'}


@dataclass(frozen=True)
class CostAccrual:
    amount: Decimal | None
    currency: str
    source: str
    quality: str


@dataclass(frozen=True)
class LiquidationMark:
    bid: Decimal | None
    observed_at: object
    exit_fee_rate: Decimal | None
    slippage_rate: Decimal | None
    source: str


def _decimal(value):
    if type(value) not in {str,int,Decimal}:
        raise ValueError('financial values require Decimal, integer or decimal text')
    try: result=Decimal(value)
    except InvalidOperation as error: raise ValueError('invalid decimal value') from error
    if not result.is_finite(): raise ValueError('financial value must be finite')
    return result


def value_spot_wallet(starting_balance,currency,positions,marks,cost_totals,*,now=None,max_mark_age=timedelta(minutes=5)):
    current=_now(now); initial=_decimal(starting_balance)
    if initial <= 0 or not isinstance(currency,str) or not currency:
        raise ValueError('positive initial capital and explicit currency are required')
    if max_mark_age <= timedelta(0): raise ValueError('mark freshness must be positive')
    if set(cost_totals)-COSTS:
        raise ValueError('only accrued operating/funding costs belong here; trading fees/slippage are already embedded')
    missing=[]; cost_sum=ZERO; provenance={}
    for name in sorted(COSTS):
        event=cost_totals.get(name)
        if event is None:
            missing.append('cost:'+name); continue
        if not isinstance(event,CostAccrual) or event.currency != currency or not isinstance(event.source,str) or not event.source.strip():
            raise ValueError('accrued costs require matching currency and provenance')
        if event.quality not in {'actual','estimated','missing'}: raise ValueError('unsupported accrued-cost quality')
        provenance[name]={'source':event.source,'quality':event.quality,'amount':None if event.amount is None else str(_decimal(event.amount))}
        if event.amount is None or event.quality=='missing':
            missing.append('cost:'+name); continue
        amount=_decimal(event.amount)
        if name != 'funding' and amount < 0: raise ValueError('operating expenses cannot be negative')
        cost_sum+=amount
    identities=set(); closed_net=ZERO; basis=ZERO; entry_fees=ZERO; liquidation=ZERO; missing_mark=False
    mark_provenance={}
    for row in positions:
        if not isinstance(row,dict): raise ValueError('positions must be objects')
        identity=row.get('position_id'); asset=row.get('asset')
        if not isinstance(identity,str) or not identity or identity in identities:
            raise ValueError('positions require unique identities')
        identities.add(identity)
        if not isinstance(asset,str) or asset.split('/')[-1] != currency or asset.count('/') != 1:
            raise ValueError('long spot positions must match account quote currency')
        if row.get('side','long') not in {'long','buy'}: raise ValueError('short/derivative accounting is not supported by this valuation')
        opened=_time(row.get('opened_at'))
        if opened>current: raise ValueError('future position cannot enter a valuation')
        quantity=_decimal(row.get('quantity')); entry=_decimal(row.get('entry_price')); fees=_decimal(row.get('fees'))
        if quantity<=0 or entry<=0 or fees<0: raise ValueError('position quantity/price must be positive and fees nonnegative')
        if row.get('status')=='closed':
            closed=_time(row.get('closed_at'))
            if not opened<=closed<=current: raise ValueError('position close time is invalid')
            closed_net+=_decimal(row.get('realized_pnl'))
            continue
        if row.get('status')!='open': raise ValueError('unsettled/error positions require explicit reconciliation')
        basis+=quantity*entry; entry_fees+=fees
        mark=marks.get(asset)
        if mark is None:
            missing.append('mark:'+asset); missing_mark=True; continue
        if not isinstance(mark,LiquidationMark) or not isinstance(mark.source,str) or not mark.source.strip():
            raise ValueError('liquidation mark provenance is required')
        stamp=_now(mark.observed_at)
        if not timedelta(0)<=current-stamp<=max_mark_age: raise ValueError('liquidation mark is stale or future-dated')
        if mark.bid is None or mark.exit_fee_rate is None or mark.slippage_rate is None:
            missing.append('mark:'+asset); missing_mark=True; continue
        bid=_decimal(mark.bid); fee=_decimal(mark.exit_fee_rate); slip=_decimal(mark.slippage_rate)
        if bid<=0 or not ZERO<=fee<1 or not ZERO<=slip<1: raise ValueError('invalid liquidation price/cost model')
        liquidation+=quantity*bid*(1-slip)*(1-fee)
        mark_provenance[asset]={'bid':str(bid),'observed_at':stamp.isoformat(),'exit_fee_rate':str(fee),'slippage_rate':str(slip),'source':mark.source}
    trading_cash=initial+closed_net-basis-entry_fees
    known_equity=None if missing_mark else trading_cash+liquidation-cost_sum
    return {'schema_version':1,'valuation_model':'long_spot_net_v1','at':current.isoformat(),'currency':currency,
            'external_flow':'0','starting_balance':str(initial),'closed_net_pnl':str(closed_net),
            'trading_cash':str(trading_cash),'open_basis':str(basis),'open_entry_fees':str(entry_fees),
            'open_liquidation_value':None if missing_mark else str(liquidation),'known_accrued_costs':str(cost_sum),
            'known_cost_equity':None if known_equity is None else str(known_equity),
            'net_equity':None if missing or known_equity is None else str(known_equity),
            'missing':sorted(set(missing)),'cost_provenance':provenance,'mark_provenance':mark_provenance,
            'evidence':'paper_liquidation_estimate_not_profitability_qualification'}


def rolling_wallet_metrics(observations,currency,*,now=None,max_gap=timedelta(minutes=5),boundary_tolerance=timedelta(minutes=2)):
    end=_now(now); start=end-timedelta(days=30)
    if max_gap<=timedelta(0) or not timedelta(0)<=boundary_tolerance<timedelta(days=1):
        raise ValueError('invalid observation spacing policy')
    if not isinstance(currency,str) or not currency: raise ValueError('explicit metric currency is required')
    points={}
    for row in observations:
        if not isinstance(row,dict) or row.get('currency')!=currency:
            raise ValueError('wallet observations require one currency')
        if _decimal(row.get('external_flow'))!=0:
            raise ValueError('external flows require flow-adjusted valuations')
        stamp=_time(row.get('at'))
        if stamp>end: continue
        value=None if row.get('net_equity') is None else _decimal(row['net_equity'])
        if stamp in points and points[stamp]!=value: raise ValueError('conflicting equity observations')
        points[stamp]=value
    ordered=sorted(points)
    report={'status':'incomplete','window_start':start.isoformat(),'window_end':end.isoformat(),
            'currency':currency,'return_30d':None,'max_drawdown_30d':None,'daily_return_count':0,
            'annualized_daily_return_ratio_zero_reference':None,'reasons':[]}
    boundaries=[]
    for day in range(31):
        boundary=start+timedelta(days=day)
        candidates=[stamp for stamp in ordered if boundary-boundary_tolerance<=stamp<=boundary]
        if not candidates:
            report['reasons'].append('missing_calendar_boundary'); return report
        boundaries.append(max(candidates))
    if any(left>=right for left,right in zip(boundaries,boundaries[1:])):
        raise ValueError('calendar endpoints must be distinct and increasing')
    in_window=[stamp for stamp in ordered if boundaries[0]<=stamp<=boundaries[-1]]
    if any(points[stamp] is None for stamp in in_window): report['reasons'].append('unknown_net_equity')
    if any(right-left>max_gap for left,right in zip(in_window,in_window[1:])): report['reasons'].append('observation_gap')
    if report['reasons']: return report
    values=[points[stamp] for stamp in in_window]
    if values[0]<=0:
        report['status']='insolvent'; report['reasons'].append('nonpositive_start_equity'); return report
    report['return_30d']=str(values[-1]/values[0]-1)
    peak=values[0]; worst=ZERO
    for value in values:
        peak=max(peak,value); worst=max(worst,(peak-value)/peak)
    report['max_drawdown_30d']=str(worst)
    report['actual_boundary_times']=[stamp.isoformat() for stamp in boundaries]
    report['drawdown_basis']='observed_equity_samples'
    report['maximum_allowed_observation_gap_seconds']=max_gap.total_seconds()
    if any(value<=0 for value in values):
        report['status']='insolvent'; report['reasons'].append('nonpositive_equity'); return report
    returns=[points[right]/points[left]-1 for left,right in zip(boundaries,boundaries[1:])]
    report['status']='measured'; report['daily_return_count']=len(returns)
    report['daily_return_mean']=str(sum(returns,ZERO)/len(returns))
    daily=[float(value) for value in returns]
    if not all(math.isfinite(value) for value in daily):
        report['status']='incomplete'; report['reasons'].append('daily_statistics_numeric_range'); return report
    volatility=stdev(daily)
    report['daily_return_sample_volatility']=volatility
    if volatility>0:
        ratio=mean(daily)/volatility*math.sqrt(365)
        if math.isfinite(ratio): report['annualized_daily_return_ratio_zero_reference']=ratio
        else: report['reasons'].append('daily_ratio_numeric_range')
    else:
        report['reasons'].append('daily_ratio_undefined_zero_variance')
    return report


class WalletJournal:
    def __init__(self,path,config):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        if not isinstance(config,dict) or not isinstance(config.get('experiment_id'),str) or not config['experiment_id']:
            raise ValueError('explicit wallet experiment identity is required')
        if not isinstance(config.get('currency'),str) or not config['currency'] or _decimal(config.get('starting_balance'))<=0:
            raise ValueError('wallet currency and positive capital are required')
        self.config=json.loads(_json({**config,'activated_at':_time(config.get('activated_at')).isoformat()}))
        self.db=sqlite3.connect(self.path,timeout=0,isolation_level=None)
        try:
            self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('CREATE TABLE IF NOT EXISTS wallet_config (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS observations (at TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            prior=self.db.execute('SELECT config FROM wallet_config WHERE id=1').fetchone()
            if prior is None: self.db.execute('INSERT INTO wallet_config VALUES (1,?)',(_json(self.config),))
            elif prior[0]!=_json(self.config): raise ValueError('wallet experiment cannot be reset/reconfigured')
            self.db.commit()
        except BaseException:
            self.db.rollback(); self.db.close(); raise

    def append(self,observation,*,now=None):
        row=json.loads(_json(observation)); stamp=_time(row.get('at'))
        row['at']=stamp.isoformat()
        if row.get('currency')!=self.config['currency'] or not _time(self.config['activated_at'])<=stamp<=_now(now):
            raise ValueError('observation currency/time differs from experiment')
        if _decimal(row.get('external_flow'))!=0: raise ValueError('external flows require a different accounting contract')
        if _decimal(row.get('starting_balance'))!=_decimal(self.config['starting_balance']):
            raise ValueError('observation starting capital differs from wallet experiment')
        if row.get('net_equity') is not None: _decimal(row['net_equity'])
        try:
            self.db.execute('BEGIN IMMEDIATE')
            existing=self.db.execute('SELECT payload FROM observations WHERE at=?',(row['at'],)).fetchone()
            if existing is not None:
                if _json(load_packet(self.db,existing[0]))!=_json(row): raise ValueError('wallet observation cannot be rewritten')
                self.db.commit(); return False
            latest=self.db.execute('SELECT at FROM observations ORDER BY at DESC LIMIT 1').fetchone()
            if latest and stamp<=_time(latest[0]): raise ValueError('wallet observations cannot be inserted retrospectively')
            self.db.execute('INSERT INTO observations VALUES (?,?)',(row['at'],store_record(self.db,row))); self.db.commit()
            return True
        except BaseException:
            self.db.rollback(); raise

    def observations(self,*,start=None,end=None):
        conditions=[]; values=[]
        for operator,boundary in (('>=',start),('<=',end)):
            if boundary is not None:
                conditions.append('at '+operator+' ?'); values.append(_now(boundary).isoformat())
        query='SELECT at,payload FROM observations'
        if conditions: query+=' WHERE '+' AND '.join(conditions)
        result=[]
        for stamp,payload in self.db.execute(query+' ORDER BY at',values):
            observation=load_packet(self.db,payload)
            if observation.get('at')!=stamp:raise ValueError('wallet observation timestamp differs from primary key')
            result.append(observation)
        return result

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()
