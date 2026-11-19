"""Optional paper entry admission; never changes finance, strategy or exit rules.

The explicit ``root/paper_safety.json`` binds one experiment to a whole-volume
500 MB or reviewed 1 GB budget, a 70% entry alert and three-error latches. Health is durable in
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
    'schema_version': 1, 'alert_fraction': '0.70',
    'max_worker_errors': 3, 'max_supervisor_errors': 3,
    'supervisor_stale_seconds': 300, 'diagnostics_max_bytes': 1048576,
    'diagnostics_generations': 3,
}
_REQUIRED = set(_FIXED) | {'experiment_id', 'volume_root', 'capacity_bytes'}


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
    if type(value['capacity_bytes']) is not int or value['capacity_bytes'] not in (500000000, 1000000000):
        raise ValueError('unsupported paper safety capacity')
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
        # Check after the state read: a recovery may have begun during it.
        if _recovery_pending(root):
            reasons.append('supervisor_recovery_pending')
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


_RECOVERY_PENDING = 'paper_supervisor_recovery_pending.json'


def _recovery_pending(root):
    return _regular(root / _RECOVERY_PENDING, missing=True) is not None


def _recovery_hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _recovery_once(path, value):
    """Immutable bounded artifact; caller owns the safety lock."""
    raw = (_json(value) + '\n').encode()
    if len(raw) > _JSON_LIMIT:
        raise ValueError('recovery artifact exceeds bound')
    if _regular(path, missing=True) is not None:
        if _read(path) != value:
            raise ValueError('conflicting recovery artifact')
        return
    stage = path.parent / ('.recovery.' + uuid.uuid4().hex + '.tmp')
    try:
        with stage.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if _regular(path, missing=True) is not None:
            raise ValueError('recovery artifact appeared during preparation')
        os.replace(stage, path)
        _fsync_directory(path.parent)
    finally:
        if stage.exists(): stage.unlink()


def _recovery_clear_marker(root, marker):
    path = root / _RECOVERY_PENDING
    if _read(path) != marker:
        raise ValueError('recovery pending marker changed')
    path.unlink()
    _fsync_directory(root)


def recover_supervisor(root, request, *, preflight, now=None, enabled=False, abort=False):
    """Opt-in operator transition, never called by normal health/snapshot paths.

    preflight is a context manager holding verified read-only publication/account
    transactions. It is entered BEFORE the safety lock (worker lock order).
    It must yield the exact checked binding dictionary from the request.
    """
    from contextlib import nullcontext
    if enabled is not True or type(abort) is not bool:
        raise ValueError('explicit recovery opt-in required')
    root = _directory(Path(root))
    request = _pairs(list(request.items())) if isinstance(request, dict) else None
    if request is None or len(_json(request).encode()) > _JSON_LIMIT:
        raise ValueError('bounded recovery request required')
    identifier = request.get('request_id')
    if not isinstance(identifier, str) or re.fullmatch('[0-9a-f]{32}', identifier) is None:
        raise ValueError('recovery request identity differs')
    policy = load_policy(root, request.get('experiment_id'))
    if policy is None: raise ValueError('recovery requires existing safety policy')
    state_path = root / 'paper_safety_state.json'
    _regular(state_path); _regular(state_path.with_suffix('.json.lock'), missing=True)
    directory = root / 'paper_supervisor_recovery'
    if directory.exists(): _directory(directory)
    intent_path = directory / (identifier + '.intent.json')
    receipt_path = directory / (identifier + '.receipt.json')
    abort_path = directory / (identifier + '.abort.json')
    request_hash = _recovery_hash(request)
    root_hash = _recovery_hash(str(root))
    marker = dict(schema_version=1, request_id=identifier, request_hash=request_hash, root_hash=root_hash)

    def read_intent():
        intent = _read(intent_path)
        if (set(intent) != {'schema_version', 'request', 'request_hash', 'root_hash', 'before', 'after_hash'} or
                intent['schema_version'] != 1 or intent['request'] != request or
                intent['request_hash'] != request_hash or intent['root_hash'] != root_hash or
                _recovery_hash(intent['before']) != request['state_hash']):
            raise ValueError('recovery intent differs')
        after = json.loads(_json(intent['before']))
        after['supervisor'].update(entry_halt=False, consecutive_errors=0)
        if _recovery_hash(after) != intent['after_hash']:
            raise ValueError('recovery target differs')
        return intent, after

    def receipt(intent, outcome, before, after):
        return dict(schema_version=1, request_id=identifier, request_hash=request_hash,
                    root_hash=root_hash, intent_hash=_recovery_hash(intent),
                    before_hash=_recovery_hash(before), after_hash=_recovery_hash(after),
                    before=before, after=after, outcome=outcome)

    def checked_receipt(path, intent, outcome):
        done = _read(path)
        before = done['before']
        after = json.loads(_json(before))
        after['supervisor'].update(entry_halt=outcome == 'aborted',
                                   consecutive_errors=3 if outcome == 'aborted' else 0)
        if ((outcome == 'completed' and before != intent['before']) or
                done != receipt(intent, outcome, before, after)):
            raise ValueError('recovery receipt differs')
        return done

    # A confirmed historical retry must not reset a newer health state.
    with _artifact_lock(state_path):
        if load_policy(root, policy['experiment_id']) != policy:
            raise ValueError('recovery safety policy changed')
        if _regular(abort_path, missing=True) is not None:
            intent, after = read_intent()
            done = checked_receipt(abort_path, intent, 'aborted')
            state = _state(root, policy)
            if _recovery_pending(root):
                if state['supervisor']['entry_halt'] is not True:
                    raise ValueError('aborted recovery no longer halted')
                _recovery_clear_marker(root, marker)
            return dict(done, status='already_aborted',
                        current_supervisor_halted=state['supervisor']['entry_halt'])
        if _regular(receipt_path, missing=True) is not None:
            intent, after = read_intent()
            done = checked_receipt(receipt_path, intent, 'completed')
            if not _recovery_pending(root):
                return dict(done, status='already_completed',
                            current_supervisor_halted=_state(root, policy)['supervisor']['entry_halt'])
            # A remaining marker needs fresh preflight, or conservative abort.

    with (nullcontext(request.get('bindings')) if abort else preflight()) as bindings:
        if bindings != request.get('bindings'):
            raise ValueError('recovery preflight binding differs')
        with _artifact_lock(state_path):
            if load_policy(root, policy['experiment_id']) != policy:
                raise ValueError('recovery safety policy changed')
            at = _time(_utc_now() if now is None else now)
            if not abort and not (_time(request['prepared_at']) <= at < _time(request['valid_until']) and
                                  (_time(request['valid_until'])-_time(request['prepared_at'])).total_seconds() == 120):
                raise ValueError('recovery request expired or future-dated')
            state = _state(root, policy)
            if _regular(intent_path, missing=True) is not None:
                intent, after = read_intent()
            else:
                if abort: raise ValueError('no recovery intent to abort')
                if _recovery_hash(state) != request.get('state_hash'):
                    raise ValueError('recovery safety state changed')
                after = json.loads(_json(state))
                after['supervisor'].update(entry_halt=False, consecutive_errors=0)
                intent = dict(schema_version=1, request=request, request_hash=request_hash,
                              root_hash=root_hash, before=state, after_hash=_recovery_hash(after))
            before = intent['before']
            if not abort and state not in (before, after):
                raise ValueError('recovery cannot replace newer health')
            if not abort and state == after and not _recovery_pending(root):
                raise ValueError('unreceipted cleared state requires pending marker')
            supervisor = before['supervisor']; worker = before['worker']
            if (supervisor is None or supervisor['entry_halt'] is not True or supervisor['consecutive_errors'] != 3 or
                    supervisor['success'] is not True or supervisor['event_id'] != request['supervisor_event_id'] or
                    supervisor['last_seen'] != request['supervisor_observed_at'] or worker is None or
                    worker['entry_halt'] is not False or worker['success'] is not True):
                raise ValueError('recovery health or latch differs')
            if not abort and any(not 0 <= (at-_time(row['last_seen'])).total_seconds() <= 60 for row in (supervisor, worker)):
                raise ValueError('recovery health is stale')
            if _recovery_pending(root) and _read(root/_RECOVERY_PENDING) != marker:
                raise ValueError('another recovery is pending')
            if not directory.exists(): directory.mkdir(); _fsync_directory(root)
            _directory(directory)
            members = list(directory.iterdir())
            targets={intent_path,abort_path if abort else receipt_path}
            if (len(set(members)|targets)>96 or any(not _regular(path) or
                    re.fullmatch('[0-9a-f]{32}\\.(intent|receipt|abort)\\.json',path.name) is None for path in members)):
                raise ValueError('recovery audit inventory differs')
            if not intent_path.exists() and len(list(directory.glob('*.intent.json'))) >= 32:
                raise ValueError('recovery audit capacity reached')
            _recovery_once(intent_path, intent)
            _recovery_once(root/_RECOVERY_PENDING, marker)
            target = json.loads(_json(state)) if abort else after
            if abort:
                # Cancellation adds a halt; it never restores older health.
                target['supervisor'].update(entry_halt=True, consecutive_errors=3)
            if state != target: _write(root, target)
            if _state(root, policy) != target: raise ValueError('recovery transition verification failed')
            done = receipt(intent, 'aborted' if abort else 'completed', state if abort else before, target)
            _recovery_once(abort_path if abort else receipt_path, done)
            _recovery_clear_marker(root, marker)
            return dict(done, status=done['outcome'], current_supervisor_halted=target['supervisor']['entry_halt'])


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


# --- Audited automatic recovery of the worker error latch (paper only) -----
#
# Same lane as the 17/11 manual latch release and recover_supervisor: one
# shared pending marker (``paper_supervisor_recovery_pending.json``) blocks
# entries while a transition is in flight and makes every recovery mutually
# exclusive; the immutable intent/receipt pair is the audit trail. The worker
# latch is lifted only; the supervisor latch keeps its own procedure.
WORKER_RECOVERY_SUCCESSES = 3
WORKER_RECOVERY_PURPOSE = 'paper_worker_recovery_v1'
WORKER_RECOVERY_DIRECTORY = 'paper_worker_recovery'
WORKER_RECOVERY_ACCOUNTS = frozenset(f'{arm}_{wallet}' for arm in ('adaptive', 'control')
                                     for wallet in (50, 100, 150, 200))
_WORKER_RECOVERY_ALLOWED_REASONS = frozenset({'worker_error_halt', 'supervisor_error_halt'})
_WORKER_REQUEST_FIELDS = {'schema_version', 'purpose', 'request_id', 'experiment_id', 'mode',
                          'prepared_at', 'valid_until', 'state_hash', 'successes', 'bindings'}
_WORKER_BINDING_FIELDS = {'accounts', 'pending_batch', 'daily_backup', 'daily_backup_error',
                          'storage', 'local_archive_capture_error'}


class WorkerRecoveryRefused(ValueError):
    """A precondition of the worker latch recovery is not met (bounded code)."""

    def __init__(self, code):
        if re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code) is None:
            raise ValueError('bounded refusal code required')
        super().__init__('worker recovery refused: ' + code)
        self.code = code


def _worker_mode(enabled, env):
    if enabled is not True:
        raise ValueError('explicit worker recovery opt-in required')
    env = os.environ if env is None else env
    # The paired worker is paper-only; an explicit other mode always refuses.
    if str(env.get('HERMES_TRADING_MODE', 'paper')).strip().lower() != 'paper':
        raise WorkerRecoveryRefused('mode_not_paper')


def _worker_request(request):
    request = _pairs(list(request.items())) if isinstance(request, dict) else None
    if request is None or set(request) != _WORKER_REQUEST_FIELDS or len(_json(request).encode()) > _JSON_LIMIT:
        raise ValueError('bounded worker recovery request required')
    if (request['schema_version'] != 1 or type(request['schema_version']) is not int or
            request['purpose'] != WORKER_RECOVERY_PURPOSE):
        raise ValueError('worker recovery request purpose differs')
    if not isinstance(request['request_id'], str) or re.fullmatch('[0-9a-f]{32}', request['request_id']) is None:
        raise ValueError('worker recovery request identity differs')
    if request['mode'] != 'paper':
        raise WorkerRecoveryRefused('mode_not_paper')
    if not isinstance(request['state_hash'], str) or re.fullmatch('[0-9a-f]{64}', request['state_hash']) is None:
        raise ValueError('worker recovery state hash differs')
    successes = request['successes']
    if not isinstance(successes, list) or len(successes) != WORKER_RECOVERY_SUCCESSES:
        raise WorkerRecoveryRefused('successes_insufficient')
    previous = None
    for row in successes:
        if not isinstance(row, dict) or set(row) != {'at', 'batch_id'}:
            raise ValueError('worker recovery success record differs')
        at = _time(row['at'])
        if not isinstance(row['batch_id'], str) or not 1 <= len(row['batch_id']) <= 128:
            raise ValueError('worker recovery success batch differs')
        if previous is not None and at <= previous:
            raise WorkerRecoveryRefused('successes_not_consecutive')
        previous = at
    if len({row['batch_id'] for row in successes}) != len(successes):
        raise WorkerRecoveryRefused('successes_not_consecutive')
    if not isinstance(request['bindings'], dict):
        raise ValueError('worker recovery bindings required')
    return request


def _worker_bindings(bindings, at):
    """Cumulative preconditions measured by the worker preflight."""
    if not isinstance(bindings, dict) or set(bindings) != _WORKER_BINDING_FIELDS:
        raise ValueError('worker recovery bindings differ')
    accounts = bindings['accounts']
    if (not isinstance(accounts, dict) or set(accounts) != WORKER_RECOVERY_ACCOUNTS or
            any(type(value) is not int or value < 0 for value in accounts.values())):
        raise ValueError('worker recovery accounts differ')
    if any(accounts.values()):
        raise WorkerRecoveryRefused('accounts_not_flat')
    if bindings['pending_batch'] is not False:
        raise WorkerRecoveryRefused('batch_pending')
    backup = bindings['daily_backup']
    if (bindings['daily_backup_error'] is not None or not isinstance(backup, dict) or
            set(backup) != {'date', 'sha256', 'status', 'verified'} or backup['verified'] is not True or
            backup['date'] != at.date().isoformat() or
            backup['status'] not in ('completed', 'already_completed') or
            not isinstance(backup['sha256'], str) or re.fullmatch('[0-9a-f]{64}', backup['sha256']) is None):
        raise WorkerRecoveryRefused('daily_backup_missing')
    if bindings['storage'] != {'status': 'ready', 'storage_stop': False}:
        raise WorkerRecoveryRefused('storage_not_ready')
    if bindings['local_archive_capture_error'] is not None:
        raise WorkerRecoveryRefused('archive_capture_error')


def _worker_paths(root, identifier):
    directory = root / WORKER_RECOVERY_DIRECTORY
    return (directory, directory / (identifier + '.intent.json'),
            directory / (identifier + '.receipt.json'), directory / (identifier + '.abort.json'))


def _worker_intent(path, *, request_id, root_hash):
    intent = _read(path)
    if (set(intent) != {'schema_version', 'request', 'request_hash', 'root_hash', 'before_worker', 'after_worker'} or
            intent['schema_version'] != 1 or not isinstance(intent['request'], dict) or
            intent['request'].get('request_id') != request_id or
            intent['request_hash'] != _recovery_hash(intent['request']) or intent['root_hash'] != root_hash):
        raise ValueError('worker recovery intent differs')
    before = intent['before_worker']
    if not isinstance(before, dict) or before.get('entry_halt') is not True:
        raise ValueError('worker recovery intent differs')
    if intent['after_worker'] != dict(before, entry_halt=False, consecutive_errors=0):
        raise ValueError('worker recovery target differs')
    return intent


def _worker_receipt(intent, outcome):
    request = intent['request']
    after = intent['after_worker'] if outcome == 'completed' else intent['before_worker']
    return dict(schema_version=1, purpose=WORKER_RECOVERY_PURPOSE, request_id=request['request_id'],
                request_hash=intent['request_hash'], root_hash=intent['root_hash'],
                intent_hash=_recovery_hash(intent), before_worker=intent['before_worker'],
                after_worker=after, outcome=outcome)


def _worker_marker(intent):
    return dict(schema_version=1, kind='worker_recovery', request_id=intent['request']['request_id'],
                request_hash=intent['request_hash'], root_hash=intent['root_hash'])


def _manual_release_unfinished(root):
    # The 17/11 manual release (scripts/paper_entry_latch_recovery_20261117.py)
    # keeps its own audit; an unfinished one needs manual reconciliation.
    for audit in root.glob('.manual-latch-release-*'):
        if not (audit / 'receipt.json').is_file():
            return True
    return False


def _worker_inventory(directory, intent_path, extra):
    if not directory.exists():
        directory.mkdir()
        _fsync_directory(directory.parent)
    _directory(directory)
    members = list(directory.iterdir())
    if (len(set(members) | {intent_path, extra}) > 96 or any(
            _regular(path) is None or
            re.fullmatch('[0-9a-f]{32}\\.(intent|receipt|abort)\\.json', path.name) is None for path in members)):
        raise ValueError('worker recovery audit inventory differs')
    if not intent_path.exists() and len(list(directory.glob('*.intent.json'))) >= 32:
        raise WorkerRecoveryRefused('audit_capacity_reached')


def reconcile_worker_recovery(root, *, experiment_id, enabled=False, env=None):
    """Finish or cancel an interrupted worker recovery found at startup.

    Only the worker's own pending marker is handled; a supervisor or manual
    marker is left to its procedure (entries stay blocked meanwhile). The
    state already written is receipted; an unwritten one is aborted, keeping
    the latch. Never re-checks or relaxes a precondition.
    """
    _worker_mode(enabled, env)
    root = _directory(Path(root))
    policy = load_policy(root, experiment_id)
    if policy is None:
        return None
    state_path = root / 'paper_safety_state.json'
    _regular(state_path.with_suffix('.json.lock'), missing=True)
    with _artifact_lock(state_path):
        if not _recovery_pending(root):
            return None
        marker = _read(root / _RECOVERY_PENDING)
        if marker.get('kind') != 'worker_recovery':
            return None
        identifier = marker.get('request_id')
        if not isinstance(identifier, str) or re.fullmatch('[0-9a-f]{32}', identifier) is None:
            raise ValueError('worker recovery marker differs')
        directory, intent_path, receipt_path, abort_path = _worker_paths(root, identifier)
        _directory(directory)
        intent = _worker_intent(intent_path, request_id=identifier, root_hash=_recovery_hash(str(root)))
        if _worker_marker(intent) != marker:
            raise ValueError('worker recovery marker differs')
        for path, outcome in ((receipt_path, 'completed'), (abort_path, 'aborted')):
            if _regular(path, missing=True) is not None:
                if _read(path) != _worker_receipt(intent, outcome):
                    raise ValueError('worker recovery receipt differs')
                _recovery_clear_marker(root, marker)
                return dict(_read(path), status='already_' + outcome)
        worker = _state(root, policy)['worker']
        if worker == intent['after_worker']:
            path, outcome = receipt_path, 'completed'
        elif worker == intent['before_worker']:
            path, outcome = abort_path, 'aborted'
        else:
            raise ValueError('interrupted worker recovery state differs')
        done = _worker_receipt(intent, outcome)
        _recovery_once(path, done)
        _recovery_clear_marker(root, marker)
        return dict(done, status='reconciled_' + outcome)


def recover_worker(root, request, *, preflight, now=None, enabled=False, env=None):
    """Lift the worker error latch after N consecutive successful cycles.

    Opt-in transition, paper only, called by the worker itself. ``preflight``
    is a context manager re-measuring the cumulative preconditions (flat
    accounts, no pending batch, today's verified daily backup, ready storage
    without storage stop); it must yield exactly ``request['bindings']``. It is
    entered BEFORE the safety lock (worker lock order). Under the lock the
    latch, its fresh successful observation and the remaining entry blockers
    are verified; then an immutable intent, the shared pending marker, the
    new state and the receipt are written in that order.
    """
    _worker_mode(enabled, env)
    root = _directory(Path(root))
    request = _worker_request(request)
    identifier = request['request_id']
    policy = load_policy(root, request['experiment_id'])
    if policy is None:
        raise ValueError('worker recovery requires existing safety policy')
    state_path = root / 'paper_safety_state.json'
    _regular(state_path)
    _regular(state_path.with_suffix('.json.lock'), missing=True)
    directory, intent_path, receipt_path, abort_path = _worker_paths(root, identifier)
    if directory.exists():
        _directory(directory)
    request_hash = _recovery_hash(request)
    root_hash = _recovery_hash(str(root))

    with _artifact_lock(state_path):
        # A confirmed retry never reapplies the transition.
        for path, outcome in ((receipt_path, 'completed'), (abort_path, 'aborted')):
            if _regular(path, missing=True) is not None:
                intent = _worker_intent(intent_path, request_id=identifier, root_hash=root_hash)
                if intent['request'] != request or _read(path) != _worker_receipt(intent, outcome):
                    raise ValueError('worker recovery receipt differs')
                return dict(_read(path), status='already_' + outcome,
                            current_worker_halted=_state(root, policy)['worker']['entry_halt'])

    with preflight() as bindings:
        if bindings != request['bindings']:
            raise WorkerRecoveryRefused('preflight_changed')
        at = _time(_utc_now() if now is None else now)
        _worker_bindings(bindings, at)
        with _artifact_lock(state_path):
            if load_policy(root, policy['experiment_id']) != policy:
                raise ValueError('worker recovery safety policy changed')
            if not (_time(request['prepared_at']) <= at < _time(request['valid_until']) and
                    (_time(request['valid_until']) - _time(request['prepared_at'])).total_seconds() == 120):
                raise ValueError('worker recovery request expired or future-dated')
            state = _state(root, policy)
            if _recovery_pending(root):
                raise WorkerRecoveryRefused('recovery_pending')
            if _manual_release_unfinished(root):
                raise WorkerRecoveryRefused('manual_release_unfinished')
            if _regular(intent_path, missing=True) is not None:
                raise ValueError('unreceipted worker recovery intent requires reconciliation')
            if _recovery_hash(state) != request['state_hash']:
                raise WorkerRecoveryRefused('safety_state_changed')
            worker = state['worker']
            if worker is None or worker['entry_halt'] is not True or worker['consecutive_errors'] != 3:
                raise WorkerRecoveryRefused('worker_not_latched')
            if worker['success'] is not True or worker['last_seen'] != request['successes'][-1]['at']:
                raise WorkerRecoveryRefused('worker_last_cycle_not_successful')
            if not 0 <= (at - _time(worker['last_seen'])).total_seconds() <= 60:
                raise WorkerRecoveryRefused('worker_health_stale')
            # The cause has disappeared: no other entry blocker than the
            # latches themselves (the supervisor keeps its own procedure).
            status = entry_status(root, policy=policy, now=at)
            if 'worker_error_halt' not in status['reasons']:
                raise WorkerRecoveryRefused('worker_not_latched')
            if set(status['reasons']) - _WORKER_RECOVERY_ALLOWED_REASONS:
                raise WorkerRecoveryRefused('entry_blocker_present')
            _worker_inventory(directory, intent_path, receipt_path)
            after_worker = dict(worker, entry_halt=False, consecutive_errors=0)
            intent = dict(schema_version=1, request=request, request_hash=request_hash, root_hash=root_hash,
                          before_worker=worker, after_worker=after_worker)
            marker = _worker_marker(intent)
            _recovery_once(intent_path, intent)
            _recovery_once(root / _RECOVERY_PENDING, marker)
            target = dict(state, worker=after_worker)
            _write(root, target)
            if _state(root, policy) != target:
                raise ValueError('worker recovery transition verification failed')
            done = _worker_receipt(intent, 'completed')
            _recovery_once(receipt_path, done)
            _recovery_clear_marker(root, marker)
            return dict(done, status='completed', current_worker_halted=False)
