"""Read-only paper-worker snapshots and supervised release preflight."""
import argparse
import base64
import json
import os
from pathlib import Path
import uuid

import yaml

from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash, _now, _time, load_owner_policy
from hermes_trading.paired_primary import PrimarySelection,read_primary_context,validate_primary_account

FILES = ('reflection_owner.json', 'goal.yaml', 'strategy.yaml', 'trades.jsonl', 'heartbeat.json', 'wallet_metrics.json')
CONFIG_FILES = ('reflection_owner.json', 'goal.yaml', 'strategy.yaml')


def _pid_is_live(pid):
    if os.name == 'nt':
        # Windows os.kill(..., 0) must not be used as a liveness probe.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def capture_startup_release(state_dir, *, now=None,primary=None):
    policy = load_owner_policy(state_dir)
    result={'hashes': _release_hashes(), 'policy_hash': _hash(policy) if policy is not None else None,
            'started_at': _now(now).isoformat(), 'instance_id': str(uuid.uuid4()), 'pid': os.getpid()}
    if primary is not None and primary.versioned:
        context=read_primary_context(state_dir)
        if context is None or context[1]!=primary:raise ValueError('worker startup primary differs from manifest')
        account=json.loads((Path(state_dir)/'wallet_account.json').read_text(encoding='utf-8'))
        digest=validate_primary_account(account,primary,experiment_id=policy['experiment_id'])
        result.update(primary_wallet_eur=primary.wallet_eur,primary_selection_sha256=primary.selection_sha256,account_hash=digest)
    return result


def validate_snapshot(snapshot, expected_policy, *, now=None,expected_primary=None,expected_account_hash=None):
    current = _now(now)
    versioned=isinstance(expected_primary,PrimarySelection) and expected_primary.versioned
    if expected_primary is not None and not isinstance(expected_primary,PrimarySelection):raise ValueError('snapshot primary expectation differs')
    if versioned and (type(expected_account_hash) is not str or len(expected_account_hash)!=64):
        raise ValueError('versioned snapshot requires the expected account hash')
    if not isinstance(snapshot, dict) or type(snapshot.get('schema_version')) is not int or snapshot['schema_version'] != (2 if versioned else 1):
        raise ValueError('unsupported worker snapshot')
    if snapshot.get('disk_release') != _release_hashes():
        raise ValueError('worker files differ from the expected release')
    encoded = snapshot.get('files')
    if not isinstance(encoded, dict) or set(encoded) != set(FILES):
        raise ValueError('incomplete worker snapshot')
    try:
        files = {name: base64.b64decode(encoded[name], validate=True) for name in FILES}
        policy = json.loads(files['reflection_owner.json'])
        heartbeat = json.loads(files['heartbeat.json'])
        strategy = yaml.safe_load(files['strategy.yaml'])
        goal = yaml.safe_load(files['goal.yaml'])
        rows = [json.loads(line) for line in files['trades.jsonl'].decode('utf-8').splitlines() if line.strip()]
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError('worker snapshot contains invalid records') from error
    if not isinstance(goal, dict) or not isinstance(strategy, dict) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('worker snapshot records must be objects')
    if policy != expected_policy or policy.get('owner') != 'external_llm':
        raise ValueError('worker experiment/owner differs from the supervisor')
    if not isinstance(heartbeat, dict) or heartbeat.get('status') != 'ok':
        raise ValueError('worker is not healthy')
    if 'worker_lifecycle' in heartbeat:
        from hermes_trading.market_data import timeframe_interval
        sources=heartbeat.get('market_sources')
        if not isinstance(sources,dict) or not sources:raise ValueError('paired market source evidence is missing')
        if heartbeat['worker_lifecycle'].get('status')!='running' or heartbeat.get('model_ready') is not True:
            raise ValueError('paired worker supervision is not ready')
        for row in sources.values():
            if row.get('valid') is not True or not 0<=(current-_time(row.get('received_at'))).total_seconds()<=300:
                raise ValueError('paired market quote is invalid or stale')
            if not 0<=(current-_time(row.get('candle_closed_at'))).total_seconds()<=2*timeframe_interval(row.get('timeframe')).total_seconds():
                raise ValueError('paired market candle is invalid or stale')
    if heartbeat.get('mode') not in {'training', 'paper', 'train', 'demo'} or heartbeat.get('execution') != 'simulated':
        raise ValueError('worker is not executing in paper mode')
    stamp = _time(heartbeat.get('timestamp'))
    if not 0 <= (current-stamp).total_seconds() <= 600:
        raise ValueError('worker heartbeat is stale or future-dated')
    release = heartbeat.get('worker_release')
    if not isinstance(release, dict) or release.get('hashes') != snapshot['disk_release'] or release.get('policy_hash') != _hash(policy):
        raise ValueError('worker startup release/owner has not been verified')
    primary_fields={'primary_wallet_eur','primary_selection_sha256','account_hash'}
    if versioned:
        selected=PrimarySelection(release.get('primary_wallet_eur'),release.get('primary_selection_sha256'))
        if not selected.versioned or selected!=expected_primary or release.get('account_hash')!=expected_account_hash:
            raise ValueError('worker startup primary/account differs from the launcher')
    elif primary_fields.intersection(release):raise ValueError('versioned primary cannot use a legacy snapshot')
    if _time(release.get('started_at')) > stamp or not isinstance(release.get('instance_id'), str):
        raise ValueError('worker startup identity is invalid')
    uuid.UUID(release['instance_id'])
    if type(release.get('pid')) is not int or release['pid'] <= 0 or snapshot.get('worker_process_live') is not True:
        raise ValueError('worker process identity is not live')
    if str(strategy.get('version')) != str(heartbeat.get('strategy_version')):
        raise ValueError('worker has not observed the current strategy version')
    if files['wallet_metrics.json']:
        report=json.loads(files['wallet_metrics.json'])
        if not isinstance(report,dict) or report.get('status')!='observed' or report.get('experiment_id')!=policy['experiment_id']:
            raise ValueError('wallet report is not a current experiment observation')
        if heartbeat.get('wallet_report_hash')!=_hash(report):
            raise ValueError('wallet report does not match the worker heartbeat')
        if not 0<=(current-_time(report.get('observed_at'))).total_seconds()<=600:
            raise ValueError('wallet report is stale or future-dated')
        if versioned:
            observation=report.get('observation')
            from hermes_trading.wallet_metrics import _decimal
            if (type(observation) is not dict or observation.get('account_config_hash')!=expected_account_hash or
                    observation.get('experiment_id')!=policy['experiment_id'] or observation.get('currency')!='EUR' or
                    _decimal(observation.get('starting_balance'))!=expected_primary.wallet_eur):
                raise ValueError('worker report primary account/capital differs')
    elif versioned or heartbeat.get('wallet_account') is not None or policy.get('wallet_required') is True:
        raise ValueError('configured wallet report is missing')
    return files


