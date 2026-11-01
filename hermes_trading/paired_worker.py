"""Finite shared-input paper worker. No model or exchange order API is invoked."""
import argparse,asyncio,json,os,sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from hermes_trading.adapters.price import PriceClient
from hermes_trading.venue_collection import PublicVenueClient
from hermes_trading.market_data import MarketSnapshot,latest_closed_candle,_closed_candle,_normalized_price,timeframe_interval
from hermes_trading.paired_paper import _profiles,run_paired_cycle,PairedDeadlineReached,PairedStopReached
from hermes_trading.paper_publication import _temporary,_sync_dir,_release_hashes
from hermes_trading.reflection_supervision import _connect,_hash,_json,_now,_time,assert_writer_owner
from hermes_trading.supervisor_sync import capture_startup_release
from hermes_trading.paired_packet_storage import load_packet,MAX_PACKET_BYTES
from hermes_trading.paired_storage_admission import validate_storage_limits,inspect_storage,StorageStopReached


def _write(path,value):
    temporary=_temporary(path,(_json(value)+'\n').encode());os.replace(temporary,path);_sync_dir(path.parent)


def _model_ready(primary,policy,at):
    try:
        access=json.loads((primary/'model_access.json').read_text(encoding='utf-8'))
        if not isinstance(access,dict):raise ValueError('model access must be an object')
        if access.get('schema_version')!=1 or type(access.get('schema_version')) is not int:raise ValueError('model schema')
        if access.get('policy_hash')!=_hash(policy) or (access.get('provider'),access.get('model'))!=(policy['model']['provider'],policy['model']['name']):raise ValueError('model pin')
        reference=access.get('provenance_ref') if access.get('payment')=='verified_free' else access.get('approval_ref')
        if access.get('payment') not in {'verified_free','paid_authorized'} or not isinstance(reference,str) or not reference.strip():raise ValueError('model provenance')
        if _time(access.get('valid_until'))<=at:raise ValueError('model access expired')
        if policy.get('model_access_renewal_required') is True:
            from hermes_trading.model_access_refresh import validate_renewed_access
            validate_renewed_access(policy,access,now=at)
        return True
    except (ValueError,OSError,TypeError,KeyError):return False


def _stats(primary):
    path=primary/'paper_publications.sqlite'
    if not path.exists():return {'complete':0,'pending':False,'latest':None}
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('BEGIN')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_batches'").fetchone() is None:
            return {'complete':0,'pending':False,'latest':None}
        count=db.execute("SELECT COUNT(*) FROM paired_batches WHERE status='complete'").fetchone()[0]
        pending=db.execute("SELECT 1 FROM paired_batches WHERE status='pending' LIMIT 1").fetchone() is not None
        archive_revision=0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_archive_checkpoint'").fetchone() is not None:
            from hermes_trading.archive_journal import read_lifetime
            archived=read_lifetime(db)
            count=archived['complete'];pending=archived['pending'];archive_revision=archived['revision']
        latest=db.execute("SELECT batch_id,input_hash,payload,completed FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
        packet=None if latest is None else load_packet(db,latest[2])
        if packet is not None and _hash(packet)!=latest[1]:raise ValueError('completed packet input hash differs')
        return {'complete':count,'pending':pending,'archive_revision':archive_revision,'latest':None if latest is None else {
            'batch_id':latest[0],'input_hash':latest[1],'packet':packet,'accounts':load_packet(db,latest[3])}}
    finally:db.close()


def _active_storage_positions(profiles,*,allow_missing=False):
    counts={}
    for (arm,wallet),(path,_,_) in profiles.items():
        database=path/'trading.db'
        if not database.exists() and allow_missing:
            counts[f'{arm}_{wallet}']=0;continue
        connection=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)
        try:
            counts[f'{arm}_{wallet}']=connection.execute("SELECT COUNT(*) FROM positions WHERE status!='closed'").fetchone()[0]
        finally:connection.close()
    return counts


def _protective_space(root,storage):
    # This is observed headroom, not a filesystem quota or a worst-case peak
    # proof. The whole-state write/peak certificate remains a launch gate.
    report=inspect_storage(root,storage);margin=2*MAX_PACKET_BYTES
    values=(report.get('state_bytes'),report.get('free_bytes'))
    ready=all(type(value) is int and value>=0 for value in values)
    ready=ready and min(storage['max_state_bytes']-values[0],values[1])>=margin
    return {'status':'ready' if ready else 'blocked','admission_margin_bytes':margin,
            'measurement':report,'hard_write_quota_verified':False}


