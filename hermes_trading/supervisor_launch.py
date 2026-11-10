"""Read-only binding for the isolated adaptive-primary watcher invocation."""
from datetime import timedelta
from pathlib import Path
import json,re

from hermes_trading.reflection_supervision import _hash,_now,_time,load_owner_policy
from hermes_trading.paired_primary import PrimarySelection,validate_primary_account,validate_primary_limits


def launch_primary(config):
    version=config.get('schema_version')
    if type(version) is not int or version not in (1,2):raise ValueError('unsupported primary launch schema')
    if version==2:
        primary=PrimarySelection(config.get('primary_wallet_eur'),config.get('primary_selection_sha256'))
        if not primary.versioned:raise ValueError('versioned primary launch selection required')
        return primary
    if 'primary_selection_sha256' in config or ('primary_wallet_eur' in config and
            (type(config['primary_wallet_eur']) is not int or config['primary_wallet_eur']!=100)):
        raise ValueError('legacy launch primary differs')
    return PrimarySelection(100)


def validate_launch(path,project,release,*,allow_expired=False):
    path=Path(path)
    if path.stat().st_size>65536:raise ValueError('supervisor launch configuration exceeds bound')
    config=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config,dict) or config.get('status')!='activated' or config.get('enabled') is not True:
        raise ValueError('supervisor launch is not activated')
    primary=launch_primary(config)
    experiment=config.get('experiment_id')
    if not isinstance(experiment,str) or re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}',experiment) is None:
        raise ValueError('invalid launch experiment identity')
    project=Path(project).resolve()
    def absolute(name):
        value=config.get(name)
        if not isinstance(value,str) or not Path(value).is_absolute():raise ValueError('absolute launch path required')
        return Path(value).resolve()
    if absolute('project_dir')!=project:raise ValueError('launch project differs')
    base=project/'state/paired-supervisor'/experiment
    state=absolute('local_state_dir');mirror=absolute('mirror_dir')
    if state!=base/primary.profile_key('adaptive') or mirror!=base/'mirror' or config.get('remote_state_dir')!='/app/state/'+primary.profile_key('adaptive'):
        raise ValueError('launch must target isolated adaptive primary paths')
    policy=load_owner_policy(state)
    if policy is None or policy['owner']!='external_llm' or policy['experiment_id']!=experiment or any(policy.get(flag) is not True for flag in ('wallet_required','model_access_renewal_required','supervisor_lifecycle_required')):
        raise ValueError('launch requires the controlled adaptive supervisor')
    account=json.loads((state/'wallet_account.json').read_text(encoding='utf-8'))
    validate_primary_account(account,primary,experiment_id=experiment,expected_hash=config.get('account_hash'))
    if config.get('policy_hash')!=_hash(policy) or config.get('account_hash')!=_hash(account) or config.get('release_hash')!=_hash(release):
        raise ValueError('launch owner, wallet or release differs')
    limits=json.loads((state/'supervisor_limits.json').read_text(encoding='utf-8'))
    if not isinstance(limits,dict) or limits.get('experiment_id')!=experiment or limits.get('policy_hash')!=_hash(policy) or limits.get('release_hash')!=_hash(release):
        raise ValueError('launch lifecycle differs')
    validate_primary_limits(limits,primary,_hash(account))
    seconds=limits.get('max_runtime_seconds')
    if type(seconds) is not int or not 1<=seconds<=180*86400:raise ValueError('launch needs finite lifecycle duration')
    activated=_time(account.get('activated_at'))
    expires=activated+timedelta(seconds=seconds)
    if type(allow_expired) is not bool:raise ValueError('literal lifetime validation option required')
    now=_now()
    if (_time(limits.get('activated_at'))!=activated or _time(config.get('expires_at'))!=expires or
            now<activated or (now>=expires and not allow_expired)):
        raise ValueError('launch lifetime differs or expired')
    return dict(config,local_state_dir=state,mirror_dir=mirror)
