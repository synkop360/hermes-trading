"""Resolve archived paper execution rules for existing positions."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import re
import yaml
from hermes_trading.positions import SimulationSettings
from hermes_trading.strategy import _decimal_setting

class EntryContractError(ValueError):
    pass

class _DecimalLoader(yaml.SafeLoader):
    pass
_DecimalLoader.add_constructor('tag:yaml.org,2002:float',lambda loader,node:Decimal(loader.construct_scalar(node)))

class EntryContractResolver:
    def __init__(self,state_dir,current_strategy):
        self.state_dir=Path(state_dir)
        self.current=deepcopy(current_strategy)
    def __call__(self,asset,version):
        version=str(version)
        if not re.fullmatch(r'[0-9]{1,12}',version) or int(version)<1:
            raise EntryContractError('invalid entry strategy version')
        try:
            if str(self.current.get('version'))==version:
                full=deepcopy(self.current)
            else:
                path=self.state_dir/'history'/f'v{int(version):04d}.yaml'
                full=yaml.load(path.read_text(encoding='utf-8'),Loader=_DecimalLoader)
            if not isinstance(full,dict) or str(full.get('version'))!=version:
                raise EntryContractError('entry archive version differs')
            from hermes_trading.loop import resolve_asset_strategy
            effective=resolve_asset_strategy(full,asset)
            if str(effective.get('version'))!=version:
                raise EntryContractError('asset override changes entry version')
            for key in ('entry','exit','simulation'):
                if key in effective and not isinstance(effective[key],dict):
                    raise EntryContractError('entry contract rule must be an object: '+key)
            settings=SimulationSettings.from_strategy(effective)
            for value in vars(settings).values():
                if isinstance(value,Decimal) and not value.is_finite():
                    raise EntryContractError('entry contract numbers must be finite')
            rule=effective.get('exit',{})
            for key in ('take_profit_pct','take_profit_atr','max_bars','stop_atr','trailing_atr','trailing_activation_atr'):
                if key in rule and _decimal_setting(rule[key],key)<=0:
                    raise EntryContractError('entry exit limits must be positive')
            if 'partial_close_pct' in rule and not Decimal('0')<_decimal_setting(rule['partial_close_pct'],'partial_close_pct')<1:
                raise EntryContractError('partial close fraction must be within zero and one')
            if 'partial_close_pct' in rule and ('trailing_atr' not in rule or bool(rule.get('trailing_overrides_explicit_exit',False))):
                raise EntryContractError('partial closes require trailing and cannot suppress the first explicit exit')
            if 'threshold' in rule:
                _decimal_setting(rule['threshold'],'exit threshold')
            if 'trailing_overrides_explicit_exit' in rule and type(rule['trailing_overrides_explicit_exit']) is not bool:
                raise EntryContractError('trailing override must be boolean')
            return effective
        except EntryContractError:
            raise
        except (OSError,ValueError,TypeError,ArithmeticError,yaml.YAMLError) as error:
            raise EntryContractError('entry archive is missing or invalid') from error