def make_snapshot(state_dir, *, now=None):
    state_dir = Path(state_dir)
    context=read_primary_context(state_dir)
    primary=None if context is None else context[1]
    account_raw=(state_dir/'wallet_account.json').read_bytes() if primary is not None else None
    account_hash=None if account_raw is None else validate_primary_account(json.loads(account_raw),primary)
    from hermes_trading.paper_publication import assert_no_pending_paired_batch
    assert_no_pending_paired_batch(state_dir)
    original = {name: (state_dir/name).read_bytes() for name in CONFIG_FILES}
    files = dict(original)
    files['trades.jsonl'] = (state_dir/'trades.jsonl').read_bytes() if (state_dir/'trades.jsonl').exists() else b''
    files['heartbeat.json'] = (state_dir/'heartbeat.json').read_bytes()
    files['wallet_metrics.json'] = (state_dir/'wallet_metrics.json').read_bytes() if (state_dir/'wallet_metrics.json').exists() else b''
    heartbeat = json.loads(files['heartbeat.json'])
    live = False
    release = heartbeat.get('worker_release') if isinstance(heartbeat, dict) else None
    if isinstance(release, dict) and type(release.get('pid')) is int and release['pid'] > 0:
        live = _pid_is_live(release['pid'])
    snapshot = {'schema_version': 2 if primary is not None else 1, 'files': {name: base64.b64encode(files[name]).decode('ascii') for name in FILES},
                'disk_release': _release_hashes(), 'worker_process_live': live}
    if any((state_dir/name).read_bytes() != data for name, data in original.items()):
        raise ValueError('worker configuration changed during snapshot')
    if account_raw is not None and (state_dir/'wallet_account.json').read_bytes()!=account_raw:
        raise ValueError('worker account changed during snapshot')
    if context is not None and read_primary_context(state_dir)!=context:raise ValueError('worker primary changed during snapshot')
    validate_snapshot(snapshot, json.loads(original['reflection_owner.json']), now=now,
                      expected_primary=primary,expected_account_hash=account_hash)
    assert_no_pending_paired_batch(state_dir)
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(make_snapshot(Path(args.state_dir)), separators=(',', ':')))


if __name__ == '__main__': main()
