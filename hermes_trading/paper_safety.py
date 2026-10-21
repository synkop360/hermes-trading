"""Optional paper entry admission; never changes finance, strategy or exit rules.

The explicit ``root/paper_safety.json`` binds one experiment to a whole-volume
500 MB budget, a 70% entry alert and three-error latches. Health is durable in
``paper_safety_state.json``. Supervisor retries must repeat the latest event ID,
event time and semantic result exactly; retries do not refresh its freshness.
Only ``paper_runtime.log`` and its three named generations may be rotated.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from hermes_trading.persistence.artifacts import _artifact_lock, _fsync_directory


MAX_VOLUME_ENTRIES = 100000
VOLUME_TIMEOUT_SECONDS = 5
_JSON_LIMIT = 16384
_FIXED = {
    'schema_version': 1, 'capacity_bytes': 500000000, 'alert_fraction': '0.70',
    'max_worker_errors': 3, 'max_supervisor_errors': 3,
    'supervisor_stale_seconds': 300, 'diagnostics_max_bytes': 1048576,
    'diagnostics_generations': 3,
}
_REQUIRED = set(_FIXED) | {'experiment_id', 'volume_root'}


def _utc_now():
    return datetime.now(timezone.utc)


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('aware safety observation time required')
    return value.astimezone(timezone.utc)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate safety JSON key')
        result[key] = value
    return result


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _directory(path):
    # Check ancestors before scandir/open: resolving first would follow links.
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute safety root required')
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('safety directory must be an unlinked directory')
    return path


def _regular(path, *, missing=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    if _linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('safety artifact must be an unlinked regular file')
    return info


def _read(path):
    info = _regular(path)
    if info.st_size > _JSON_LIMIT:
        raise ValueError('safety JSON exceeds bound')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_nlink) != (info.st_dev, info.st_ino, 1):
            raise ValueError('safety artifact changed while opening')
        raw = stream.read(_JSON_LIMIT + 1)
    if len(raw) > _JSON_LIMIT:
        raise ValueError('safety JSON exceeds bound')
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite safety JSON')))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid safety JSON') from error
    if not isinstance(value, dict):
        raise ValueError('safety JSON object required')
    return value


def _policy(value, experiment_id):
    if not isinstance(value, dict) or not _REQUIRED <= set(value) or set(value) - (_REQUIRED | {'supervisor_health_required'}):
        raise ValueError('invalid paper safety policy fields')
    for key, expected in _FIXED.items():
        if type(value[key]) is not type(expected) or value[key] != expected:
            raise ValueError('unsupported paper safety policy limit')
    identity = value['experiment_id']
    if not isinstance(identity, str) or not 1 <= len(identity) <= 128 or identity != experiment_id:
        raise ValueError('paper safety experiment differs')
    volume = value['volume_root']
    if not isinstance(volume, str) or not Path(volume).is_absolute():
        raise ValueError('absolute declared volume root required')
    required = value.get('supervisor_health_required', True)
    if type(required) is not bool:
        raise ValueError('supervisor health requirement must be boolean')
    return dict(value, supervisor_health_required=required)


def load_policy(root, experiment_id):
    """Return strict schema1 policy, or None when the optional file is absent."""
    root = _directory(root)
    path = root / 'paper_safety.json'
    if _regular(path, missing=True) is None:
        return None
    return _policy(_read(path), experiment_id)


def inspect_volume(policy):
    """Bounded whole-volume logical file bytes; unknown measurement blocks entry.

    Legacy files count. Links/reparse points and other nonregular entries are
    never followed. A count/deadline/error makes the total unknown, not zero.
    """
    policy = _policy(policy, policy.get('experiment_id'))
    threshold = int(Decimal(policy['alert_fraction']) * policy['capacity_bytes'])
    result = dict(blocked=True, reasons=['volume_measurement_unknown'], used_bytes=None,
                  capacity_bytes=policy['capacity_bytes'], threshold_bytes=threshold)
    deadline = time.monotonic() + VOLUME_TIMEOUT_SECONDS
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('volume measurement deadline')
    try:
        pending = [_directory(policy['volume_root'])]
        used, count = 0, 0
        while pending:
            check()
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    check()
                    count += 1
                    if count > MAX_VOLUME_ENTRIES:
                        raise ValueError('volume measurement count bound')
                    info = entry.stat(follow_symlinks=False)
                    if _linked(info):
                        raise ValueError('volume contains a link')
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        used += info.st_size
                    else:
                        raise ValueError('volume contains a nonregular entry')
        check()
    except (OSError, ValueError, TimeoutError):
        return result
    blocked = used >= threshold
    return dict(result, blocked=blocked, reasons=['volume_threshold'] if blocked else [], used_bytes=used)


def _empty(policy):
    return dict(schema_version=1, experiment_id=policy['experiment_id'],
                policy_sha256=hashlib.sha256(_json(policy).encode()).hexdigest(),
                worker=None, supervisor=None)


def _state(root, policy):
    path = root / 'paper_safety_state.json'
    expected = _empty(policy)
    if _regular(path, missing=True) is None:
        return expected
    value = _read(path)
    if (set(value) != set(expected) or type(value.get('schema_version')) is not int or
            any(value[key] != expected[key] for key in ('schema_version', 'experiment_id', 'policy_sha256'))):
        raise ValueError('safety state policy/experiment differs')
    for name in ('worker', 'supervisor'):
        row = value[name]
        if row is None:
            continue
        fields = {'consecutive_errors', 'entry_halt', 'last_seen', 'success', 'error_kind'}
        if name == 'supervisor':
            fields.add('event_id')
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError('invalid safety health fields')
        if type(row['consecutive_errors']) is not int or not 0 <= row['consecutive_errors'] <= 3 or type(row['entry_halt']) is not bool:
            raise ValueError('invalid safety health counters')
        if (row['consecutive_errors'] == 3) != row['entry_halt']:
            raise ValueError('inconsistent safety error latch')
        _result(row['success'], row['error_kind'])
        if ((row['success'] and not row['entry_halt'] and row['consecutive_errors'] != 0) or
                (not row['success'] and row['consecutive_errors'] == 0)):
            raise ValueError('inconsistent safety error counter')
        _time(row['last_seen'])
        if name == 'supervisor':
            _event_id(row['event_id'])
    return value


def _write(root, value):
    path = root / 'paper_safety_state.json'
    raw = (_json(value) + '\n').encode()
    if len(raw) > _JSON_LIMIT:
        raise ValueError('safety state exceeds bound')
    stage = root / ('.paper_safety_state.' + uuid.uuid4().hex + '.tmp')
    try:
        with stage.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _regular(path, missing=True)
        os.replace(stage, path)
        _fsync_directory(root)
    finally:
        if stage.exists():
            stage.unlink()


def _result(success, error_kind):
    if type(success) is not bool:
        raise ValueError('literal safety success boolean required')
    if success:
        if error_kind is not None:
            raise ValueError('successful health cannot contain an error')
    elif not isinstance(error_kind, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', error_kind) is None:
        raise ValueError('bounded diagnostic code required; never exception text')


def _event_id(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None:
        raise ValueError('bounded supervisor cycle identity required')


def _record(root, *, name, success, error_kind, now, event_id=None):
    root = _directory(root)
    path = root / 'paper_safety.json'
    if _regular(path, missing=True) is None:
        return None
    raw_policy = _read(path)
    policy = _policy(raw_policy, raw_policy.get('experiment_id'))
    if success is False and error_kind is None:
        error_kind = name + '_failure'
    _result(success, error_kind)
    at = _time(_utc_now() if now is None else now)
    if at > _time(_utc_now()):
        raise ValueError('future safety health observation')
    if name == 'supervisor':
        _event_id(event_id)
    state_path = root / 'paper_safety_state.json'
    _regular(state_path.with_suffix('.json.lock'), missing=True)
    with _artifact_lock(state_path):
        # The lock guards both readers of previous counters and atomic replace.
        if load_policy(root, policy['experiment_id']) != policy:
            raise ValueError('paper safety policy changed')
        state = _state(root, policy)
        old = state[name]
        observation = dict(last_seen=at.isoformat(), success=success, error_kind=error_kind)
        if name == 'supervisor':
            observation['event_id'] = event_id
            if old is not None and old['event_id'] == event_id:
                if any(old[key] != value for key, value in observation.items()):
                    raise ValueError('conflicting supervisor cycle retry')
                return dict(old)
        if old is not None and (at < _time(old['last_seen']) or
                                (name == 'supervisor' and at == _time(old['last_seen']))):
            raise ValueError('out-of-order safety health observation')
        latched = old is not None and old['entry_halt']
        count = old['consecutive_errors'] if old else 0
        count = count if latched and success else (0 if success else min(3, count + 1))
        row = dict(observation, consecutive_errors=count, entry_halt=latched or count >= policy[f'max_{name}_errors'])
        state[name] = row
        _write(root, state)
        return dict(row)


def record_worker_result(root, *, success, error_kind=None, now=None):
    """Record one worker result; success never clears a latched entry halt."""
    return _record(root, name='worker', success=success, error_kind=error_kind, now=now)


def record_supervisor_result(root, *, success, event_id, now=None, error_kind=None):
    """Record latest supervisor cycle, with exact latest-event retry semantics."""
    return _record(root, name='supervisor', success=success, error_kind=error_kind, now=now, event_id=event_id)


def entry_status(root, *, policy, now=None):
    """Return entry-only admission. Callers must continue protective exits."""
    if policy is None:
        return dict(enabled=False, blocked=False, reasons=[], volume=None, worker=None, supervisor=None)
    policy = _policy(policy, policy.get('experiment_id'))
    volume = inspect_volume(policy)
    reasons = list(volume['reasons'])
    result = dict(enabled=True, blocked=True, reasons=reasons, volume=volume, worker=None, supervisor=None)
    try:
        root = _directory(root)
        if load_policy(root, policy['experiment_id']) != policy:
            raise ValueError('paper safety policy changed')
        state = _state(root, policy)
        at = _time(_utc_now() if now is None else now)
        for name in ('worker', 'supervisor'):
            row = state[name]
            result[name] = row
            if row is not None:
                if _time(row['last_seen']) > at:
                    raise ValueError('future health state')
                if row['entry_halt']:
                    reasons.append(name + '_error_halt')
        supervisor = state['supervisor']
        if policy['supervisor_health_required']:
            if supervisor is None:
                reasons.append('supervisor_health_missing')
            elif (at - _time(supervisor['last_seen'])).total_seconds() > policy['supervisor_stale_seconds']:
                reasons.append('supervisor_health_stale')
    except (OSError, ValueError):
        reasons.append('safety_state_unknown')
    result['blocked'] = bool(reasons)
    return result


def rotate_diagnostics(root, *, policy):
    """Rotate ONLY paper_runtime.log -> .1..3, retaining each whole generation.

    A full oldest generation expires at the fourth rotation. No finance/config
    file is enumerated, truncated or removed. Cooperating writers use the same
    artifact lock; all paths are checked before the first rename.
    """
    if policy is None:
        return None
    root = _directory(root)
    policy = _policy(policy, policy.get('experiment_id'))
    if load_policy(root, policy['experiment_id']) != policy:
        raise ValueError('paper safety policy changed')
    log = root / 'paper_runtime.log'
    _regular(log.with_suffix('.log.lock'), missing=True)
    with _artifact_lock(log):
        paths = [log] + [root / f'paper_runtime.log.{index}' for index in range(1, policy['diagnostics_generations'] + 1)]
        infos = [_regular(path, missing=True) for path in paths]
        if infos[0] is None or infos[0].st_size < policy['diagnostics_max_bytes']:
            return dict(rotated=False)
        # Replacement only expires the explicitly declared oldest generation.
        for index in range(len(paths) - 1, 0, -1):
            if infos[index - 1] is not None:
                os.replace(paths[index - 1], paths[index])
        with log.open('xb') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(root)
        return dict(rotated=True)


def append_diagnostic(root, *, policy, event, now=None, error_type=None):
    """Write one bounded code-only record; never persist exception messages."""
    if policy is None:
        return
    root = _directory(root)
    policy = _policy(policy, policy.get('experiment_id'))
    if load_policy(root, policy['experiment_id']) != policy:
        raise ValueError('paper safety policy changed')
    _event_id(event)
    if error_type is not None:
        _event_id(error_type)
    record = dict(at=_time(_utc_now() if now is None else now).isoformat(), event=event)
    if error_type is not None:
        record['error_type'] = error_type
    raw = (_json(record)+'\n').encode()
    if len(raw) > 1024:
        raise ValueError('diagnostic record exceeds bound')
    log = root/'paper_runtime.log'
    _regular(log.with_suffix('.log.lock'), missing=True)
    # Rotate first as well: a restart must not append to an oversized old log.
    rotate_diagnostics(root, policy=policy)
    with _artifact_lock(log):
        _regular(log, missing=True)
        with log.open('ab') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    rotate_diagnostics(root, policy=policy)
