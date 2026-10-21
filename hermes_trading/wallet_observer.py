"""Prospective paper account observations from authoritative positions/quotes."""
from datetime import timedelta
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path

from hermes_trading.domain import json_value
from hermes_trading.entry_contracts import EntryContractResolver
from hermes_trading.paper_publication import _temporary, _sync_dir
from hermes_trading.positions import SimulationSettings
from hermes_trading.reflection_supervision import _hash, _json, _now, _time, load_owner_policy
from hermes_trading.wallet_metrics import CostAccrual, LiquidationMark, WalletJournal, value_spot_wallet, rolling_wallet_metrics


def load_wallet_config(state_dir):
    path=Path(state_dir)/'wallet_account.json'
    if not path.exists():
        policy=load_owner_policy(state_dir)
        if (Path(state_dir)/'wallet_equity.sqlite').exists() or (policy is not None and policy.get('wallet_required') is True):
            raise ValueError('initialized wallet configuration is missing')
        return None
    config=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config,dict) or type(config.get('schema_version')) is not int or config['schema_version']!=1:
        raise ValueError('invalid wallet account schema')
    policy=load_owner_policy(state_dir)
    if policy is not None and config.get('experiment_id')!=policy['experiment_id']:
        raise ValueError('wallet and supervisor experiments differ')
    return config


def _costs(state_dir,config,current,*,snapshot=None):
    path=Path(state_dir)/'wallet_costs.json'
    def unknown(reason):
        return {name:CostAccrual(None,config['currency'],reason,'missing') for name in ('hosting','ai','tools','funding')}
    if snapshot is not None:
        if snapshot.get('error'):raise ValueError(snapshot['error'])
        if snapshot.get('present') is not True:return unknown('cost_accruals_not_configured'),{'status':'missing'}
        data=snapshot['data']
    else:
        if not path.exists(): return unknown('cost_accruals_not_configured'),{'status':'missing'}
        data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or type(data.get('schema_version')) is not int or data['schema_version']!=1:
        raise ValueError('invalid wallet cost schema')
    if data.get('experiment_id')!=config['experiment_id'] or data.get('currency')!=config['currency']:
        raise ValueError('wallet cost experiment/currency differs')
    if _time(data.get('scope_start'))!=_time(config['activated_at']):
        raise ValueError('cumulative cost scope differs from wallet inception')
    stamp=_time(data.get('as_of'))
    if stamp>current: raise ValueError('future cost coverage cannot enter current valuation')
    coverage={'as_of':stamp.isoformat(),'status':'current'}
    if current-stamp>timedelta(minutes=5):
        coverage['status']='stale'; return unknown('stale_cost_coverage'),coverage
    totals=data.get('totals')
    if not isinstance(totals,dict): raise ValueError('cost totals must be objects')
    result={}
    for name,event in totals.items():
        if not isinstance(event,dict): raise ValueError('cost accrual must be an object')
        result[name]=CostAccrual(event.get('amount'),config['currency'],event.get('source'),event.get('quality'))
    return result,coverage


