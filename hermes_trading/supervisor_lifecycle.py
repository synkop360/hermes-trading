"""Durable finite watcher admission; caller holds the whole watcher-cycle lease."""
from datetime import timedelta
from pathlib import Path
import json,os

from hermes_trading.reflection_supervision import _hash,_json,_now,_time
from hermes_trading.paper_publication import _temporary,_sync_dir


def _save(path,state):
    temporary=_temporary(path,(_json(state)+'\n').encode())
    try:os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    _sync_dir(path.parent)


class SupervisorCycle:
    def __init__(self,path,state,limits,status):
        self.path,self.state,self.limits,self.status=path,state,limits,status

    def check_actions(self,*,now=None):
        current=_now(now)
        state=json.loads(self.path.read_text(encoding='utf-8'))
        if state!=self.state:raise ValueError('supervisor cycle was superseded')
        if self.status not in {'admitted','model_reserved'} or state['status']!='running':return False
        if current<_time(state['last_observed_at']):raise ValueError('supervisor clock regressed')
        state['last_observed_at']=current.isoformat()
        reason=None
        if (self.path.parent/'watcher_maintenance.json').exists():reason='maintenance'
        elif (self.path.parent/'supervisor_operator_stop.json').exists():
            marker=json.loads((self.path.parent/'supervisor_operator_stop.json').read_text(encoding='utf-8'))
            if not isinstance(marker,dict) or marker.get('experiment_id')!=state['experiment_id']:raise ValueError('supervisor stop identity differs')
            reason='operator_stop'
        elif current>=_time(state['deadline']):reason='deadline'
        if reason:
            state.update(status='stopped',stop_reason=reason)
            self.state=state;self.status='stopped';_save(self.path,state);return False
        self.state=state;_save(self.path,state);return True

    def reserve_model(self,*,now=None):
        if self.status!='admitted' or not self.check_actions(now=now):return False
        state=self.state
        if state['model_attempts_reserved']>=self.limits['max_model_attempts']:
            state.update(status='stopped',stop_reason='model_attempt_budget')
            self.status='stopped';_save(self.path,state);return False
        state['model_attempts_reserved']+=1
        self.state=state;self.status='model_reserved'
        _save(self.path,state)
        return True


def admit_cycle(root,policy,release,*,now=None):
    root=Path(root);current=_now(now);path=root/'supervisor_lifecycle.json';limits_path=root/'supervisor_limits.json'
    required=policy.get('supervisor_lifecycle_required') is True
    if not required and not limits_path.exists() and not path.exists():return None
    try:limits=json.loads(limits_path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as error:raise ValueError('explicit supervisor limits required') from error
    if not isinstance(limits,dict) or type(limits.get('schema_version')) is not int or limits['schema_version']!=1:
        raise ValueError('invalid supervisor limits schema')
    if limits.get('experiment_id')!=policy['experiment_id'] or limits.get('policy_hash')!=_hash(policy) or limits.get('release_hash')!=_hash(release):
        raise ValueError('supervisor limits differ from the experiment or release')
    for key,minimum,maximum in (('max_cycles',1,259200),('max_runtime_seconds',1,180*86400),
                               ('interval_seconds',60,240),('max_model_attempts',0,1000)):
        if type(limits.get(key)) is not int or not minimum<=limits[key]<=maximum:
            raise ValueError('finite supervisor bounds required')
    activated=_time(limits.get('activated_at'))
    if activated>current:raise ValueError('supervisor activation is future-dated')
    account_path=root/'wallet_account.json'
    if account_path.exists() or policy.get('wallet_required') is True:
        account=json.loads(account_path.read_text(encoding='utf-8'))
        if not isinstance(account,dict) or account.get('experiment_id')!=policy['experiment_id'] or _time(account.get('activated_at'))!=activated:
            raise ValueError('supervisor and wallet inception differ')
    binding=_hash({'limits':limits,'policy':policy,'release':release})
    deadline=(activated+timedelta(seconds=limits['max_runtime_seconds'])).isoformat()
    if path.exists():
        state=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(state,dict) or state.get('binding')!=binding or state.get('deadline')!=deadline:
            raise ValueError('supervisor lifecycle binding changed')
        for key,maximum in (('cycles_started',limits['max_cycles']),('model_attempts_reserved',limits['max_model_attempts'])):
            if type(state.get(key)) is not int or not 0<=state[key]<=maximum:raise ValueError('invalid supervisor counters')
        if state.get('status') not in {'running','stopped'}:raise ValueError('invalid supervisor status')
        if current<_time(state['last_observed_at']):raise ValueError('supervisor clock regressed')
    else:
        state={'schema_version':1,'experiment_id':policy['experiment_id'],'binding':binding,'deadline':deadline,
               'cycles_started':0,'model_attempts_reserved':0,'last_started_at':None,
               'last_observed_at':current.isoformat(),'status':'running','stop_reason':None}
    state['last_observed_at']=current.isoformat()
    reason=None
    stop=root/'supervisor_operator_stop.json'
    if stop.exists():
        marker=json.loads(stop.read_text(encoding='utf-8'))
        if not isinstance(marker,dict) or marker.get('experiment_id')!=policy['experiment_id']:raise ValueError('supervisor stop identity differs')
        reason='operator_stop'
    elif current>=_time(state['deadline']):reason='deadline'
    elif state['cycles_started']>=limits['max_cycles']:reason='cycle_budget'
    if reason and state['status']=='running':state.update(status='stopped',stop_reason=reason)
    if state['status']=='stopped':status='stopped'
    elif state['last_started_at'] is not None and current<_time(state['last_started_at'])+timedelta(seconds=limits['interval_seconds']):status='not_due'
    else:
        state['cycles_started']+=1;state['last_started_at']=current.isoformat();status='admitted'
    _save(path,state)
    return SupervisorCycle(path,state,limits,status)
