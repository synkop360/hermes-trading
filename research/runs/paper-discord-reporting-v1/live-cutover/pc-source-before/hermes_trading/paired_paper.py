"""One durable common-input batch for eight independent paper accounts.

No collector loop, model access, deployment or live order is enabled here.
"""
from copy import deepcopy
from datetime import datetime,timedelta
from decimal import Decimal
import json
import hashlib
import base64
from pathlib import Path
import uuid
import sqlite3

from hermes_trading.domain import json_value
from hermes_trading.loop import run_cycle,_load_yaml
from hermes_trading.market_data import ClosedCandle,MarketSnapshot,timeframe_interval
from hermes_trading.paper_publication import _archive_once,_release_hashes
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _connect,_hash,_json,_now,_time
from hermes_trading.wallet_observer import observe_wallet,load_wallet_config,value_current_wallet,record_wallet_observation
from hermes_trading.execution_policy import strategy_is_frozen
from hermes_trading.paired_risk import initial_risk_state,update_risk_state,is_halted,validate_risk_state
from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for
from hermes_trading.positions import PositionEngine
from hermes_trading.entry_contracts import EntryContractResolver
from hermes_trading.loop import resolve_asset_strategy
from hermes_trading.prospective_execution import require_taker_strategy,execution_quote
from hermes_trading.wallet_cost_accrual import refresh_wallet_costs
from hermes_trading.paired_packet_storage import store_packet,store_record,load_packet


