"""Audited, restart-safe increase of the original paper state budget to 1 GB.

Run only before the paper worker starts. This transition preserves every
position, batch, safety latch, and original experiment clock.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from hermes_trading.archive_snapshot import _safe_root, ownership
from hermes_trading.paired_inception import read_paired_inception
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.paired_storage_admission import inspect_storage, validate_storage_limits
from hermes_trading.paired_worker import _profiles
from hermes_trading.paper_publication import _release_hashes, _sync_dir, _temporary
from hermes_trading.paper_safety import load_policy
from hermes_trading.reflection_supervision import _hash, _json, _now, assert_writer_owner

EXPERIMENT = 'paper-beta-v1-20261021-6c9d520'
APPROVAL = 'user-2026-11-17-storage-limit-1gb'
OLD_MAX = 500000000
NEW_MAX = 1000000000
RESERVE = 134217728
EXPECTED_RUNTIME = '33f6c6329f1bc5383d12d5cafce8e13255eb68a02a2bf2bba9ec60ccb370114f'
AUDIT = '.paper-storage-limit-transition-2026-11-17'


def _read(path):
    value = json.loads(_safe_root(path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError('storage transition object required')
    return value


def _save(path, body):
    path = _safe_root(path)
    temporary = _temporary(path, (_json(body) + '\n').encode())
    try:
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _configuration(root, storage, experiment_id):
    manifest = _read(root / 'manifest.json')
    primary = resolve_primary(manifest)
    profiles = _profiles(root, manifest)
    selected = profiles[('adaptive', primary.wallet_eur)][0]
    policy = assert_writer_owner(selected, 'external_llm')
    if policy is None or policy['experiment_id'] != experiment_id:
        raise ValueError('storage transition owner differs')
    limits = _read(root / 'worker_limits.json')
    if limits['experiment_id'] != experiment_id:
        raise ValueError('storage transition worker differs')
    opening = read_paired_inception(root) if 'reporting_protocol' in manifest else None
    configuration = {'manifest': manifest, 'limits': limits, 'storage': storage,
                     'policy': policy,
                     'accounts': {f'{arm}_{wallet}': config
                                  for (arm, wallet), (_, _, config) in profiles.items()},
                     'opening_receipt_hash': None if opening is None else _hash(opening)}
    safety = load_policy(root, experiment_id)
    if safety is not None:
        configuration['paper_safety'] = safety
    return _hash(configuration), selected, policy


def transition_limit(root, *, approval_ref, fault=None):
    """Change the bound budget while retaining the active storage halt.

    A durable intent lets the same call finish after interruption at either
    the file replacement or the lifecycle compare-and-set.
    """
    return _transition_limit(root, approval_ref=approval_ref, experiment_id=EXPERIMENT,
                             reserve_bytes=RESERVE, old_max=OLD_MAX, new_max=NEW_MAX,
                             protective_required=True, expected_runtime=EXPECTED_RUNTIME,
                             fault=fault)


def _transition_limit(root, *, approval_ref, experiment_id, reserve_bytes, old_max,
                      new_max, protective_required, expected_runtime=None, fault=None):
    if os.environ.get('HERMES_TRADING_MODE') != 'paper' or os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS') != 'false':
        raise ValueError('paper-only storage transition required')
    if approval_ref != APPROVAL:
        raise ValueError('exact storage transition approval required')
    if expected_runtime is not None and _hash(_release_hashes()) != expected_runtime:
        raise ValueError('storage transition source differs')
    root = _safe_root(Path(root).absolute())
    path = _safe_root(root / 'storage_limits.json')
    audit = _safe_root(root / AUDIT)
    intent_path = _safe_root(audit / 'intent.json')
    receipt_path = _safe_root(audit / 'receipt.json')
    with ownership(root, role='worker', enabled=True) as db:
        raw = db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()
        if raw is None:
            raise ValueError('original worker lifecycle required')
        state = json.loads(raw[0])
        current = _read(path)
        if (current.get('experiment_id') != experiment_id or current.get('reserve_bytes') != reserve_bytes or
                (protective_required and current.get('protective_stop_on_pressure') is not True)):
            raise ValueError('original storage policy differs')
        if current.get('max_state_bytes') not in (old_max, new_max):
            raise ValueError('unexpected storage ceiling')
        old = dict(current, max_state_bytes=old_max)
        new = dict(old, max_state_bytes=new_max)
        validate_storage_limits(old, experiment_id)
        validate_storage_limits(new, experiment_id)
        old_hash, primary, policy = _configuration(root, old, experiment_id)
        new_hash, _, _ = _configuration(root, new, experiment_id)
        if state.get('status') != 'running' or state.get('storage_stop') is not True:
            if not receipt_path.exists():
                raise ValueError('active original storage pause required')
        if intent_path.exists():
            intent = _read(intent_path)
            if (intent.get('intent_hash') != _hash({k: v for k, v in intent.items() if k != 'intent_hash'}) or
                    intent.get('root') != str(root) or intent.get('experiment_id') != experiment_id or
                    intent.get('approval_ref') != APPROVAL or
                    intent.get('old_hash') != old_hash or intent.get('new_hash') != new_hash or
                    intent.get('old_limits') != old or intent.get('new_limits') != new):
                raise ValueError('storage transition intent differs')
        else:
            if current != old or state.get('configuration_hash') != old_hash or receipt_path.exists():
                raise ValueError('unowned storage transition state')
            report = inspect_storage(root, new)
            if report.get('status') != 'ready' or report['free_bytes'] < reserve_bytes + 2*1024*1024:
                raise ValueError('verified storage headroom required')
            halt = _read(primary / 'experiment_halt.json')
            if halt.get('reason') != 'storage_stop' or halt.get('experiment_id') != experiment_id or halt.get('policy_hash') != _hash(policy):
                raise ValueError('bound storage halt required')
            if audit.exists() and any(audit.iterdir()):
                raise ValueError('unowned storage transition audit')
            body = {'schema_version': 1, 'root': str(root), 'experiment_id': experiment_id, 'approval_ref': APPROVAL,
                    'old_hash': old_hash, 'new_hash': new_hash, 'old_limits': old,
                    'new_limits': new, 'halt_sha256': hashlib.sha256((primary / 'experiment_halt.json').read_bytes()).hexdigest(),
                    'observed_at': _now().isoformat()}
            audit.mkdir(exist_ok=True)
            intent = dict(body, intent_hash=_hash(body))
            _save(intent_path, intent)
            if fault:
                fault('intent')
        if state.get('storage_stop') is True:
            marker = _safe_root(primary / 'experiment_halt.json')
            if not marker.is_file() or hashlib.sha256(marker.read_bytes()).hexdigest() != intent['halt_sha256']:
                raise ValueError('storage transition halt changed')
        if current == old:
            _save(path, new)
            if fault:
                fault('storage_limits')
        if state.get('configuration_hash') == old_hash:
            updated = deepcopy(state)
            updated['configuration_hash'] = new_hash
            db.execute('BEGIN EXCLUSIVE')
            try:
                changed = db.execute('UPDATE lifecycle SET state=? WHERE id=1 AND state=?', (_json(updated), raw[0]))
                if changed.rowcount != 1:
                    raise ValueError('storage transition lifecycle changed')
                db.commit()
            except BaseException:
                db.rollback()
                raise
            if fault:
                fault('lifecycle')
        elif state.get('configuration_hash') != new_hash:
            raise ValueError('storage transition lifecycle differs')
        if receipt_path.exists():
            receipt = _read(receipt_path)
            if receipt != {'schema_version': 1, 'status': 'transitioned', 'intent_hash': intent['intent_hash']}:
                raise ValueError('storage transition receipt differs')
            return {'kind': 'paper_storage_limit_transition', 'status': 'already_transitioned'}
        _save(receipt_path, {'schema_version': 1, 'status': 'transitioned', 'intent_hash': intent['intent_hash']})
        return {'kind': 'paper_storage_limit_transition', 'status': 'transitioned'}