class PublicQuoteCollector:
    def __init__(self,timeframe,*,timeout_seconds=20):
        self.timeframe=timeframe
        if type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=20:raise ValueError('invalid collection deadline')
        self.timeout_seconds=timeout_seconds
    async def collect(self,assets):
        deadline=asyncio.get_running_loop().time()+self.timeout_seconds
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with PriceClient(exchange_id='kraken',quote_only_on_history_failure=True) as client:prices=await client.fetch_many(assets,self.timeframe)
        except Exception:
            prices={}
        venues={}
        remaining=deadline-asyncio.get_running_loop().time()
        if remaining>0:
            try:
                async with PublicVenueClient(timeout_seconds=remaining) as client:venues=await client.fetch_many(assets)
            except Exception:pass
        at=_now();result={}
        for asset in assets:
            price=prices.get(asset) if isinstance(prices,dict) else None
            if isinstance(price,BaseException) and not isinstance(price,Exception):raise price
            try:
                if not isinstance(price,dict):raise ValueError('missing public price')
                candle=latest_closed_candle(price,self.timeframe,now=at)
                closed=sorted((row for row in price['candles'] if _closed_candle(row,timeframe_interval(self.timeframe)).close_timestamp<=candle.close_timestamp),
                              key=lambda row:_closed_candle(row,timeframe_interval(self.timeframe)).close_timestamp)
                normalized=_normalized_price(dict(price,candles=closed,closes=[row['close'] for row in closed]))
            except (ValueError,TypeError,KeyError,ArithmeticError,AttributeError):
                quote=None
                try:
                    from hermes_trading.wallet_metrics import _decimal
                    received=_time(price['received_at']);bid=_decimal(price['bid']);ask=_decimal(price['ask'])
                    if not timedelta(0)<=at-received<=timedelta(minutes=5) or not 0<bid<=ask:
                        raise ValueError('unusable independent quote')
                    quote={'bid':bid,'ask':ask,'received_at':received.isoformat(),
                           'source':str(price.get('source') or 'received_bid')}
                except (ValueError,TypeError,KeyError,ArithmeticError,AttributeError):pass
                result[asset]=MarketSnapshot(asset,self.timeframe,'unavailable','no_trade','public_price_unavailable',
                    price=quote,venue_rules=venues.get(asset))
                continue
            result[asset]=MarketSnapshot(asset,self.timeframe,'available','evaluate','public_price_only',candle,price=normalized,
                                         venue_rules=venues.get(asset))
        return result


def _write_heartbeat(root,primary,state,release,stats,*,at,model_ready,error=None):
    latest=stats['latest'];accounts={} if latest is None else latest['accounts']
    report=json.loads((primary/'wallet_metrics.json').read_text(encoding='utf-8')) if (primary/'wallet_metrics.json').exists() else None
    version=None if latest is None else latest['packet']['strategies']['adaptive'].get('version')
    primary_wallet=release.get('primary_wallet_eur',100)
    primary_names={f'{arm}_{primary_wallet}' for arm in ('adaptive','control')}
    healthy=primary_names.issubset(accounts) and all(row.get('status')=='observed' and row.get('net_equity') is not None for row in accounts.values())
    healthy=healthy and all(not accounts[name].get('halted') for name in primary_names)
    healthy=healthy and not state.get('storage_stop') and not state.get('local_archive_capture_error') and state.get('storage_report',{}).get('status')=='ready'
    sources={} if latest is None else latest['packet'].get('market_sources',{})
    if latest is not None:
        healthy=healthy and timedelta(0)<=at-_time(latest['packet']['created_at'])<=timedelta(minutes=5)
        healthy=healthy and set(sources)==set(latest['packet']['assets']) and all(row.get('valid') is True and timedelta(0)<=at-_time(row['received_at'])<=timedelta(minutes=5) for row in sources.values())
    status='ok' if healthy and model_ready and error is None else 'degraded'
    if state['status']!='running':status='stopped'
    heartbeat={'timestamp':at.isoformat(),'mode':'paper','execution':'simulated','status':status,'strategy_version':version,
        'worker_release':release,'worker_lifecycle':state,'model_ready':model_ready,
        # Operational liveness and permission to open exposure are separate.
        # In particular, a missing first supervisor health receipt must not
        # prevent that supervisor from reading its first valid worker snapshot.
        'entry_admission':state.get('entry_admission'),
        'batch_id':None if latest is None else latest['batch_id'],'input_hash':None if latest is None else latest['input_hash'],
        'observed_at':None if latest is None else latest['packet']['created_at'],'accounts':accounts,'market_sources':sources,
        'wallet_report_hash':_hash(report) if isinstance(report,dict) and report.get('status')=='observed' else None,
        'wallet_account':None if report is None else {'status':report.get('status'),'experiment_id':report.get('experiment_id')},
        'economic_gate':{'status':'blocked','eligible_for_real_trading':False,'basis':'unqualified_prospective_paper'},
        'hosting_stopped':False,'error':error}
    _write(root/'heartbeat.json',heartbeat);_write(primary/'heartbeat.json',heartbeat)
    if at>=_time(state['activated_at'])+timedelta(days=30) and not (root/'checkpoint_30d.json').exists():
        _write(root/'checkpoint_30d.json',{'timestamp':at.isoformat(),'kind':'operational_learning_checkpoint',
            'due_at':(_time(state['activated_at'])+timedelta(days=30)).isoformat(),
            'coverage_requires_validation':True,
            'profitability_qualified':False,'lifecycle':state,'heartbeat':heartbeat,
            'wallet_reports':{name:json.loads((root/name/'wallet_metrics.json').read_text(encoding='utf-8')) for name in accounts}})
    return heartbeat


