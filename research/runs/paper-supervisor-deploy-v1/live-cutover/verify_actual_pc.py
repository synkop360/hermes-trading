"""Operator-run read-only PC validation after both-host checked transitions.

Never calls a watcher, launcher execution, provider, refresh, transport or DB
initializer. Its sole write, when the owner runs it, is a new research receipt.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

LIVE = Path(__file__).resolve().parent
PROJECT = LIVE.parents[4] / 'hermes-trading'
AUDIT = Path('C:/Users/thoma/AppData/Local/hermes/cutovers/paper-recovery-ea752c5/pc-audit')
SOURCE = 'ea752c5f3990acff2a6f7164be733fe2bf92df35'
EXPECTED_RELEASE = 'd8289ff44d543bebca1df87a566335f05366607ffc767d05eb00b75c6eddcf04'
EXPECTED_TRANSITION = json.loads((LIVE/'frozen-live-transition-plan.json').read_text())['transition_id']
OUTPUT = LIVE / 'pc-native-launch-validated.json'


def read(path, maximum=1024*1024):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('bounded_validation_input_exceeded')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('validation_object_required')
    return value


def validate():
    sys.path.insert(0, str(PROJECT))
    import scripts.hermes_watch as watcher
    import scripts.paper_release_transition as transition
    from hermes_trading.reflection_supervision import _hash
    from hermes_trading.supervisor_launch import validate_launch

    if Path(watcher.__file__).resolve() != (PROJECT/'scripts/hermes_watch.py').resolve() or Path(transition.__file__).resolve() != (PROJECT/'scripts/paper_release_transition.py').resolve():
        raise ValueError('installed_native_module_origin_required')
    plan = read(LIVE/'frozen-live-transition-plan.json')
    original = read(LIVE/'pc-original-reference.json')
    prepared = read(LIVE/'pc-intent-prepared.json')
    pins = read(Path('E:\\_OB_VAULTS\\Main Vault\\Projects\\Automatic Trading\\hermes-recovery-ea752c5.raw-sha256.json'))
    if plan['transition_id'] != EXPECTED_TRANSITION or plan['release_plan']['new_commit'] != SOURCE:
        raise ValueError('exact_frozen_transition_required')
    if hashlib.sha256((PROJECT/'scripts/paper_release_transition.py').read_bytes()).hexdigest() != pins['scripts/paper_release_transition.py']:
        raise ValueError('installed_operator_helper_bytes_differ')
    release = watcher._supervisor_release()  # Only hashes existing source bytes.
    if release != plan['release_plan']['new'] or len(release['runtime']) != 129 or _hash(release) != EXPECTED_RELEASE:
        raise ValueError('installed_runtime_release_bytes_differ')
    if any(release[key] != plan['release_plan']['old'][key] for key in ('watcher', 'native_launcher')):
        raise ValueError('watcher_or_native_launcher_changed')
    state = Path(original['actual_supervisor'])
    launch = Path(original['actual_launch'])
    options = dict(worker_root=PROJECT.parent/'r27-ea752c5/w', supervisor_root=state, launch_file=launch, audit_dir=AUDIT)
    layout = transition._layout(**options)
    intent = transition._intent(plan, layout)  # Reads and verifies this host's existing immutable intent.
    if intent['intent_hash'] != prepared['intent_hash'] or intent['root_binding'] != prepared['root_binding']:
        raise ValueError('pc_intent_differs_from_prepared_reference')
    inspection = transition.inspect_transition(plan, **options)
    if len(inspection['components']) != 6 or any(c['state'] != 'after' for c in inspection['components']):
        raise ValueError('all_six_guards_must_be_after')
    completed = read(AUDIT/'receipt.json')
    if completed.get('status') != 'completed' or completed.get('transition_id') != plan['transition_id'] or completed.get('root_binding') != intent['root_binding']:
        raise ValueError('completed_pc_transition_receipt_required')
    components = {c['name']: c for c in plan['components']}
    limits, lifecycle = read(state/'supervisor_limits.json'), read(state/'supervisor_lifecycle.json')
    config, account, owner = read(launch, 65536), read(state/'wallet_account.json'), read(state/'reflection_owner.json')
    for name, actual in (('pc_limits', limits), ('pc_lifecycle', lifecycle), ('pc_launch', config)):
        if actual != json.loads(components[name]['after']):
            raise ValueError('actual_pc_guard_differs_from_frozen_after')
    before_lifecycle = json.loads(components['pc_lifecycle']['before'])
    if {k:v for k,v in lifecycle.items() if k != 'binding'} != {k:v for k,v in before_lifecycle.items() if k != 'binding'}:
        raise ValueError('original_lifecycle_state_changed')
    if _hash(account) != plan['identity']['account_hashes']['adaptive_200'] or _hash(owner) != plan['identity']['policy_hash']:
        raise ValueError('original_pc_account_or_owner_changed')
    if account.get('activated_at') != '2026-10-23T20:13:29.470165+00:00' or limits.get('activated_at') != account['activated_at']:
        raise ValueError('original_pc_cost_clock_changed')
    if config.get('wrapper_sha256') != release['native_launcher']:
        raise ValueError('native_wrapper_binding_differs')
    checked = validate_launch(launch, PROJECT, release)  # Native pure/read-only launch validation.
    if checked['local_state_dir'].resolve() != state.resolve() or checked.get('primary_wallet_eur') != 200:
        raise ValueError('actual_primary_launch_target_differs')
    return dict(status='native_launch_validated_on_actual_pc', transition_id=plan['transition_id'],
        root_binding=intent['root_binding'], intent_hash=intent['intent_hash'], release_hash=_hash(release),
        source_candidate=SOURCE, runtime_files=len(release['runtime']), all_129_runtime_bytes_match=True,
        watcher_and_native_launcher_unchanged=True, native_wrapper_hash_matches=True,
        original_account_hash=_hash(account), original_owner_hash=_hash(owner),
        original_account_cost_t0=account['activated_at'], deadline=lifecycle['deadline'],
        cycles_started=lifecycle['cycles_started'], model_attempts_reserved=lifecycle['model_attempts_reserved'],
        original_lifecycle_nonbinding_fields_unchanged=True, all_pc_guards_match_frozen_after=True,
        six_component_states=inspection['components'], primary_wallet_eur=200,
        native_validation='hermes_trading.supervisor_launch.validate_launch',
        qualification='actual_pc_only_peer_worker_is_retained_reference_no_watcher_or_readiness_action')


if __name__ == '__main__':
    try:
        value = validate()
    except Exception as error:
        value = dict(status='pc_native_validation_failed', error_type=type(error).__name__, source_candidate=SOURCE)
    value['observed_at'] = datetime.now(timezone.utc).isoformat()
    with OUTPUT.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(0 if value['status'] == 'native_launch_validated_on_actual_pc' else 1)
