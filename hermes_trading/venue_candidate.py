"""Prepare a new disabled revision; never activate accounts or change originals."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import hashlib,json
from pathlib import Path
import yaml

from hermes_trading.reflection_supervision import _hash,_time
from hermes_trading.venue_constraints import parse_venue_constraints


_ASSETS={'BTC/EUR','ETH/EUR','XRP/EUR'}
_MINIMA=('minimum_order_quantity','minimum_order_quote','quantity_decimals')


def _raw(path,limit=1024*1024):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:
        raise ValueError('bounded regular candidate file required')
    raw=path.read_bytes()
    if len(raw)>limit:raise ValueError('candidate file exceeds bound')
    return raw


def _json(value):return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def _profiles(manifest):
    from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for
    primary=resolve_primary(manifest)
    if primary.versioned:
        from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
        reporting_protocol_for(manifest,REPORTING_PROTOCOL)
    profiles=manifest.get('profiles')
    if not isinstance(profiles,list) or len(profiles)!=8 or set(manifest.get('assets',[]))!=_ASSETS:
        raise ValueError('exactly eight original EUR profiles and three assets required')
    seen=set()
    for profile in profiles:
        arm,wallet=profile.get('arm'),profile.get('wallet_eur')
        if arm not in ('adaptive','control') or type(wallet) is not int or wallet not in (50,100,150,200):
            raise ValueError('unsupported candidate profile')
        name=f'{arm}_{wallet}'
        if profile.get('directory')!=name or name in seen:raise ValueError('foreign or duplicate profile directory')
        seen.add(name)
    return profiles


def _facts(receipt, *, at=None):
    if (not isinstance(receipt,dict) or set(receipt)!={'schema_version','records'} or
        type(receipt['schema_version']) is not int or receipt['schema_version']!=1 or
        not isinstance(receipt['records'],dict) or set(receipt['records'])!=_ASSETS or len(_json(receipt))>32768):
        raise ValueError('invalid bounded three-asset venue receipt')
    values={};origins=set()
    for asset,record in receipt['records'].items():
        values[asset]=parse_venue_constraints(record,asset=asset,at=at or _time(record.get('received_at')))
        origins.add((values[asset].received_at,record['source_url']))
    if len(origins)!=1:raise ValueError('venue receipt must be one common public collection')
    return values


def prepare_draft_revision(source_root,target,*,venue_receipt,enabled=False):
    if enabled is not False:raise ValueError('candidate builder is disabled-only')
    source_root=Path(source_root).resolve();target=Path(target).resolve()
    if target.exists() or target.is_relative_to(source_root):raise ValueError('new separate candidate target required')
    source={}
    def read(relative):
        source[relative]=_raw(source_root/relative)
        return source[relative]
    manifest=json.loads(read('manifest.json'));authorization=read('authorization.json')
    profiles=_profiles(manifest)
    if manifest.get('status')!='prepared_draft_not_activated' or manifest.get('model_access',{}).get('calls_enabled') is not False:
        raise ValueError('unactivated source with disabled inference required')
    facts=_facts(venue_receipt);output={};changes=[]
    for profile in profiles:
        name=profile['directory'];raw=read(name+'/strategy.yaml')
        if hashlib.sha256(raw).hexdigest()!=profile.get('strategy_sha256'):raise ValueError('original strategy pin differs')
        original=yaml.safe_load(raw);revised=deepcopy(original)
        if str(original.get('version'))!='1' or (profile['arm']=='control' and original.get('research',{}).get('frozen') is not True):
            raise ValueError('original version and frozen control required')
        for asset,venue in facts.items():
            simulation=revised['symbols'][asset]['simulation']
            values=(str(venue.minimum_quantity),str(venue.minimum_quote),venue.quantity_decimals)
            for field,value in zip(_MINIMA,values):
                if simulation.get(field)!=value:
                    changes.append({'profile':name,'asset':asset,'field':field,'before':simulation.get(field),'after':value})
                simulation[field]=value
        # Explicitly prove that only the declared venue fields differ.
        stripped=deepcopy(revised)
        for asset in _ASSETS:
            for field in _MINIMA:
                if field in original['symbols'][asset]['simulation']:
                    stripped['symbols'][asset]['simulation'][field]=original['symbols'][asset]['simulation'][field]
                else:stripped['symbols'][asset]['simulation'].pop(field,None)
        if stripped!=original:raise ValueError('economic strategy drift')
        output[name+'/strategy.yaml']=yaml.safe_dump(revised,sort_keys=True).encode()
        for filename in ('goal.yaml','wallet_account.draft.json'):
            output[name+'/'+filename]=read(name+'/'+filename)
        account=json.loads(output[name+'/wallet_account.draft.json'])
        if (account.get('currency')!='EUR' or Decimal(account.get('starting_balance','0'))!=profile['wallet_eur'] or
            account.get('experiment_id')!='unassigned_until_activation' or account.get('activated_at') is not None or
            account.get('status')!='draft_not_activated' or account.get('arm')!=profile['arm']):
            raise ValueError('draft account is active or differs from original scenario')
        profile['strategy_sha256']=hashlib.sha256(output[name+'/strategy.yaml']).hexdigest()
    original_hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in source.items()}
    receipt=deepcopy(venue_receipt)
    manifest.update(created_at=datetime.now(timezone.utc).isoformat(),execution_venue_protocol='dated_rules_v1',
                    execution_enabled=False,venue_revision={'source_manifest_sha256':original_hashes['manifest.json'],
                        'receipt_sha256':_hash(receipt),'activation_requires_fresh_metadata':True,'activation_authorized':False})
    output.update({'manifest.json':_json(manifest),'authorization.json':authorization,'venue-receipt.json':_json(receipt),
        'venue-revision.json':_json({'schema_version':1,'purpose':'disabled_venue_revision_v1','activated':False,
            'changes':changes,'original_source_sha256':original_hashes,'economic_strategy_and_fees_preserved':True,
            'implementation_approval_ref':'research/runs/current-venue-execution-v1/authorization.json',
            'approval_ref_scope':'local implementation only; external project evidence, no activation permission',
            'requires_fresh_cost_release_owner_capacity_and_activation_bindings':True})})
    if len(output)>4096 or sum(map(len,output.values()))>128*1024*1024:raise ValueError('candidate exceeds copy budget')
    for name,raw in source.items():
        if _raw(source_root/name)!=raw:raise ValueError('original changed during candidate preparation')
    target.mkdir()
    # Manifest is published last. A failed write leaves an unactivated partial
    # target for inspection; this function never recursively deletes evidence.
    for name,raw in sorted(output.items(),key=lambda item:item[0]=='manifest.json'):
        path=target/name;path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
    return {'schema_version':1,'status':'prepared_draft_not_activated','target':str(target),
            'changed_fields':len(changes),'files':len(output),'bytes':sum(map(len,output.values())),
            'enabled':False,'activated':False,'source_sha256':original_hashes,'manifest_sha256':hashlib.sha256(output['manifest.json']).hexdigest()}


def _activation_context(root,*,retained=False):
    root=Path(root);manifest=json.loads(_raw(root/'manifest.json'));profiles=_profiles(manifest)
    if manifest.get('status')!='paper_ready' or manifest.get('execution_venue_protocol')!='dated_rules_v1':
        raise ValueError('explicit corrected paper candidate required')
    bindings={};accounts=[]
    for profile in profiles:
        path=root/profile['directory'];strategy=_raw(path/'strategy.yaml')
        if retained and profile['arm']=='adaptive' and hashlib.sha256(strategy).hexdigest()!=profile.get('strategy_sha256'):
            version=yaml.safe_load(strategy).get('version')
            if not isinstance(version,(str,int)) or isinstance(version,bool) or not str(version).isdigit() or not 1<int(version)<=999999:
                raise ValueError('activation baseline requires a later adaptive version')
            # Activation proves immutable inception facts, not the current epoch.
            # The paired causal/publication chain separately authorizes that epoch.
            strategy=_raw(path/'history/v0001.yaml')
            if str(yaml.safe_load(strategy).get('version'))!='1':raise ValueError('activation baseline version differs')
        if hashlib.sha256(strategy).hexdigest()!=profile.get('strategy_sha256'):raise ValueError('activation strategy pin differs')
        account=json.loads(_raw(path/'wallet_account.json'));accounts.append(account)
        if (account.get('currency')!='EUR' or Decimal(account.get('starting_balance','0'))!=profile['wallet_eur'] or
            not account.get('experiment_id') or account['experiment_id']=='unassigned_until_activation'):
            raise ValueError('assigned activation account required')
        bindings[profile['directory']]={'strategy_sha256':hashlib.sha256(strategy).hexdigest(),
            'account_hash':_hash(account),'goal_sha256':hashlib.sha256(_raw(path/'goal.yaml')).hexdigest()}
    activations={_time(row['activated_at']) for row in accounts};experiments={row['experiment_id'] for row in accounts}
    if len(activations)!=1 or len(experiments)!=1:raise ValueError('one common activation identity and time required')
    return root,manifest,profiles,bindings,next(iter(activations)),next(iter(experiments))


def _activation_payload(root,venue_receipt,*,at,retained=False):
    current=_time(at) if isinstance(at,str) else at
    facts=_facts(venue_receipt,at=current)
    root,manifest,profiles,bindings,activation,experiment=_activation_context(root,retained=retained)
    if not timedelta(0)<=current-activation<=timedelta(minutes=2):raise ValueError('fresh activation time required')
    for profile in profiles:
        path=root/profile['directory']/'strategy.yaml'
        if retained and profile['arm']=='adaptive' and hashlib.sha256(_raw(path)).hexdigest()!=profile['strategy_sha256']:
            path=path.parent/'history/v0001.yaml'
        strategy=yaml.safe_load(_raw(path))
        for asset,venue in facts.items():
            simulation=strategy['symbols'][asset]['simulation']
            if (Decimal(str(simulation.get('minimum_order_quantity')))!=venue.minimum_quantity or
                Decimal(str(simulation.get('minimum_order_quote')))!=venue.minimum_quote or
                type(simulation.get('quantity_decimals')) is not int or simulation['quantity_decimals']!=venue.quantity_decimals):
                raise ValueError('fresh venue facts differ from candidate; reconcile before activation')
    return {'schema_version':1,'purpose':'paired_venue_activation_v1','observed_at':current.isoformat(),
        'activated_at':activation.isoformat(),'experiment_id':experiment,'manifest_hash':_hash(manifest),
        'bindings':bindings,'venue_receipt':deepcopy(venue_receipt)}


def build_activation_receipt(root,venue_receipt,*,at):
    return _activation_payload(root,venue_receipt,at=at)


def validate_activation_venue(root,*,at=None):
    receipt=json.loads(_raw(Path(root)/'venue_activation.json',32768))
    if (set(receipt)!={'schema_version','purpose','observed_at','activated_at','experiment_id','manifest_hash','bindings','venue_receipt'} or
        type(receipt['schema_version']) is not int or receipt['schema_version']!=1 or receipt['purpose']!='paired_venue_activation_v1'):
        raise ValueError('invalid activation venue receipt')
    original=_time(receipt['observed_at'])
    expected=_activation_payload(root,receipt['venue_receipt'],at=original,retained=True)
    if receipt!=expected:raise ValueError('activation venue receipt binding differs')
    if at is not None:
        current=_time(at) if isinstance(at,str) else at
        _facts(receipt['venue_receipt'],at=current)
        if current<original:raise ValueError('future activation venue receipt')
    return receipt