def _offer_notification(observer,heartbeat,identity,capture=None):
    """Memory-only handoff, after settlement; notification faults are not trading faults."""
    try:
        settled=(isinstance(capture,dict) and capture.get('status')=='observed'
                 and capture.get('batch_id')==heartbeat.get('batch_id')
                 and capture.get('input_hash')==heartbeat.get('input_hash')
                 and capture.get('manifest_hash')==identity['manifest_hash'])
        envelope=dict(schema_version=1,kind='settled' if settled else 'operational',
            **{key:identity[key] for key in ('experiment_id','opening_at','opening_hash','manifest_hash')},
            at=heartbeat['timestamp'],observed_at=heartbeat.get('observed_at'),
            batch_id=heartbeat.get('batch_id'),input_hash=heartbeat.get('input_hash'),
            completed_batches=heartbeat['worker_lifecycle']['completed_batches'],
            status=heartbeat['status'],entry_admission=heartbeat.get('entry_admission'),
            model_ready=heartbeat.get('model_ready'),strategy_version=heartbeat.get('strategy_version'),
            error=heartbeat.get('error'),accounts=capture['accounts'] if settled else {})
        if capture is not None and not settled:envelope['notification_source_status']='unavailable'
        from copy import deepcopy
        observer(deepcopy(envelope))
    except Exception as error:
        print('paper notification handoff failed: '+type(error).__name__,flush=True)


