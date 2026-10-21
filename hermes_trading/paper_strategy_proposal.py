"""Exact decimal proposal values shared by paper review and publication.

JSON model responses use numbers; YAML engine settings use integers or decimal
text. Keep conversion at this boundary without relaxing the engine's accounting
validation or changing legacy reflection behavior.
"""
from decimal import Decimal, InvalidOperation

import yaml

from hermes_trading.reflect import (
    _get_nested, _normalize_strategy_proposal, _resolve_asset_strategy,
    _set_nested, _variable_path, STRATEGY_VARIABLE_BOUNDS,
    STRATEGY_VARIABLE_MAX_STEP,
)


def proposal_decimal(value):
    if type(value) not in {int, float, str}:
        raise ValueError('paper proposal value must be numeric')
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('paper proposal value must be numeric') from error
    if not number.is_finite():
        raise ValueError('paper proposal value must be finite')
    return number


def apply_paper_strategy_proposal(strategy, proposal, *, assets=None):
    variable, asset = _normalize_strategy_proposal(strategy, proposal)
    if assets is None:
        assets = list(strategy.get('symbols', {})) or ['']
    elif (not isinstance(assets, (list, tuple)) or not assets
          or any(not isinstance(item, str) or not item.strip() for item in assets)):
        raise ValueError('active assets must be a nonempty list of symbols')
    assets = [item.strip().upper() for item in assets]
    if asset is not None and asset not in assets:
        raise ValueError('proposal must target an active asset')
    number = proposal_decimal(proposal.get('value'))
    low, high = (Decimal(str(bound)) for bound in STRATEGY_VARIABLE_BOUNDS[variable])
    if not low <= number <= high:
        raise ValueError(f'{variable} must stay between {low} and {high}')
    if variable == 'exit.max_bars' and number != number.to_integral_value():
        raise ValueError('exit.max_bars must be a whole bar count')
    path = _variable_path(variable)
    previous = _get_nested(_resolve_asset_strategy(strategy, asset or ''), path)
    if previous is not None:
        old = proposal_decimal(previous)
        if abs(number - old) > Decimal(str(STRATEGY_VARIABLE_MAX_STEP[variable])):
            raise ValueError(f'{variable} change exceeds the maximum step')
        if old == number:
            raise ValueError('proposal does not change the effective variable')
    updated = yaml.safe_load(yaml.safe_dump(strategy))
    target = updated if asset is None else updated['symbols'][asset]
    value = int(number) if number == number.to_integral_value() else format(number.normalize(), 'f')
    _set_nested(target, path, value)
    effective_assets = [asset] if asset is not None else assets
    for symbol in effective_assets:
        before = _get_nested(_resolve_asset_strategy(strategy, symbol), path)
        after = _get_nested(_resolve_asset_strategy(updated, symbol), path)
        if before is None and after is None:
            continue
        if before is None or after is None or proposal_decimal(before) != proposal_decimal(after):
            return updated, variable, asset
    raise ValueError('proposal does not change the effective variable on any active asset')
