"""One approved, fail-closed release of the two paper entry latches."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hermes_trading import paper_safety as ps
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.paired_worker import _active_storage_positions, _profiles, _stats
from hermes_trading.paper_backup import _verified
from hermes_trading.paper_publication import _release_hashes, _sync_dir
from hermes_trading.reflection_supervision import _hash, _time
from scripts.paper_storage_recovery import _save

ROOT = Path('/app/state')
AUDIT = ROOT / '.manual-latch-release-2026-11-17'
PENDING = ROOT / 'paper_supervisor_recovery_pending.json'
APPROVAL = 'user-2026-11-17-entry-latch-recovery'
RUNTIME = '3b7eb68929b48c502a3b041768069274d05e7dd1508cf97e56366737b7b82258'
OLD_REASONS = {'worker_error_halt', 'supervisor_error_halt'}


def read(path):
    return json.loads(path.read_bytes())


def fail(message):
    raise ValueError('manual latch recovery: ' + message)


def main(argv=None):
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--apply', action='store_true')
    parser.add_argument('--approval-ref')
    args = parser.parse_args(argv)
    if args.apply and args.approval_ref != APPROVAL:
        fail('exact approval reference required')
    if os.environ.get('HERMES_TRADING_MODE') != 'paper' or os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS') != 'false':
        fail('paper-only mode required')
    if _hash(_release_hashes()) != RUNTIME:
        fail('runtime differs')
    if ROOT.is_symlink() or PENDING.is_symlink() or AUDIT.is_symlink():
        fail('linked path')
    if read(ROOT / 'storage_limits.json').get('max_state_bytes') != 1000000000:
        fail('storage budget differs')
    policy = ps.load_policy(ROOT, 'paper-beta-v1-20261021-6c9d520')
    if policy is None or policy['capacity_bytes'] != 1000000000:
        fail('safety policy differs')
    if (AUDIT / 'receipt.json').exists():
        receipt = read(AUDIT / 'receipt.json')
        intent = read(AUDIT / 'intent.json')
        if receipt.get('intent_hash') != _hash(intent) or receipt.get('status') != 'released':
            fail('completed audit differs')
        print(json.dumps({'kind': 'manual_entry_latch_recovery', 'status': 'already_released',
                          'event_id': receipt['event_id']}), flush=True)
        return
    if AUDIT.exists() or PENDING.exists():
        fail('unfinished or foreign recovery requires manual reconciliation')

    at = datetime.now(timezone.utc)
    manifest = read(ROOT / 'manifest.json')
    profiles = _profiles(ROOT, manifest)
    primary = profiles[('adaptive', resolve_primary(manifest).wallet_eur)][0]
    stats = _stats(primary)
    if stats['pending'] or stats['latest'] is None or any(_active_storage_positions(profiles).values()):
        fail('pending batch or active position')
    heartbeat = read(ROOT / 'heartbeat.json')
    age = (at - _time(heartbeat['timestamp'])).total_seconds()
    lifecycle = heartbeat.get('worker_lifecycle', {})
    admission = heartbeat.get('entry_admission', {})
    backup = lifecycle.get('daily_backup', {})
    accounts = heartbeat.get('accounts', {})
    if (not 0 <= age <= 90 or heartbeat.get('status') != 'ok' or heartbeat.get('model_ready') is not True or
            heartbeat.get('batch_id') != stats['latest']['batch_id'] or
            set(accounts) != {f'{arm}_{wallet}' for arm in ('adaptive', 'control') for wallet in (50, 100, 150, 200)} or
            any(row.get('status') != 'observed' for row in accounts.values()) or
            set(admission.get('reasons', [])) != OLD_REASONS or
            lifecycle.get('status') != 'running' or lifecycle.get('storage_stop') is not False or
            lifecycle.get('storage_report', {}).get('status') != 'ready' or
            any(lifecycle.get(key) for key in ('stop_reason', 'daily_backup_error', 'local_archive_capture_error')) or
            backup.get('status') not in ('completed', 'already_completed') or
            backup.get('date') != at.date().isoformat()):
        fail('fresh settled healthy worker preflight differs')
    if not isinstance(backup.get('sha256'), str) or len(backup['sha256']) != 64:
        fail('daily backup digest missing')
    destination = Path(read(ROOT / 'worker_limits.json')['daily_backup']['destination'])
    backup_path = Path(backup['path'])
    if backup_path.parent != destination:
        fail('daily backup destination differs')
    manifest_bytes = (ROOT / 'manifest.json').read_bytes()
    identity = hashlib.sha256(ROOT.as_posix().encode() + b'\0' + manifest_bytes).hexdigest()
    verified = _verified(backup_path, identity, lambda: None, 500000000)
    if verified.get('sha256') != backup['sha256']:
        fail('daily backup receipt differs')

    state_path = ROOT / 'paper_safety_state.json'
    with ps._artifact_lock(state_path):
        current = datetime.now(timezone.utc)
        latest_heartbeat = read(ROOT / 'heartbeat.json')
        if (latest_heartbeat.get('batch_id') != heartbeat['batch_id'] or
                latest_heartbeat.get('timestamp') != heartbeat['timestamp'] or
                not 0 <= (current - _time(heartbeat['timestamp'])).total_seconds() <= 90):
            fail('worker heartbeat advanced during preflight')
        if ps.load_policy(ROOT, policy['experiment_id']) != policy:
            fail('safety policy changed')
        before = ps._state(ROOT, policy)
        for name in ('worker', 'supervisor'):
            row = before[name]
            if (row is None or row.get('success') is not True or row.get('entry_halt') is not True or
                    row.get('consecutive_errors') != 3 or
                    not 0 <= (current - _time(row['last_seen'])).total_seconds() <= 90):
                fail(name + ' recent success latch differs')
        actual = ps.entry_status(ROOT, policy=policy, now=current)
        if set(actual.get('reasons', [])) != OLD_REASONS:
            fail('another entry blocker exists')
        if args.check:
            print(json.dumps({'kind': 'manual_entry_latch_recovery', 'status': 'preflight_ready',
                              'batch_id': heartbeat['batch_id'], 'backup_verified': True,
                              'active_positions': 0, 'reasons': actual['reasons']}), flush=True)
            return
        if PENDING.exists() or AUDIT.exists():
            fail('recovery namespace appeared')
        after = json.loads(json.dumps(before))
        for name in ('worker', 'supervisor'):
            after[name].update(entry_halt=False, consecutive_errors=0)
        body = {'schema_version': 1, 'approval_ref': APPROVAL, 'runtime': RUNTIME,
                'root': str(ROOT), 'observed_at': current.isoformat(),
                'batch_id': heartbeat['batch_id'], 'backup_sha256': backup['sha256'],
                'backup_verified': True,
                'before': before, 'after': after}
        event_id = _hash(body)
        AUDIT.mkdir(mode=0o700)
        _sync_dir(ROOT)
        _save(AUDIT / 'intent.json', dict(body, event_id=event_id))
        _save(PENDING, {'schema_version': 1, 'purpose': 'manual_entry_latch_recovery', 'event_id': event_id})
        ps._write(ROOT, after)
        if ps._state(ROOT, policy) != after:
            fail('safety state write differs')
        _save(AUDIT / 'receipt.json', {'schema_version': 1, 'status': 'released',
                                      'event_id': event_id, 'intent_hash': _hash(dict(body, event_id=event_id))})
        PENDING.unlink()
        _sync_dir(ROOT)
    result = ps.entry_status(ROOT, policy=policy)
    print(json.dumps({'kind': 'manual_entry_latch_recovery', 'status': 'released',
                      'event_id': event_id, 'entry_blocked': result['blocked'],
                      'reasons': result['reasons']}), flush=True)


if __name__ == '__main__':
    main()