async def run_worker(root,*,collector_factory=None,clock=None,sleep=asyncio.sleep,storage_probe=None,witness=None,archive_boundary=None,observer=None):
    from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    selection=resolve_primary(manifest)
    profiles=_profiles(root,manifest);primary=profiles[('adaptive',selection.wallet_eur)][0]
    opening=None
    if 'reporting_protocol' in manifest:
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
        from hermes_trading.paired_inception import read_paired_inception
        reporting_protocol_for(manifest,REPORTING_PROTOCOL)
        opening=read_paired_inception(root)
    policy=assert_writer_owner(primary,'external_llm')
    if policy is None:raise ValueError('paired worker requires an explicit external model owner')
    limits=json.loads((root/'worker_limits.json').read_text(encoding='utf-8'))
    if limits.get('schema_version')!=1 or limits.get('experiment_id')!=policy['experiment_id']:raise ValueError('worker lifecycle identity differs')
    for key,maximum in (('max_cycles',259200),('max_runtime_seconds',180*86400),('interval_seconds',240)):
        if type(limits.get(key)) is not int or not 1<=limits[key]<=maximum:raise ValueError('finite worker lifecycle bounds required')
    try:storage=validate_storage_limits(json.loads((root/'storage_limits.json').read_text(encoding='utf-8')),policy['experiment_id'])
    except OSError as error:raise ValueError('explicit storage limits are required before collection') from error
    from hermes_trading.paper_safety import load_policy,entry_status,record_worker_result,append_diagnostic
    safety=load_policy(root.resolve(),policy['experiment_id'])
    if safety is not None and not root.resolve().is_relative_to(Path(safety['volume_root']).resolve()):
        raise ValueError('paper safety must measure the complete active volume')
    backup=limits.get('daily_backup')
    if backup is not None:
        if (safety is None or not isinstance(backup,dict) or
                set(backup)!={'schema_version','destination','max_bytes','timeout_seconds','keep'} or
                type(backup.get('schema_version')) is not int or backup['schema_version']!=1 or
                type(backup.get('max_bytes')) is not int or backup['max_bytes']!=500000000 or
                type(backup.get('timeout_seconds')) is not int or backup['timeout_seconds']!=30 or
                type(backup.get('keep')) is not int or backup['keep']!=2 or
                not isinstance(backup.get('destination'),str) or not Path(backup['destination']).is_absolute()):
            raise ValueError('explicit bounded daily backup policy required')
        destination=Path(backup['destination']).resolve();volume=Path(safety['volume_root']).resolve()
        if destination==volume or destination.is_relative_to(volume) or volume.is_relative_to(destination):
            raise ValueError('daily backup must be outside the active volume')
    from hermes_trading.archive_witness import worker_identity,reconcile_worker
    witness_config=limits.get('archive_witness')
    protective_identity=None
    if isinstance(witness_config,dict) and witness_config.get('schema_version')==2:
        from hermes_trading.archive_protective_session import worker_identity as protective_worker_identity
        protective_identity=protective_worker_identity(root,witness_config,witness);witness_identity=None
    else:witness_identity=worker_identity(root,witness_config,witness)
    from hermes_trading.archive_worker_boundary import configuration as archive_configuration,WorkerCapture
    archive_limits=archive_configuration(limits,archive_boundary)
    current=lambda:_now(clock()) if clock is not None else _now()
    configuration={'manifest':manifest,'limits':limits,'storage':storage,'policy':policy,'accounts':{f'{arm}_{wallet}':config for (arm,wallet),(path,profile,config) in profiles.items()},
                   'opening_receipt_hash':None if opening is None else _hash(opening)}
    if safety is not None:configuration['paper_safety']=safety
    administrative=profiles[('adaptive',selection.wallet_eur)][2]['activated_at']
    activated=administrative if opening is None else opening['at']
    notification_identity=dict(experiment_id=policy['experiment_id'],opening_at=activated,
        opening_hash=None if opening is None else _hash(opening),manifest_hash=_hash(manifest))
    notification_pending={}
    def publish_heartbeat(*args,**kwargs):
        heartbeat=_write_heartbeat(*args,**kwargs)
        if observer is not None:
            _offer_notification(observer,heartbeat,notification_identity,notification_pending.pop('capture',None))
        return heartbeat
    if any(_time(config['activated_at'])!=_time(administrative) for _,_,config in profiles.values()):raise ValueError('paired inception times differ')
    if _time(activated)>current():raise ValueError('paired inception is future-dated')
    try:db=_connect(root/'worker_runtime.sqlite')
    except sqlite3.OperationalError as error:raise RuntimeError('another paired worker holds the lifecycle lease') from error
    protective_session=None
    try:
        db.execute('CREATE TABLE IF NOT EXISTS lifecycle (id INTEGER PRIMARY KEY,state TEXT)')
        existing=db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone();stats=_stats(primary)
        if existing is None:
            if stats['complete'] or stats['pending']:raise ValueError('existing core batches require lifecycle reconciliation')
            if not _model_ready(primary,policy,current()):raise ValueError('verified model access is required before initial worker collection')
            state={'schema_version':1,'experiment_id':policy['experiment_id'],'configuration_hash':_hash(configuration),
                   'activated_at':activated,'deadline':(_time(activated)+timedelta(seconds=limits['max_runtime_seconds'])).isoformat(),
                   'administrative_activated_at':administrative,
                   'limits':limits,'status':'running','completed_batches':0}
            db.execute('INSERT INTO lifecycle VALUES (1,?)',(_json(state),));db.commit()
        else:
            state=json.loads(existing[0]);db.commit()
            if state['configuration_hash']!=_hash(configuration):raise ValueError('worker lifecycle changed; reconciliation required')
        release=capture_startup_release(primary,now=current(),primary=selection);collector=None
        def entry_admission():
            if safety is None:return False
            state['entry_admission']=entry_status(root.resolve(),policy=safety,now=current())
            if state.get('daily_backup_error'):
                state['entry_admission']['reasons'].append('daily_backup_failed')
                state['entry_admission']['blocked']=True
            return state['entry_admission']['blocked']
        def record_result(success,error=None):
            if safety is None:return
            record_worker_result(root.resolve(),success=success,now=current(),
                                 error_kind=None if success else 'cycle_failed')
            append_diagnostic(root.resolve(),policy=safety,now=current(),
                              event='cycle_complete' if success else 'cycle_failed',
                              error_type=None if success else type(error).__name__)
            entry_admission()
        def daily_backup():
            if backup is None:return
            at=current();day=at.date().isoformat()
            if state.get('daily_backup',{}).get('date')==day:return
            from hermes_trading.paper_backup import create_daily_backup
            try:
                result=create_daily_backup(root.resolve(),backup['destination'],enabled=True,
                    owner_db=db,now=at,max_bytes=backup['max_bytes'],
                    timeout_seconds=backup['timeout_seconds'],keep=backup['keep'])
                state['daily_backup']=dict(date=day,path=result['path'],sha256=result['sha256'],
                                          status=result['status'],off_host_verified=False)
                state['daily_backup_error']=None
            except Exception as error:
                # A failed copy blocks new exposure but leaves exits and health
                # reporting alive. No private paths or exception text are logged.
                state['daily_backup_error']=type(error).__name__
                record_worker_result(root.resolve(),success=False,now=current(),error_kind='daily_backup_failed')
                append_diagnostic(root.resolve(),policy=safety,now=current(),event='daily_backup_failed',
                                  error_type=type(error).__name__)
            entry_admission()
        if protective_identity is not None:
            from hermes_trading.archive_protective_session import ProtectiveSession
            protective_session=ProtectiveSession(root,db,witness_config['binding'],protective_identity,witness,enabled=True,now=current)
        def latch_storage_stop(*,publication_guard_held=False):
            lease=None
            if not publication_guard_held:
                lease=sqlite3.connect(primary/'paper_publications.sqlite',timeout=5,isolation_level=None)
                try:lease.execute('BEGIN EXCLUSIVE')
                except BaseException:lease.close();raise
            try:
                marker=primary/'experiment_halt.json'
                if not marker.exists():
                    _write(marker,{'schema_version':1,'experiment_id':state['experiment_id'],'policy_hash':_hash(policy),
                        'reason':'storage_stop','observed_at':current().isoformat(),'storage_report':state.get('storage_report')})
            finally:
                if lease is not None:lease.close()
        if state.get('storage_stop'):latch_storage_stop()
        def check_storage(*,publication_guard_held=False):
            report=(storage_probe or inspect_storage)(root,storage)
            if not isinstance(report,dict) or report.get('status') not in {'ready','blocked'}:raise ValueError('storage probe returned invalid evidence')
            if (primary/'experiment_halt.json').exists():report=dict(report,status='blocked',reason='durable_experiment_halt')
            state['storage_report']=dict(report,observed_at=current().isoformat())
            if report['status']=='blocked':state['storage_stop']=True
            if state.get('storage_stop'):latch_storage_stop(publication_guard_held=publication_guard_held)
            db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
            return not state.get('storage_stop',False)
        def packet_preflight():
            stop=root/'paper_stop.json'
            if stop.exists():
                request=json.loads(stop.read_text(encoding='utf-8'))
                if request.get('experiment_id')!=state['experiment_id']:raise ValueError('paper stop identity differs')
                raise PairedStopReached('operator stopped paper collection')
            if not check_storage(publication_guard_held=True):raise StorageStopReached('storage admission halted the experiment')
        def protective_admission(stats,at):
            previous=state.get('storage_protection',{})
            result={'status':'positions_pending','observed_at':at.isoformat()}
            state['storage_protection']=result
            try:
                halt=json.loads((primary/'experiment_halt.json').read_text(encoding='utf-8'))
                if (not isinstance(halt,dict) or type(halt.get('schema_version')) is not int or halt['schema_version']!=1 or
                    halt.get('reason')!='storage_stop' or halt.get('experiment_id')!=state['experiment_id'] or halt.get('policy_hash')!=_hash(policy)):
                    raise ValueError('storage halt differs')
            except (OSError,ValueError):
                result['reason']='storage_halt_binding_differs';return False
            try:counts=_active_storage_positions(profiles,allow_missing=not stats['complete'] and not stats['pending'])
            except (OSError,sqlite3.Error):
                result['reason']='position_measurement_unavailable';return False
            result['active_positions']=counts
            if not any(counts.values()):result.update(status='flat',reason=None);return False
            if state['status']!='running':result['reason']='already_terminal';return False
            if stop.exists():result['reason']='operator_stop';return False
            if at>=_time(state['deadline']):result['reason']='deadline';return False
            if stats['complete']>=limits['max_cycles']:result['reason']='cycle_bound';return False
            if stats['latest'] is not None and stats['latest']['packet'].get('operational_stop_reason')=='paired_storage_stop':
                result['reason']='single_protective_batch_consumed';return False
            if previous.get('preflight_blocked'):
                result.update(reason='protective_space_unavailable',preflight_blocked=True,
                              space_report=previous.get('space_report'));return False
            space=_protective_space(root,storage);result['space_report']=space
            if space['status']!='ready':result['reason']='protective_space_unavailable';return False
            result.update(status='admitted',reason='paired_storage_stop');return True
        def protective_preflight():
            if stop.exists():raise PairedStopReached('operator stopped protective collection')
            space=_protective_space(root,storage)
            state['storage_protection']['space_report']=space
            if space['status']!='ready':
                state['storage_protection'].update(preflight_blocked=True,reason='protective_space_unavailable')
            db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
            if space['status']!='ready':raise StorageStopReached('protective headroom unavailable')
        def pending_storage_protection(stats):
            # Retention cannot reopen ordinary frozen work. A protective packet
            # already admitted before the fault may finish its original batch.
            if not stats['pending']:return False
            with closing(sqlite3.connect((primary/'paper_publications.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as source:
                rows=source.execute("SELECT payload,input_hash FROM paired_batches WHERE status='pending' LIMIT 2").fetchall()
                if len(rows)!=1:raise ValueError('pending storage protection packet differs')
                packet=load_packet(source,rows[0][0])
            if _hash(packet)!=rows[0][1] or packet.get('release')!=release['hashes']:
                raise ValueError('pending storage protection integrity/release differs')
            if packet.get('operational_stop_reason')!='paired_storage_stop':return False
            admission=state.get('storage_protection',{})
            if (storage.get('protective_stop_on_pressure') is not True or state.get('storage_stop') is not True or
                    admission.get('status')!='admitted' or admission.get('reason')!='paired_storage_stop' or
                    packet.get('operational_entries_blocked') is not True or
                    not _time(state['activated_at'])<=_time(packet['created_at'])<_time(state['deadline']) or
                    stats['complete']>=limits['max_cycles']):
                raise ValueError('pending storage protection original admission differs')
            halt=json.loads((primary/'experiment_halt.json').read_text(encoding='utf-8'))
            if (type(halt) is not dict or type(halt.get('schema_version')) is not int or halt['schema_version']!=1 or
                    halt.get('reason')!='storage_stop' or halt.get('experiment_id')!=state['experiment_id'] or
                    halt.get('policy_hash')!=_hash(policy)):
                raise ValueError('pending storage protection halt binding differs')
            return True
        def archive_preflight():
            if (root/'paper_stop.json').exists():raise PairedStopReached('operator stopped archive protection')
            if current()>=_time(state['deadline']):raise PairedDeadlineReached('archive protection original deadline reached')
            if _release_hashes()!=release['hashes'] or _hash(assert_writer_owner(primary,'external_llm'))!=release['policy_hash']:
                raise ValueError('archive protection owner/release changed')
            if _protective_space(root,storage)['status']!='ready':raise StorageStopReached('archive protection headroom unavailable')
        async def archive_protect(error=None,*,replay=False):
            nonlocal collector,stats
            try:
                stats=_stats(primary)
                if not replay:
                    if (not protective_session.live or not protective_session.outage or stats['pending'] or
                            state['status']!='running' or stats['complete']>=limits['max_cycles']):
                        raise error or ValueError('archive protection lacks original admission')
                    archive_preflight()
                    if not any(_active_storage_positions(profiles,allow_missing=not stats['complete']).values()):
                        raise error or ValueError('archive protection has no retained positions')
                    token=protective_session.claim_protection()
                else:token=protective_session.replay_protection()
                state['status']='waiting_for_archive'
                state['stop_reason']='archive_receiver_unavailable'
                state['archive_protection']={'status':'replaying' if replay else 'claimed','acknowledgement':'pending',
                    'reservation_id':token,'observed_at':current().isoformat()}
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                if collector is None:collector=(collector_factory or (lambda:PublicQuoteCollector(manifest['timeframe'])))()
                cycle_result=await run_paired_cycle(root,collector,now=clock,entries_blocked=True,not_after=_time(state['deadline']),
                    packet_preflight=archive_preflight,operational_stop='paired_archive_stop',reservation_id=token,
                    **({'notification_capture':True} if observer is not None else {}))
                if observer is not None:notification_pending['capture']=cycle_result.get('notification')
                stats=_stats(primary);state['completed_batches']=stats['complete']
                counts=_active_storage_positions(profiles)
                state['archive_protection'].update(status='positions_pending' if any(counts.values()) else 'flat',active_positions=counts)
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                try:protective_session.reconcile()
                except (OSError,TimeoutError):
                    if not protective_session.outage:raise
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='archive_acknowledgement_pending')
                    return False
                state['status']='running';state.pop('stop_reason',None)
                state['archive_protection']['acknowledgement']='acknowledged'
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                return True
            except Exception as failure:
                publish_heartbeat(root,primary,state,release,_stats(primary),at=current(),model_ready=False,
                    error='archive_protection_failed:'+type(failure).__name__)
                raise
        publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='worker_starting')
        while True:
            stats=_stats(primary)
            if protective_session is not None:
                try:protective_head=protective_session.reconcile()
                except (OSError,TimeoutError) as error:
                    if protective_session.live and protective_session.outage:
                        if await archive_protect(error):continue
                        return state
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='witness_failed:'+type(error).__name__)
                    raise
                except Exception as error:
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='witness_failed:'+type(error).__name__)
                    raise
                if protective_head['state']['pending_protection'] is not None:
                    if await archive_protect(replay=True):continue
                    return state
                if state['status']=='waiting_for_archive':
                    state['status']='running';state.pop('stop_reason',None)
                    state['archive_protection']['acknowledgement']='acknowledged'
                    db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
            if witness_identity is not None:
                try:reconcile_worker(root,witness_config,witness,witness_identity)
                except Exception as error:
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,
                                     error='witness_failed:'+type(error).__name__)
                    raise
            if stats['complete']<state['completed_batches'] or stats.get('archive_revision',0)<state.get('archive_revision',0):
                raise ValueError('paper worker lifetime/archive checkpoint regressed')
            state['completed_batches']=stats['complete']
            if stats.get('archive_revision',0):state['archive_revision']=stats['archive_revision']
            from hermes_trading.archive_quote_state import assert_quote_cohort
            retention_pending=assert_quote_cohort(root,manifest=manifest,protective=True)
            if not retention_pending and archive_limits is not None and stats['complete'] and not stats['pending']:
                if _release_hashes()!=release['hashes']:
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='disk_release_changed')
                    raise ValueError('archive boundary release changed')
                try:owner_hash=_hash(assert_writer_owner(primary,'external_llm'))
                except Exception:
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='owner_changed')
                    raise
                if owner_hash!=release['policy_hash']:
                    publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=False,error='owner_changed')
                    raise ValueError('archive boundary owner changed')
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                boundary=WorkerCapture(root,db,stats,manifest,release,policy,archive_limits)
                try:
                    await asyncio.wait_for(archive_boundary(boundary),timeout=boundary.remaining())
                    boundary.check()
                    if boundary.completed_exports:state['local_archive_capture_error']=None
                except Exception as error:
                    state['local_archive_capture_error']=type(error).__name__
                finally:boundary.close()
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
            if stats['latest'] is not None and stats['latest']['packet'].get('operational_stop_reason')=='paired_storage_stop':
                # The shared journal is independent of the lifecycle/marker
                # projection. A partial rollback cannot reopen this experiment.
                state['storage_stop']=True;latch_storage_stop()
            at=current()
            stop=root/'paper_stop.json';reason=None
            if state['status']!='running':reason=state.get('stop_reason','already_terminal')
            elif state.get('storage_stop') and safety is None:reason='storage_stop'
            elif stop.exists():
                request=json.loads(stop.read_text(encoding='utf-8'))
                if request.get('experiment_id')!=state['experiment_id']:raise ValueError('paper stop identity differs')
                reason='operator_stop'
            elif at>=_time(state['deadline']):reason='deadline'
            elif stats['complete']>=limits['max_cycles']:reason='cycle_bound'
            elif state.get('storage_stop'):reason='storage_stop'
            elif protective_session is not None and not stats['pending'] and stats['complete']+1>=limits['max_cycles']:
                reason='protective_capacity_reserved'
            if reason is None and not check_storage():reason='storage_stop'
            protective=False
            if (reason=='storage_stop' and not stats['pending'] and storage.get('protective_stop_on_pressure') is True):
                protective=protective_admission(stats,at)
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
            if retention_pending and stats['pending']:
                protective=pending_storage_protection(stats)
            if reason is not None and not stats['pending'] and not protective:
                if reason=='storage_stop' and safety is not None and state['status']=='running' and at<_time(state['deadline']):
                    daily_backup()
                    entry_admission()
                    state['entry_admission']['blocked']=True
                    if 'storage_stop' not in state['entry_admission']['reasons']:
                        state['entry_admission']['reasons'].append('storage_stop')
                    db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                    publish_heartbeat(root,primary,state,release,stats,at=at,model_ready=False,error='storage_entry_halt')
                    await sleep(limits['interval_seconds'])
                    continue
                if reason=='storage_stop' and safety is not None and at>=_time(state['deadline']):reason='deadline'
                state.update(status='completed' if reason in {'deadline','cycle_bound','protective_capacity_reserved'} else 'stopped',stop_reason=reason)
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                publish_heartbeat(root,primary,state,release,stats,at=at,model_ready=False)
                return state
            if retention_pending and not protective:
                waiting=dict(state,status='waiting_for_retention')
                publish_heartbeat(root,primary,waiting,release,stats,at=at,model_ready=False,error='quote_retention_pending')
                return waiting
            ready=_model_ready(primary,policy,at)
            if _release_hashes()!=release['hashes']:
                publish_heartbeat(root,primary,state,release,stats,at=at,model_ready=False,error='disk_release_changed')
                raise ValueError('loaded paper release changed on disk')
            if _hash(assert_writer_owner(primary,'external_llm'))!=release['policy_hash']:
                publish_heartbeat(root,primary,state,release,stats,at=at,model_ready=False,error='owner_changed')
                raise ValueError('paper worker owner changed')
            try:
                reservation_id=None if witness_identity is None else reconcile_worker(
                    root,witness_config,witness,witness_identity,reserve=True)
                if protective_session is not None:
                    try:reserved=protective_session.reserve()
                    except (OSError,TimeoutError) as error:
                        if protective_session.live and protective_session.outage:
                            if await archive_protect(error):continue
                            return state
                        raise
                    reservation_id=reserved['state']['reservation']['reservation_id']
                if collector is None:collector=(collector_factory or (lambda:PublicQuoteCollector(manifest['timeframe'])))()
                cycle_result=await run_paired_cycle(root,collector,now=clock,
                                      entries_blocked=True if protective else lambda:entry_admission() or bool(state.get('local_archive_capture_error')) or not _model_ready(primary,policy,current()),
                                      not_after=_time(state['deadline']),
                                      packet_preflight=protective_preflight if protective else packet_preflight,
                                      operational_stop='paired_storage_stop' if protective else None,
                                      reservation_id=reservation_id,
                                      **({'notification_capture':True} if observer is not None else {}))
                if observer is not None:notification_pending['capture']=cycle_result.get('notification')
                if witness_identity is not None:
                    reconcile_worker(root,witness_config,witness,witness_identity)
                if protective_session is not None:
                    try:protective_session.reconcile()
                    except (OSError,TimeoutError) as error:
                        if protective_session.live and protective_session.outage:
                            if await archive_protect(error):continue
                            return state
                        raise
                stats=_stats(primary);state['completed_batches']=stats['complete']
                record_result(True)
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                daily_backup()
                db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                publish_heartbeat(root,primary,state,release,stats,at=current(),model_ready=_model_ready(primary,policy,current()))
            except (PairedDeadlineReached,PairedStopReached,StorageStopReached):
                if witness_identity is not None or protective_session is not None:
                    # A reserved but unfrozen attempt cannot be silently reused
                    # after a crash. Do not infer cancellation from local files.
                    raise ValueError('unfrozen witness reservation requires explicit recovery')
                continue
            except Exception as error:
                if safety is not None:
                    record_result(False,error)
                    db.execute('UPDATE lifecycle SET state=? WHERE id=1',(_json(state),));db.commit()
                    publish_heartbeat(root,primary,state,release,_stats(primary),at=current(),model_ready=False,error='cycle_failed:'+type(error).__name__)
                    await sleep(limits['interval_seconds'])
                    continue
                publish_heartbeat(root,primary,state,release,_stats(primary),at=current(),model_ready=False,error='cycle_failed:'+type(error).__name__)
                raise
            if not state.get('storage_stop'):await sleep(limits['interval_seconds'])
    finally:
        if protective_session is not None:protective_session.close()
        db.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args()
    print(_json(asyncio.run(run_worker(Path(args.root)))))

if __name__=='__main__':main()