def _encode(value):
    if isinstance(value,Decimal):return {'__paired_type':'decimal','value':str(value)}
    if isinstance(value,datetime):return {'__paired_type':'datetime','value':value.isoformat()}
    if isinstance(value,dict):
        if '__paired_type' in value:raise ValueError('reserved paired encoding key')
        return {key:_encode(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [_encode(item) for item in value]
    return value


def _decode(value):
    if isinstance(value,dict):
        if set(value)=={'__paired_type','value'}:
            if value['__paired_type']=='decimal':return Decimal(value['value'])
            if value['__paired_type']=='datetime':return _time(value['value'])
            raise ValueError('unknown paired encoding type')
        return {key:_decode(item) for key,item in value.items()}
    if isinstance(value,list):return [_decode(item) for item in value]
    return value


def _snapshot(value):
    row=_decode(value)
    if row.get('candle') is not None:row['candle']=ClosedCandle(**row['candle'])
    return MarketSnapshot(**row)


class _FrozenMarket:
    def __init__(self,snapshots):self.snapshots=snapshots
    async def collect(self,assets):return deepcopy(self.snapshots)


def _profiles(root,manifest):
    selection=resolve_primary(manifest)
    if selection.versioned:
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
        reporting_protocol_for(manifest,REPORTING_PROTOCOL)
    if manifest.get('schema_version')!=1 or manifest.get('status')!='paper_ready':
        raise ValueError('paired experiment is not explicitly paper ready')
    result={}
    for profile in manifest.get('profiles',[]):
        arm=profile.get('arm'); wallet=profile.get('wallet_eur'); name=profile.get('directory')
        if arm not in ('adaptive','control') or type(wallet)is not int or wallet not in (50,100,150,200):
            raise ValueError('invalid paired arm or wallet')
        if not isinstance(name,str) or Path(name).name!=name or name in ('.','..'):
            raise ValueError('paired profile path must be local')
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or (arm,wallet) in result:
            raise ValueError('paired profile escapes root or is duplicated')
        config=load_wallet_config(path)
        if config is None or config.get('currency')!='EUR' or Decimal(config['starting_balance'])!=wallet:
            raise ValueError('paired wallet must be explicitly activated with its EUR capital')
        _time(config['activated_at'])
        require_taker_strategy(_load_yaml(path/'strategy.yaml'))
        result[(arm,wallet)]=(path,profile,config)
    if set(result)!={(arm,wallet) for arm in ('adaptive','control') for wallet in (50,100,150,200)}:
        raise ValueError('paired experiment requires exactly eight accounts')
    if len({row[2]['experiment_id'] for row in result.values()})!=1:
        raise ValueError('paired accounts require one experiment identity')
    protocol=manifest.get('execution_venue_protocol')
    if protocol is not None:
        if protocol!='dated_rules_v1':raise ValueError('unsupported venue execution protocol')
        from hermes_trading.venue_candidate import validate_activation_venue
        try:validate_activation_venue(root)
        except (OSError,ValueError,TypeError,KeyError) as error:
            raise ValueError('venue activation evidence unavailable or inconsistent') from error
    return result


def _causal_strategy(db,current,source_bytes,snapshots,*,now,baseline_hash,baseline_path):
    closes={snapshot.candle.close_timestamp for snapshot in snapshots.values() if snapshot.candle is not None}
    if any(stamp>now for stamp in closes):raise ValueError('paired batch contains a future closed candle')
    if len(closes)>1:raise ValueError('paired batch candles must share one close timestamp')
    table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
    records=[]
    if table:
        for sequence,request,receipt in db.execute('SELECT rowid,request,receipt FROM publications WHERE receipt IS NOT NULL'):
            item=json.loads(request,parse_float=Decimal);ack=json.loads(receipt)
            records.append((_time(ack['published_at']),sequence,item,ack))
    records.sort(key=lambda row:(row[0],row[1]),reverse=True)
    if not records:
        if str(current.get('version'))!='1' or hashlib.sha256(source_bytes).hexdigest()!=baseline_hash:
            raise ValueError('initial adaptive strategy differs from manifest baseline')
        return current
    if not baseline_path.exists() or hashlib.sha256(baseline_path.read_bytes()).hexdigest()!=baseline_hash:
        raise ValueError('adaptive original baseline archive differs from manifest')
    if _encode(_load_yaml(baseline_path))!=_encode(records[-1][2]['base_strategy']):
        raise ValueError('publication chain does not start from manifest baseline')
    if records[0][3]['candidate_sha256']!=hashlib.sha256(source_bytes).hexdigest():
        raise ValueError('adaptive strategy differs from its durable publication receipt')
    if not closes:return current
    close=next(iter(closes));chosen=current
    for published,sequence,request,receipt in records:
        if str(request['candidate']['version'])!=str(chosen['version']):
            raise ValueError('adaptive publication chain is discontinuous')
        if close>published:return chosen
        chosen=request['base_strategy']
    return chosen


def _capture_cost_source(path):
    if not path.exists():return {'present':False,'data':None}
    try:
        raw=path.read_bytes()
    except OSError as error:
        return {'present':True,'data':None,'error':'cost source unreadable: '+type(error).__name__}
    result={'present':True,'raw_base64':base64.b64encode(raw).decode('ascii'),
            'raw_sha256':hashlib.sha256(raw).hexdigest()}
    try:result['data']=json.loads(raw)
    except (ValueError,UnicodeError) as error:
        result.update(data=None,error='cost source parse failure: '+type(error).__name__)
    return result


def _prepare_archives(primary,profiles):
    for (arm,wallet),(path,profile,config) in profiles.items():
        (path/'history').mkdir(exist_ok=True)
        if arm=='adaptive' and path!=primary:
            for archive in sorted((primary/'history').glob('v*.yaml')):
                _archive_once(path/'history'/archive.name,archive.read_bytes())
    return {f'{arm}_{wallet}':{archive.name:hashlib.sha256(archive.read_bytes()).hexdigest()
            for archive in sorted((path/'history').glob('v*.yaml'))}
            for (arm,wallet),(path,profile,config) in profiles.items()}


async def process_paper_account(state_dir,strategy,snapshots,assets,*,now=None,valuation=None,cost_snapshot=None,entries_blocked=False):
    repo=StateRepository.open(state_dir/'trading.db');repo.migrate()
    try:
        cycle=await run_cycle(assets,repo,ArtifactProjector(state_dir),_FrozenMarket(snapshots),strategy,'training',wallet_state_dir=state_dir,wallet_valuation_snapshot=valuation,wallet_entries_blocked=entries_blocked,prospective_at=_now(now))
        measurement_error=None
        try:
            observation=value_current_wallet(state_dir,repo,snapshots,strategy,now=_now(now),cost_snapshot=cost_snapshot)
            net_equity=observation['net_equity'];report={'observed_at':_now(now).isoformat()}
        except ValueError as error:
            measurement_error=str(error);net_equity=None
            report={'schema_version':1,'status':'measurement_blocked','observed_at':_now(now).isoformat(),
                    'recorded_at':_now().isoformat(),'error':measurement_error,
                    'gate':{'status':'blocked','basis':'observed_wallet_metrics','eligible_for_real_trading':False}}
        identities={result.decision.decision_id for result in cycle.results if result.decision is not None}
        decisions=[row for row in repo.export_canonical_tables()['decisions'] if row['decision_id'] in identities]
        return {'status':'degraded' if cycle.degraded or measurement_error is not None else 'observed','decisions':decisions,
                'net_equity':net_equity,'measurement_error':measurement_error,'observed_at':report['observed_at'],'recorded_at':_now().isoformat(),
                'contract_errors':list(cycle.contract_errors)}
    finally:repo.close()


def _liquidate_halted_account(path,strategy,snapshots,*,at,batch_id,reason='paired_drawdown_stop'):
    repo=StateRepository.open(path/'trading.db');repo.migrate()
    try:
        engine=PositionEngine(repo,ArtifactProjector(path),entries_allowed=False,
                              entry_contract_resolver=EntryContractResolver(path,strategy),force_exit_reason=reason,prospective_at=at)
        for position in repo.active_positions():
            if position.status.value!='open':continue
            snapshot=snapshots.get(position.asset);price=getattr(snapshot,'price',None)
            if not price or price.get('bid') is None or not price.get('received_at'):continue
            if isinstance(price['bid'],(bool,float)):continue
            try:
                age=at-_time(price['received_at']);bid=Decimal(price['bid'])
            except (ValueError,TypeError,ArithmeticError):continue
            if not timedelta(0)<=age<=timedelta(minutes=5):continue
            if not bid.is_finite() or bid<=0:continue
            # This is a quote-backed risk event, not a fabricated chart candle.
            event=MarketSnapshot(position.asset,'paired-risk:'+batch_id,'available','evaluate',reason,
                ClosedCandle(at-timedelta(microseconds=1),at,bid,bid,bid,bid,None),price=deepcopy(price),
                venue_rules=deepcopy(snapshot.venue_rules))
            engine.process_candle(event,resolve_asset_strategy(strategy,position.asset))
        return not repo.active_positions()
    finally:repo.close()


def _measure_final_account(path,strategy,snapshots,*,at,cost_snapshot,halted,halt_reason=None):
    repo=StateRepository.open(path/'trading.db');repo.migrate()
    try:
        try:
            observation=value_current_wallet(path,repo,snapshots,strategy,now=at,cost_snapshot=cost_snapshot)
            equity=observation['net_equity'];error=None
        except ValueError as failure:
            error=str(failure);equity=None;observation=None
        result={'observation':observation,'net_equity':equity,'measurement_error':error,
                'positions_hash':_hash(json_value(repo.portfolio_positions())),
                'halted':halted,'liquidation_pending':halted and bool(repo.active_positions())}
        if halt_reason is not None:result['halt_reason']=halt_reason
        return result
    finally:repo.close()


def _journal_final_account(path,measurement,*,at):
    repo=StateRepository.open(path/'trading.db');repo.migrate()
    try:
        if _hash(json_value(repo.portfolio_positions()))!=measurement['positions_hash']:
            raise ValueError('settled positions changed before final wallet observation')
    finally:repo.close()
    if measurement['observation'] is not None:
        report=record_wallet_observation(path,measurement['observation'],now=at)
        if measurement['halted']:
            report['gate']['reasons'].append(measurement.get('halt_reason') or 'paired_drawdown_halt')
            ArtifactProjector(path).write_snapshot('wallet_metrics.json',report)
    else:
        report={'schema_version':1,'status':'measurement_blocked','observed_at':at.isoformat(),
                'recorded_at':_now().isoformat(),'error':measurement['measurement_error'],
                'gate':{'status':'blocked','basis':'observed_wallet_metrics','eligible_for_real_trading':False}}
        ArtifactProjector(path).write_snapshot('wallet_metrics.json',report)
    return {key:value for key,value in measurement.items() if key not in {'observation','positions_hash'}} | {
            'observed_at':at.isoformat(),'recorded_at':_now().isoformat()}


class PairedDeadlineReached(TimeoutError):
    pass


class PairedStopReached(RuntimeError):
    """Operator stopped collection before a new financial packet was frozen."""


def market_source_status(snapshots,at,*,packet_schema=2):
    result={}
    for asset,snapshot in snapshots.items():
        row={'valid':False,'timeframe':snapshot.timeframe,'received_at':(snapshot.price or {}).get('received_at'),
             'candle_closed_at':None if snapshot.candle is None else snapshot.candle.close_timestamp.isoformat()}
        try:
            if snapshot.candle is None:raise ValueError('missing closed candle')
            if snapshot.availability!='available':raise ValueError('market source unavailable')
            if not timedelta(0)<=at-snapshot.candle.close_timestamp<=2*timeframe_interval(snapshot.timeframe):
                raise ValueError('closed candle stale or future-dated')
            execution_quote(snapshot,at)
            if packet_schema==2:
                from hermes_trading.venue_constraints import parse_venue_constraints
                venue=parse_venue_constraints(snapshot.venue_rules,asset=asset,at=at)
                row['venue_source_sha256']=venue.source_sha256
            row.update(valid=True,reason=None)
        except (ValueError,TypeError,KeyError,ArithmeticError):row['reason']='unusable_market_source'
        result[asset]=row
    return result


async def run_paired_cycle(root,market_data,*,now=None,process_account=None,entries_blocked=False,not_after=None,packet_preflight=None,operational_stop=None,reservation_id=None):
    from hermes_trading.operational_exits import PAIRED_OPERATIONAL_STOP_REASONS
    if operational_stop is not None and operational_stop not in PAIRED_OPERATIONAL_STOP_REASONS:
        raise ValueError('unknown paired operational stop')
    if reservation_id is not None:
        from hermes_trading.archive_witness import _token
        _token(reservation_id)
    current=lambda:_now(now() if callable(now) else now)
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    selection=resolve_primary(manifest)
    profiles=_profiles(root,manifest);primary=profiles[('adaptive',selection.wallet_eur)][0]
    if 'reporting_protocol' in manifest:
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
        from hermes_trading.paired_inception import read_paired_inception
        reporting_protocol_for(manifest,REPORTING_PROTOCOL)
        opening=read_paired_inception(root)
        if current()<=_time(opening['at']):
            raise ValueError('paired collection must follow authoritative inception')
    # Share the publication helper's exclusive lease, including async collection.
    db=_connect(primary/'paper_publications.sqlite')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS paired_batches (batch_id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,input_hash TEXT NOT NULL,completed TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS paired_identity (id INTEGER PRIMARY KEY,manifest_hash TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS paired_settlements (batch_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS paired_risk_state (id INTEGER PRIMARY KEY,state TEXT NOT NULL)')
        stored_risk=db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()
        if stored_risk is None:
            if db.execute('SELECT 1 FROM paired_batches LIMIT 1').fetchone():raise ValueError('existing paired experiment cannot reset missing risk state')
            risk_state=initial_risk_state(selection);db.execute('INSERT INTO paired_risk_state VALUES (1,?)',(_json(risk_state),))
        else:risk_state=json.loads(stored_risk[0])
        validate_risk_state(risk_state,selection)
        identity=db.execute('SELECT manifest_hash FROM paired_identity WHERE id=1').fetchone()
        if identity is None:db.execute('INSERT INTO paired_identity VALUES (1,?)',(_hash(manifest),))
        elif identity[0]!=_hash(manifest):raise ValueError('paired experiment manifest changed')
        db.commit()
        pending=db.execute("SELECT batch_id,payload,input_hash,completed FROM paired_batches WHERE status='pending'").fetchall()
        if len(pending)>1:raise ValueError('multiple unfinished paired batches')
        if pending:
            batch_id,raw,input_hash,completed=pending[0];packet=load_packet(db,raw);completed=load_packet(db,completed)
            from hermes_trading.archive_quote_state import assert_quote_cohort
            assert_quote_cohort(root,db=db,manifest=manifest,protective=(operational_stop is not None and
                packet.get('operational_stop_reason')==operational_stop and packet.get('operational_entries_blocked') is True))
            if _hash(packet)!=input_hash or packet['release']!=_release_hashes():raise ValueError('pending batch integrity/release differs')
            if packet.get('witness_reservation_id')!=reservation_id:
                raise ValueError('pending witness reservation differs')
        else:
            from hermes_trading.archive_quote_state import assert_quote_cohort
            assert_quote_cohort(root,db=db,manifest=manifest,protective=operational_stop is not None)
            strategies={}; bindings={}
            for (arm,wallet),(path,profile,config) in profiles.items():
                rules=_load_yaml(path/'strategy.yaml')
                if arm=='control' and (not strategy_is_frozen(rules) or hashlib.sha256((path/'strategy.yaml').read_bytes()).hexdigest()!=profile.get('strategy_sha256')):
                    raise ValueError('paired control differs from manifest baseline')
                strategies[f'{arm}_{wallet}']=rules
                bindings[f'{arm}_{wallet}']={'account_hash':_hash(config),'goal_hash':_hash(_encode(_load_yaml(path/'goal.yaml')))}
            adaptive=strategies[selection.profile_key('adaptive')];control=strategies[selection.profile_key('control')]
            if any(strategies[f'control_{w}']!=control for w in (50,100,150,200)):
                raise ValueError('fixed control strategies differ across wallets')
            if str(adaptive.get('version'))=='1' and hashlib.sha256((primary/'strategy.yaml').read_bytes()).hexdigest()!=profiles[('adaptive',selection.wallet_eur)][1].get('strategy_sha256'):
                raise ValueError('initial adaptive strategy differs from manifest baseline')
            snapshots=await market_data.collect(manifest['assets'])
            assert_quote_cohort(root,db=db,manifest=manifest,protective=operational_stop is not None)
            adaptive=_causal_strategy(db,adaptive,(primary/'strategy.yaml').read_bytes(),snapshots,now=current(),baseline_hash=profiles[('adaptive',selection.wallet_eur)][1]['strategy_sha256'],baseline_path=primary/'history/v0001.yaml')
            if set(snapshots)!=set(manifest['assets']):raise ValueError('common market batch is incomplete')
            if any(snapshot.asset!=asset or snapshot.timeframe!=manifest['timeframe'] for asset,snapshot in snapshots.items()):
                raise ValueError('common snapshot asset or timeframe differs from manifest')
            archives=_prepare_archives(primary,profiles)
            created=current();budgets={};costs={}
            if not_after is not None and created>=not_after:raise PairedDeadlineReached('paired collection crossed experiment deadline')
            if packet_preflight is not None:packet_preflight()
            entries_blocked=entries_blocked() if callable(entries_blocked) else entries_blocked
            if type(entries_blocked) is not bool:raise ValueError('entry block must be a literal boolean')
            market_sources=market_source_status(snapshots,created)
            entries_blocked=entries_blocked or operational_stop is not None or any(row['valid'] is not True for row in market_sources.values())
            # Capture the global review stream once. Concurrent imports cannot
            # give the four standalone adaptive scenarios different receipts.
            supervisor_usage=_capture_cost_source(primary/'supervisor_usage_snapshot.json')
            for (arm,wallet),(path,profile,config) in profiles.items():
                name=f'{arm}_{wallet}';cost_path=path/'wallet_costs.json'
                try:
                    refresh_wallet_costs(path,now=created,supervisor_usage=supervisor_usage['data'] if arm=='adaptive' else None)
                    costs[name]=_capture_cost_source(cost_path)
                except (ValueError,OSError,sqlite3.Error) as error:
                    costs[name]=_capture_cost_source(cost_path)
                    costs[name].update(data=None,error='cost accrual refresh failed: '+str(error))
                repo=StateRepository.open(path/'trading.db');repo.migrate()
                try:
                    budgets[name]=value_current_wallet(path,repo,snapshots,{'adaptive':adaptive,'control':control}[arm],now=created,cost_snapshot=costs[name])
                except ValueError as error:
                    budgets[name]={'status':'blocked','error':str(error)}
                finally:repo.close()
            # Candle dataclasses require explicit field encoding.
            encoded={}
            for asset,snapshot in snapshots.items():
                row=dict(vars(snapshot));row['candle']=vars(snapshot.candle) if snapshot.candle is not None else None
                json_value(dict(snapshot.price or {}));encoded[asset]=_encode(row)
            packet={'schema_version':2,'created_at':created.isoformat(),'archives':archives,'budgets':_encode(budgets),'costs':costs,'supervisor_usage':supervisor_usage,'manifest_hash':_hash(manifest),'operational_entries_blocked':entries_blocked,
                    'release':_release_hashes(),'snapshots':encoded,'market_sources':market_sources,'strategies':_encode({'adaptive':adaptive,'control':control}),
                    'bindings':bindings,'assets':manifest['assets']}
            if operational_stop is not None:packet['operational_stop_reason']=operational_stop
            if reservation_id is not None:packet['witness_reservation_id']=reservation_id
            risk_state=update_risk_state(risk_state,{name:budget.get('net_equity') for name,budget in budgets.items()},at=created,primary=selection)
            packet['risk_state_before_execution']=risk_state
            if packet_preflight is not None:packet_preflight()
            db.execute('BEGIN EXCLUSIVE')
            db.execute('UPDATE paired_risk_state SET state=? WHERE id=1',(_json(risk_state),))
            input_hash=_hash(packet);batch_id=str(uuid.uuid4());completed={}
            stored_packet=store_packet(db,packet)
            db.execute('INSERT INTO paired_batches VALUES (?,?,?,?,?)',(batch_id,'pending',stored_packet,input_hash,_json(completed)))
            db.commit() # The original common inputs survive an account-process crash.
        snapshots={asset:_snapshot(value) for asset,value in packet['snapshots'].items()}
        stop_reason=packet.get('operational_stop_reason')
        if stop_reason is not None and (stop_reason not in PAIRED_OPERATIONAL_STOP_REASONS or packet.get('operational_entries_blocked') is not True):
            raise ValueError('pending operational stop admission differs')
        strategies=_decode(packet['strategies']);process_account=process_account or process_paper_account
        for (arm,wallet),(path,profile,config) in profiles.items():
            name=f'{arm}_{wallet}'
            if packet['bindings'][name]!={'account_hash':_hash(config),'goal_hash':_hash(_encode(_load_yaml(path/'goal.yaml')))}:
                raise ValueError('paired account/goal changed during pending batch')
            if name in completed:continue
            strategy=deepcopy(strategies[arm])
            if not (path/'history').exists():(path/'history').mkdir()
            for filename,expected in packet['archives'][name].items():
                archive=path/'history'/filename
                if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
                    raise ValueError('pending batch entry archive changed')
            halted=is_halted(risk_state,arm,wallet) or stop_reason is not None
            if halted:_liquidate_halted_account(path,strategy,snapshots,at=_time(packet['created_at']),batch_id=batch_id,reason=stop_reason or 'paired_drawdown_stop')
            result=await process_account(path,strategy,deepcopy(snapshots),packet['assets'],now=_time(packet['created_at']),valuation=_decode(packet['budgets'][name]),cost_snapshot=packet['costs'][name],entries_blocked=halted or packet.get('operational_entries_blocked',False))
            risk_state=update_risk_state(risk_state,{name:result['net_equity']},at=_time(packet['created_at']),primary=selection)
            db.execute('BEGIN EXCLUSIVE')
            db.execute('UPDATE paired_risk_state SET state=? WHERE id=1',(_json(risk_state),))
            completed[name]=dict(result,arm=arm,wallet_eur=wallet,input_hash=input_hash)
            db.execute('UPDATE paired_batches SET completed=? WHERE batch_id=?',(store_record(db,completed),batch_id));db.commit()
        settlement=db.execute('SELECT payload FROM paired_settlements WHERE batch_id=?',(batch_id,)).fetchone()
        if settlement is None:
            for attempt in range(6):
                before=(risk_state['global_halted'],tuple(risk_state['halted_wallets']))
                for (arm,wallet),(path,profile,config) in profiles.items():
                    if is_halted(risk_state,arm,wallet) or stop_reason is not None:
                        _liquidate_halted_account(path,strategies[arm],snapshots,at=_time(packet['created_at']),batch_id=batch_id,reason=stop_reason or 'paired_drawdown_stop')
                measurements={}
                for (arm,wallet),(path,profile,config) in profiles.items():
                    name=f'{arm}_{wallet}'
                    measurements[name]=_measure_final_account(path,strategies[arm],snapshots,at=_time(packet['created_at']),cost_snapshot=packet['costs'][name],halted=is_halted(risk_state,arm,wallet) or stop_reason is not None,halt_reason=stop_reason)
                next_risk=update_risk_state(risk_state,{name:value['net_equity'] for name,value in measurements.items()},at=_time(packet['created_at']),primary=selection)
                after=(next_risk['global_halted'],tuple(next_risk['halted_wallets']))
                risk_state=next_risk
                if before==after:break
            else:raise ValueError('paired risk decisions failed to settle')
            # No immutable account observation precedes the final halt/sweep decision.
            db.execute('BEGIN EXCLUSIVE')
            db.execute('UPDATE paired_risk_state SET state=? WHERE id=1',(_json(risk_state),))
            db.execute('INSERT INTO paired_settlements VALUES (?,?)',(batch_id,store_record(db,measurements)));db.commit()
        else:measurements=load_packet(db,settlement[0])
        for (arm,wallet),(path,profile,config) in profiles.items():
            name=f'{arm}_{wallet}'
            if completed[name].get('finalized'):continue
            final=_journal_final_account(path,measurements[name],at=_time(packet['created_at']))
            completed[name].update(final,finalized=True)
            if final['measurement_error'] is not None:completed[name]['status']='degraded'
            db.execute('BEGIN EXCLUSIVE')
            db.execute('UPDATE paired_batches SET completed=? WHERE batch_id=?',(store_record(db,completed),batch_id));db.commit()
        db.execute("UPDATE paired_batches SET status='complete' WHERE batch_id=?",(batch_id,));db.commit()
        return {'status':'halted' if risk_state['global_halted'] else 'complete','batch_id':batch_id,
                'input_hash':input_hash,'accounts':list(completed.values()),'risk_state':risk_state}
    except BaseException:
        db.rollback();raise
    finally:db.close()
