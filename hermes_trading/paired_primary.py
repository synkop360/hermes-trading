"""Strict preregistered primary selection; no activation or state migration."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import json,re

from hermes_trading.reflection_supervision import _hash

WALLETS=(50,100,150,200)
FIELDS={'schema_version','purpose','wallet_eur','rationale_sha256'}


@dataclass(frozen=True)
class PrimarySelection:
    wallet_eur:int
    selection_sha256:str|None=None

    def __post_init__(self):
        if type(self.wallet_eur) is not int or self.wallet_eur not in WALLETS:
            raise ValueError('resolved primary capital differs')
        if self.selection_sha256 is None:
            if self.wallet_eur!=100:raise ValueError('legacy primary must remain EUR100')
        elif type(self.selection_sha256) is not str or re.fullmatch('[0-9a-f]{64}',self.selection_sha256) is None:
            raise ValueError('resolved primary selection digest differs')

    @property
    def versioned(self):return self.selection_sha256 is not None

    def profile_key(self,arm):
        if arm not in ('adaptive','control'):raise ValueError('primary arm differs')
        return f'{arm}_{self.wallet_eur}'

    def profile_directory(self,manifest,arm):
        self.profile_key(arm)
        profiles=manifest.get('profiles')
        if type(profiles) is not list:raise ValueError('primary profile inventory differs')
        matches=[row for row in profiles if type(row) is dict and row.get('arm')==arm and
            type(row.get('wallet_eur')) is int and row['wallet_eur']==self.wallet_eur]
        if len(matches)!=1:raise ValueError('primary profile missing or ambiguous')
        name=matches[0].get('directory')
        if type(name) is not str or not name or Path(name).name!=name or name in ('.','..'):
            raise ValueError('primary profile path must be local')
        return name


def _selection(manifest):
    if type(manifest) is not dict:raise ValueError('primary manifest must be an object')
    if 'primary_selection' not in manifest:primary=PrimarySelection(100)
    else:
        value=manifest['primary_selection']
        if (type(value) is not dict or set(value)!=FIELDS or type(value['schema_version']) is not int or
                value['schema_version']!=1 or value['purpose']!='paired_primary_selection_v1' or
                type(value['wallet_eur']) is not int or value['wallet_eur'] not in WALLETS or
                type(value['rationale_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',value['rationale_sha256']) is None):
            raise ValueError('primary selection contract differs')
        primary=PrimarySelection(value['wallet_eur'],_hash(value))
    if 'primary_wallet_eur' in manifest and (type(manifest['primary_wallet_eur']) is not int or
            manifest['primary_wallet_eur']!=primary.wallet_eur):
        raise ValueError('primary manifest label differs from its protocol')
    if primary.versioned and 'steering' in manifest:
        steering=manifest['steering']
        if type(steering) is not dict or ('review_wallet' in steering and steering['review_wallet']!=primary.profile_key('adaptive')):
            raise ValueError('primary review wallet label differs')
    return primary


def resolve_primary(manifest):
    primary=_selection(manifest)
    if primary.versioned or 'reporting_protocol' in manifest:
        protocol=manifest.get('reporting_protocol')
        if (type(protocol) is not dict or type(protocol.get('schema_version')) is not int or
                protocol['schema_version']!=(2 if primary.versioned else 1) or
                type(protocol.get('primary_wallet_eur')) is not int or protocol['primary_wallet_eur']!=primary.wallet_eur or
                (protocol.get('primary_selection_sha256')!=primary.selection_sha256) or
                (not primary.versioned and 'primary_selection_sha256' in protocol)):
            raise ValueError('primary reporting identity differs')
    return primary


def reporting_protocol_for(manifest,legacy_protocol):
    """Derive the method before construction; validate any already stored method."""
    primary=_selection(manifest);result=deepcopy(legacy_protocol)
    if primary.versioned:
        result.update(schema_version=2,primary_wallet_eur=primary.wallet_eur,
                      primary_selection_sha256=primary.selection_sha256)
    if 'reporting_protocol' in manifest and _hash(manifest['reporting_protocol'])!=_hash(result):
        raise ValueError('primary reporting method differs from the frozen protocol')
    return result


def validate_primary_account(account,primary,*,expected_hash=None,experiment_id=None):
    """Bind the existing schema1 account without changing its meaning."""
    from decimal import DecimalException
    from hermes_trading.wallet_metrics import _decimal
    if not isinstance(primary,PrimarySelection):raise ValueError('resolved primary required')
    try:
        valid=(type(account) is dict and type(account.get('schema_version')) is int and account['schema_version']==1 and
            account.get('currency')=='EUR' and _decimal(account.get('starting_balance'))==primary.wallet_eur and
            type(account.get('experiment_id')) is str and bool(account['experiment_id']) and
            (experiment_id is None or account['experiment_id']==experiment_id))
    except (ValueError,TypeError,DecimalException):valid=False
    if not valid:raise ValueError('primary wallet account differs')
    digest=_hash(account)
    if expected_hash is not None and digest!=expected_hash:raise ValueError('primary account hash differs')
    return digest


def read_primary_context(state_dir):
    """Read-only new-protocol path guard; absent/legacy contexts stay legacy."""
    state=Path(state_dir).resolve();path=state.parent/'manifest.json'
    if not path.exists():return None
    manifest=json.loads(path.read_text(encoding='utf-8'));primary=resolve_primary(manifest)
    if not primary.versioned:return None
    from hermes_trading.paired_performance_report import REPORTING_PROTOCOL
    reporting_protocol_for(manifest,REPORTING_PROTOCOL)
    if state!=state.parent/primary.profile_directory(manifest,'adaptive'):
        raise ValueError('state directory is not the selected primary')
    return manifest,primary


def validate_primary_limits(limits,primary,account_hash):
    """Selection pins join the existing immutable lifecycle limits binding."""
    if primary.versioned and (type(limits) is not dict or
            type(limits.get('primary_wallet_eur')) is not int or limits['primary_wallet_eur']!=primary.wallet_eur or
            limits.get('primary_selection_sha256')!=primary.selection_sha256 or limits.get('account_hash')!=account_hash):
        raise ValueError('supervisor limits primary selection/account differs')
