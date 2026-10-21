"""Prepare an isolated disabled primary revision; never migrate active state."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path

from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for
from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
from hermes_trading.reflection_supervision import _hash
from hermes_trading.venue_candidate import _raw,_json,_profiles


def prepare_primary_revision(source_root,target,*,rationale,implementation_approval,enabled=False):
    if enabled is not False:raise ValueError('primary candidate preparation is disabled-only')
    source_root=Path(source_root).resolve();target=Path(target).resolve()
    if target.exists() or target.is_relative_to(source_root):raise ValueError('separate new candidate required')
    if (type(rationale) is not dict or rationale.get('purpose')!='paired_primary_rationale_v1' or
            type(rationale.get('schema_version')) is not int or rationale['schema_version']!=1 or
            type(rationale.get('wallet_eur')) is not int or rationale['wallet_eur']!=200):
        raise ValueError('approved preregistered EUR200 rationale required')
    if (type(implementation_approval) is not dict or implementation_approval.get('user_answer')!='Approve design and disabled implementation' or
            any(implementation_approval.get(key) is not False for key in ('deployment','activation',
                'paid_services_or_model_calls','economic_backtests_or_training','real_money_trading'))):
        raise ValueError('explicit implementation-only approval record required')
    source={}
    def read(name):
        source[name]=_raw(source_root/name);return source[name]
    manifest=json.loads(read('manifest.json'));profiles=_profiles(manifest)
    if (resolve_primary(manifest).versioned or manifest.get('status')!='prepared_draft_not_activated' or
            manifest.get('execution_enabled') is not False or manifest.get('execution_venue_protocol')!='dated_rules_v1' or
            manifest.get('model_access',{}).get('calls_enabled') is not False):
        raise ValueError('original unactivated corrected venue candidate required')
    reporting_protocol_for(manifest,REPORTING_PROTOCOL)
    for name in ('authorization.json','venue-revision.json','venue-receipt.json'):read(name)
    for profile in profiles:
        name=profile['directory']
        for filename in ('strategy.yaml','goal.yaml'):
            raw=read(name+'/'+filename)
            if hashlib.sha256(raw).hexdigest()!=profile[filename.split('.')[0]+'_sha256']:
                raise ValueError('original economic input pin differs')
        account=json.loads(read(name+'/wallet_account.draft.json'))
        if (account.get('schema_version')!=1 or account.get('experiment_id')!='unassigned_until_activation' or
                account.get('activated_at') is not None or account.get('status')!='draft_not_activated' or
                account.get('currency')!='EUR' or account.get('starting_balance')!=str(profile['wallet_eur']) or
                account.get('arm')!=profile['arm']):raise ValueError('unassigned original draft account required')
    output=dict(source);rationale_raw=_json(rationale)
    manifest=deepcopy(manifest);manifest.pop('reporting_protocol')
    manifest.update(created_at=datetime.now(timezone.utc).isoformat(),primary_wallet_eur=200,
        primary_selection=dict(schema_version=1,purpose='paired_primary_selection_v1',wallet_eur=200,
            rationale_sha256=hashlib.sha256(rationale_raw).hexdigest()),primary_revision_ref='primary-revision.json')
    manifest['steering']['review_wallet']='adaptive_200'
    manifest['reporting_protocol']=reporting_protocol_for(manifest,REPORTING_PROTOCOL);primary=resolve_primary(manifest)
    manifest['qualification']['primary_comparison']='EUR200 adaptive standalone full-cost net growth; paired adaptive-minus-control improvement reported separately'
    from scripts.hermes_watch import _supervisor_release
    release=_supervisor_release();project=Path(__file__).resolve().parents[1]
    base=project/'state/paired-supervisor/unassigned_until_activation'
    launch=dict(schema_version=2,status='prepared_draft_not_activated',enabled=False,
        experiment_id='unassigned_until_activation',project_dir=project.as_posix(),
        local_state_dir=(base/primary.profile_key('adaptive')).as_posix(),mirror_dir=(base/'mirror').as_posix(),
        remote_state_dir='/app/state/'+primary.profile_key('adaptive'),policy_hash=None,account_hash=None,
        release_hash=_hash(release),expires_at=None,max_script_seconds=240,
        primary_wallet_eur=primary.wallet_eur,primary_selection_sha256=primary.selection_sha256)
    original_hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in source.items()}
    output.update({'manifest.json':_json(manifest),'primary-rationale.json':rationale_raw,
        'primary-authorization.json':_json(implementation_approval),'supervisor-launch.draft.json':_json(launch),
        'primary-revision.json':_json(dict(schema_version=1,purpose='disabled_primary_revision_v1',
            activated=False,activation_authorized=False,original_source_sha256=original_hashes,
            primary_selection_sha256=primary.selection_sha256,runtime_release=release['runtime'],supervisor_release=release,
            economic_strategy_and_fees_preserved=True,requires_fresh_cost_venue_owner_capacity_and_activation_bindings=True))})
    if len(output)>4096 or sum(map(len,output.values()))>128*1024**2:raise ValueError('candidate exceeds copy budget')
    for name,raw in source.items():
        if _raw(source_root/name)!=raw:raise ValueError('source changed during candidate preparation')
    target.mkdir()
    for name,raw in sorted(output.items(),key=lambda item:item[0]=='manifest.json'):
        path=target/name;path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
    return dict(schema_version=1,status='prepared_draft_not_activated',enabled=False,activated=False,
        files=len(output),bytes=sum(map(len,output.values())),manifest_sha256=hashlib.sha256(output['manifest.json']).hexdigest(),
        primary_selection_sha256=primary.selection_sha256,original_source_sha256=original_hashes)
