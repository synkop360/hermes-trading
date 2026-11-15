"""Read-only preparation check; never authorizes or activates paper trading.

Checks the disabled paper-beta-v1 contract, not a running state directory.
No trading engine, credentials, model client or network is loaded.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

import yaml

WALLETS = (50, 100, 150, 200)
DRAFT_ID = 'unassigned_until_activation'
MAX_FILE_BYTES = 1024 * 1024
# Approved candidate declarations, not a current/private exchange fee claim.
FEES = {'maker_fee_rate': '0.004', 'taker_fee_rate': '0.008', 'slippage_rate': '0.001'}
RISK = {'risk_per_trade_pct': '0.0025', 'max_order_quote': '5',
        'max_symbol_exposure_pct': '0.05', 'max_total_exposure_pct': '0.10'}
REMAINING = ['current_release_verification', 'fresh_model_access',
             'fresh_operating_cost_provenance', 'fresh_venue_metadata',
             'deployment_and_activation_approval', 'actual_inception_and_runtime_checks']


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value):
    _require(type(value) in (str, int, float, Decimal), 'numeric declaration required')
    result = Decimal(str(value))
    _require(result.is_finite(), 'finite numeric declaration required')
    return result


class _Reader:
    def __init__(self, root):
        self.root = Path(root).absolute()

    def raw(self, name):
        path = self.root / name
        for part in (path, *path.parents):
            _require(not part.is_symlink(), 'candidate inputs must not follow symlinks')
        _require(path.is_file(), f'{name}: regular candidate file required')
        with path.open('rb') as source:
            data = source.read(MAX_FILE_BYTES + 1)
        _require(len(data) <= MAX_FILE_BYTES, f'{name}: candidate file exceeds bound')
        return data

    def json(self, name):
        value = json.loads(self.raw(name))
        _require(isinstance(value, dict), f'{name}: JSON object required')
        return value


def _manifest(reader):
    data = reader.json('manifest.json')
    _require(data.get('status') == 'prepared_draft_not_activated' and
             data.get('execution_enabled') is False, 'disabled manifest required')
    model = data.get('model_access')
    _require(isinstance(model, dict) and model.get('calls_enabled') is False and
             model.get('paid_calls_authorized') is False, 'model calls must remain disabled')
    storage = data.get('storage_decision')
    _require(isinstance(storage, dict) and storage.get('archive_extensions_enabled') is False,
             'archive extensions must remain disabled')
    _require(sorted(data.get('assets', [])) == ['BTC/EUR', 'ETH/EUR', 'XRP/EUR'],
             'approved EUR assets required')
    profiles = data.get('profiles')
    _require(isinstance(profiles, list) and len(profiles) == 8, 'eight distinct profiles required')
    identities = set()
    for profile in profiles:
        _require(isinstance(profile, dict), 'profile object required')
        arm, wallet = profile.get('arm'), profile.get('wallet_eur')
        _require(arm in ('adaptive', 'control') and type(wallet) is int and wallet in WALLETS,
                 'eight distinct approved profiles required')
        _require(profile.get('directory') == f'{arm}_{wallet}', 'canonical profile directory required')
        identities.add((arm, wallet))
    _require(len(identities) == 8, 'eight distinct profiles required')
    selection = data.get('primary_selection')
    _require(data.get('primary_wallet_eur') == 200 and isinstance(selection, dict) and
             selection.get('wallet_eur') == 200, 'preregistered EUR200 primary required')
    rationale = reader.raw('primary-rationale.json')
    _require(hashlib.sha256(rationale).hexdigest() == selection.get('rationale_sha256'),
             'primary rationale hash differs')
    rationale_data = json.loads(rationale)
    _require(isinstance(rationale_data, dict) and type(rationale_data.get('schema_version')) is int and
             rationale_data.get('schema_version') == 1 and
             rationale_data.get('purpose') == 'paired_primary_rationale_v1' and
             type(rationale_data.get('wallet_eur')) is int and rationale_data.get('wallet_eur') == 200,
             'preregistered EUR200 rationale required')
    return data


def _fees(reader):
    data = reader.json('fee-assumptions.json')
    _require(data.get('entry_execution') == data.get('exit_execution') == 'taker',
             'declared taker entry and exit required')
    _require(all(_number(data.get(key)) == Decimal(value) for key, value in FEES.items()),
             'approved declared fees differ')


def _execution_settings(simulation, entry_fee, name):
    _require(entry_fee == 'taker' and
             all(_number(simulation.get(key)) == Decimal(value) for key, value in FEES.items()),
             f'{name}: strategy differs from declared fees')
    _require(simulation.get('cost_stress_reserve') is True and
             all(_number(simulation.get(key)) == Decimal(value) for key, value in RISK.items()),
             f'{name}: approved risk caps differ')


def _profile(reader, profile):
    name, arm, wallet = profile['directory'], profile['arm'], profile['wallet_eur']
    raw = reader.raw(f'{name}/strategy.yaml')
    _require(hashlib.sha256(raw).hexdigest() == profile.get('strategy_sha256'),
             f'{name}: strategy hash differs')
    _require(hashlib.sha256(reader.raw(f'{name}/goal.yaml')).hexdigest() == profile.get('goal_sha256'),
             f'{name}: goal hash differs')
    strategy = yaml.safe_load(raw)
    _require(isinstance(strategy, dict), f'{name}: strategy object required')
    simulation, entry = strategy.get('simulation'), strategy.get('entry')
    _require(isinstance(simulation, dict) and isinstance(entry, dict), 'strategy settings required')
    _execution_settings(simulation, entry.get('fee'), name)
    symbols = strategy.get('symbols')
    _require(isinstance(symbols, dict) and set(symbols) == {'BTC/EUR', 'ETH/EUR', 'XRP/EUR'},
             f'{name}: approved symbol settings required')
    for asset, overrides in symbols.items():
        _require(isinstance(overrides, dict), f'{name}: symbol settings must be objects')
        symbol_simulation, symbol_entry = overrides.get('simulation', {}), overrides.get('entry', {})
        _require(isinstance(symbol_simulation, dict) and isinstance(symbol_entry, dict),
                 f'{name}: symbol execution settings must be objects')
        effective_simulation = dict(simulation, **symbol_simulation)
        _execution_settings(effective_simulation, symbol_entry.get('fee', entry.get('fee')), f'{name}/{asset}')
    research = strategy.get('research', {})
    _require(isinstance(research, dict) and
             ((research.get('frozen') is True) if arm == 'control' else not research.get('frozen')),
             f'{name}: fixed control/adaptive metadata differs')
    account = reader.json(f'{name}/wallet_account.draft.json')
    _require(account.get('experiment_id') == DRAFT_ID and account.get('activated_at') is None and
             account.get('status') == 'draft_not_activated' and account.get('currency') == 'EUR' and
             account.get('starting_balance') == str(wallet) and account.get('arm') == arm,
             f'{name}: unassigned draft account required')
    costs = reader.json(f'{name}/wallet_cost_sources.draft.json')
    _require(costs.get('experiment_id') == DRAFT_ID and costs.get('scope_start') is None and
             costs.get('currency') == 'EUR', f'{name}: unassigned draft cost scope required')
    categories = costs.get('categories')
    _require(isinstance(categories, dict) and set(categories) == {'hosting', 'ai', 'tools', 'funding'},
             f'{name}: all explicit cost categories required')
    for cost in categories.values():
        _require(isinstance(cost, dict) and isinstance(cost.get('source'), str) and cost['source'].strip(),
                 f'{name}: explicit cost provenance required')
    _require(categories['ai'].get('mode') == ('supervised' if arm == 'adaptive' else 'recurring'),
             f'{name}: adaptive/control cost allocation differs')
    economic = deepcopy(strategy)
    economic.pop('research', None)
    return economic


def _operations(reader):
    safety = reader.json('paper_safety.draft.json')
    _require(safety.get('experiment_id') == DRAFT_ID and safety.get('capacity_bytes') == 500000000 and
             _number(safety.get('alert_fraction')) == Decimal('0.70') and
             safety.get('supervisor_health_required') is True and
             safety.get('supervisor_stale_seconds') == 300 and
             safety.get('max_worker_errors') == safety.get('max_supervisor_errors') == 3,
             'approved safety policy required')
    limits = reader.json('worker_limits.draft.json')
    backup = limits.get('daily_backup')
    _require(limits.get('experiment_id') == DRAFT_ID and isinstance(backup, dict) and
             backup.get('destination') == '/tmp/hermes-paper-daily-backups' and backup.get('keep') == 2 and
             backup.get('max_bytes') == 500000000 and backup.get('timeout_seconds') == 30,
             'approved daily backup required')
    launch = reader.json('supervisor-launch.draft.json')
    _require(launch.get('enabled') is False and launch.get('experiment_id') == DRAFT_ID and
             launch.get('paper_safety_enabled') is True and launch.get('primary_wallet_eur') == 200,
             'disabled EUR200 supervisor launch required')
    owner = reader.json('adaptive_200/reflection_owner.draft.json')
    _require(owner.get('owner') == 'external_llm' and owner.get('experiment_id') == DRAFT_ID and
             owner.get('cadence_closed_trades') == 5 and owner.get('one_variable_only') is True,
             'one supervised writer after five closes required')


def check_candidate(root):
    """Validate a disabled bundle; missing activation evidence remains unverified."""
    reader, errors, checks, accounts = _Reader(root), [], [], []

    def check(name, action):
        try:
            result = action()
        except (ValueError, TypeError, KeyError, OSError, InvalidOperation, yaml.YAMLError) as error:
            errors.append({'check': name, 'message': str(error)})
            checks.append({'name': name, 'passed': False})
            return None
        checks.append({'name': name, 'passed': True})
        return result

    manifest = check('manifest', lambda: _manifest(reader))
    check('fee_assumptions', lambda: _fees(reader))
    check('operations', lambda: _operations(reader))
    strategies = []
    if manifest is not None:
        for profile in manifest['profiles']:
            strategy = check(profile['directory'], lambda p=profile: _profile(reader, p))
            if strategy is not None:
                strategies.append(strategy)
                accounts.append(profile['directory'])
        if len(strategies) == 8:
            check('common_baseline', lambda: _require(all(s == strategies[0] for s in strategies),
                                                     'all eight economic baselines must match'))
    valid = not errors
    return {'schema_version': 1, 'status': 'valid_disabled_candidate' if valid else 'invalid_candidate',
            'candidate_valid': valid, 'activation_ready': False, 'activation_assessed': False,
            'primary_wallet_eur': manifest['primary_wallet_eur'] if manifest is not None else None,
            'accounts': sorted(accounts), 'checks': checks, 'errors': errors,
            'remaining_activation_checks': list(REMAINING)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'config' / 'paper-beta-v1')
    args = parser.parse_args(argv)
    report = check_candidate(args.root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['candidate_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