def value_current_wallet(state_dir,repository,snapshots,strategy,*,now=None,cost_snapshot=None):
    config=load_wallet_config(state_dir)
    if config is None: return None
    current=_now(now); activation=_time(config['activated_at'])
    if activation>current: raise ValueError('wallet activation is in the future')
    # Bind and validate the immutable account before sizing can mutate positions.
    journal_config={key:config[key] for key in ('experiment_id','currency','starting_balance','activated_at')}
    with WalletJournal(Path(state_dir)/'wallet_equity.sqlite',journal_config):
        pass
    positions=[json_value(position) for position in repository.portfolio_positions()]
    if any(_time(position.get('opened_at'))<activation for position in positions):
        raise ValueError('pre-experiment positions require account reconciliation')
    costs,coverage=_costs(state_dir,config,current,snapshot=cost_snapshot)
    marks={}; mark_versions={}
    resolver=EntryContractResolver(state_dir,strategy)
    for position in positions:
        if position.get('status')!='open': continue
        asset=position['asset']
        if asset in mark_versions: raise ValueError('multiple open positions per asset require separate liquidation contracts')
        effective=resolver(asset,position.get('strategy_version'))
        mark_versions[asset]=str(position['strategy_version'])
        snapshot=snapshots.get(asset)
        price=getattr(snapshot,'price',None)
        if not price or price.get('bid') is None or not price.get('received_at'): continue
        settings=SimulationSettings.from_strategy(effective)
        marks[asset]=LiquidationMark(price['bid'],_time(price['received_at']),settings.taker_fee_rate,
                                    settings.slippage_rate,str(price.get('source') or 'received_bid')+':exchange_bid_age_unknown')
    observation=value_spot_wallet(config['starting_balance'],config['currency'],positions,marks,costs,now=current)
    from hermes_trading.venue_constraints import parse_venue_constraints,VenueConstraintsError
    constraints={};marked=Decimal(0);marked_complete=True
    for position in positions:
        if position.get('status')!='open':continue
        asset=position['asset'];mark=observation['mark_provenance'].get(asset)
        record=getattr(snapshots.get(asset),'venue_rules',None)
        try:
            if mark is None:
                marked_complete=False
                raise VenueConstraintsError('current liquidation quote unavailable')
            quantity=Decimal(position['quantity']);bid=Decimal(mark['bid'])
            marked+=quantity*bid
            mark['venue_rules']=deepcopy(record)
            venue=parse_venue_constraints(record,asset=asset,at=current)
            executable=bid*(1-Decimal(mark['slippage_rate']))
            venue.validate_order(quantity,executable)
            settings=SimulationSettings.from_strategy(resolver(asset,position['strategy_version']))
            if quantity<settings.minimum_order_quantity or quantity*executable<settings.minimum_order_quote:
                raise VenueConstraintsError('original configured liquidation minimum not met')
            constraints[asset]={'status':'executable','source_sha256':venue.source_sha256}
        except VenueConstraintsError as error:
            constraints[asset]={'status':'unavailable','reason':str(error)}
            observation['missing'].append('liquidation:'+asset)
    complete=all(row['status']=='executable' for row in constraints.values())
    observation.update(execution_venue_protocol='dated_rules_v1',liquidation_constraints=constraints,
                       liquidation_complete=complete,marked_inventory_value=str(marked) if marked_complete else None,
                       marked_equity_before_liquidation_costs=(str(Decimal(observation['trading_cash'])+marked-Decimal(observation['known_accrued_costs']))
                           if marked_complete and not any(item.startswith('cost:') for item in observation['missing']) else None))
    if not complete:
        observation.update(open_liquidation_value=None,known_cost_equity=None,net_equity=None)
    observation['missing']=sorted(set(observation['missing']))
    observation['experiment_id']=config['experiment_id']
    observation['activated_at']=activation.isoformat()
    observation['account_config_hash']=_hash(config)
    observation['cost_coverage']=coverage
    observation['mark_entry_versions']=mark_versions
    from hermes_trading.entry_cost_reserve import reserve_enabled,frozen_reserve_context
    import sqlite3
    try:
        if reserve_enabled(strategy):observation['entry_cost_reserve']=frozen_reserve_context(state_dir,observation)
    except (ValueError,OSError,ArithmeticError,sqlite3.Error,TypeError,KeyError) as error:
        observation['entry_cost_reserve']={'status':'blocked','error':str(error)}
    return observation


def observe_wallet(state_dir,repository,snapshots,strategy,*,now=None,cost_snapshot=None):
    state_dir=Path(state_dir); config=load_wallet_config(state_dir)
    if config is None: return None
    current=_now(now)
    observation=value_current_wallet(state_dir,repository,snapshots,strategy,now=current,cost_snapshot=cost_snapshot)
    return record_wallet_observation(state_dir,observation,now=current)


def record_wallet_observation(state_dir,observation,*,now=None):
    """Journal a bound valuation already settled by the shared paper worker."""
    state_dir=Path(state_dir);config=load_wallet_config(state_dir);current=_now(now)
    if config is None or observation.get('account_config_hash')!=_hash(config) or _time(observation.get('at'))!=current:
        raise ValueError('settled wallet observation identity or time differs')
    journal_config={key:config[key] for key in ('experiment_id','currency','starting_balance','activated_at')}
    with WalletJournal(state_dir/'wallet_equity.sqlite',journal_config) as journal:
        journal.append(observation,now=current)
        rows=journal.observations(start=current-timedelta(days=30,minutes=2),end=current)
    metrics=rolling_wallet_metrics(rows,config['currency'],now=current)
    reasons=['independent_profitability_evidence_not_qualified']
    if observation['net_equity'] is None: reasons.append('current_net_equity_unknown')
    if metrics['status']!='measured': reasons.append('rolling_30d_'+metrics['status'])
    report={'schema_version':1,'status':'observed','observed_at':current.isoformat(),'experiment_id':config['experiment_id'],
            'currency':config['currency'],'observation':observation,'calendar_metrics':metrics,
            'gate':{'status':'blocked','basis':'observed_wallet_metrics','currency':config['currency'],
                    'reasons':reasons,'eligible_for_real_trading':False}}
    target=state_dir/'wallet_metrics.json'; temporary=_temporary(target,_json(report).encode('utf-8'))
    try: os.replace(temporary,target)
    finally: temporary.unlink(missing_ok=True)
    _sync_dir(state_dir)
    return report
