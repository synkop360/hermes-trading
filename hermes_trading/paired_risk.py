"""Monotone net-equity peaks and irreversible paired paper halt decisions."""
from copy import deepcopy
from decimal import Decimal
from hermes_trading.reflection_supervision import _now
from hermes_trading.paired_primary import PrimarySelection,resolve_primary

WALLETS=(50,100,150,200)

def initial_risk_state(primary=None):
    primary=_primary(primary)
    result={'schema_version':2 if primary.versioned else 1,'threshold':'0.03','primary_wallet_eur':primary.wallet_eur,'global_halted':False,
            'halted_wallets':[],'breaches':[],
            'accounts':{f'{arm}_{wallet}':{'peak':str(wallet),'net_equity':None,'drawdown':None}
                        for arm in ('adaptive','control') for wallet in WALLETS}}
    if primary.versioned:result['primary_selection_sha256']=primary.selection_sha256
    return result


def _primary(value):
    if value is None:return resolve_primary({})
    if not isinstance(value,PrimarySelection):raise ValueError('risk requires the resolved primary selection')
    return value


def validate_risk_state(state,primary=None):
    primary=_primary(primary)
    if (type(state) is not dict or type(state.get('schema_version')) is not int or
            state['schema_version']!=(2 if primary.versioned else 1) or state.get('threshold')!='0.03' or
            type(state.get('primary_wallet_eur')) is not int or state['primary_wallet_eur']!=primary.wallet_eur or
            state.get('primary_selection_sha256')!=primary.selection_sha256 or
            (not primary.versioned and 'primary_selection_sha256' in state)):
        raise ValueError('paired risk policy or primary selection changed')
    if (type(state.get('global_halted')) is not bool or type(state.get('accounts')) is not dict or
            set(state['accounts'])!={f'{arm}_{wallet}' for arm in ('adaptive','control') for wallet in WALLETS} or
            type(state.get('halted_wallets')) is not list or
            any(type(wallet) is not int or wallet not in WALLETS for wallet in state['halted_wallets'])):
        raise ValueError('paired risk state differs')
    return primary

def is_halted(state,arm,wallet):
    if f'{arm}_{wallet}' not in state['accounts']:raise ValueError('unknown risk account')
    return state['global_halted'] or wallet in state['halted_wallets']

def update_risk_state(state,equities,*,at=None,primary=None):
    primary=validate_risk_state(state,primary)
    result=deepcopy(state);stamp=_now(at).isoformat()
    for name,value in sorted(equities.items()):
        if name not in result['accounts']:raise ValueError('unknown risk account')
        row=result['accounts'][name];peak=Decimal(row['peak'])
        if not peak.is_finite() or peak<=0:raise ValueError('invalid risk peak')
        if value is None:
            row.update(net_equity=None,drawdown=None);continue
        if isinstance(value,(bool,float)):raise ValueError('risk equity must be decimal')
        equity=Decimal(value)
        if not equity.is_finite():raise ValueError('risk equity must be finite')
        peak=max(peak,equity);drawdown=max(Decimal('0'),(peak-equity)/peak)
        row.update(peak=str(peak),net_equity=str(equity),drawdown=str(drawdown))
        arm,wallet=name.rsplit('_',1);wallet=int(wallet)
        if drawdown>=Decimal(result['threshold']) and wallet not in result['halted_wallets']:
            result['halted_wallets']=sorted(result['halted_wallets']+[wallet])
            result['breaches'].append({'account':name,'at':stamp,'net_equity':str(equity),'peak':str(peak),'drawdown':str(drawdown)})
            if wallet==primary.wallet_eur:result['global_halted']=True
    return result
