"""Run the normal paper worker with allowlisted, nonfinancial health logs."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import os
import stat

ACCOUNTS = {f'{arm}_{wallet}' for arm in ('adaptive', 'control') for wallet in (50, 100, 150, 200)}
REASONS = {'volume_threshold', 'volume_measurement_unknown', 'worker_error_halt',
           'supervisor_error_halt', 'supervisor_health_missing', 'supervisor_health_stale',
           'supervisor_recovery_pending', 'safety_state_unknown'}
MAX_HEARTBEAT_BYTES = 1024 * 1024


def storage_inventory(root):
    """Return fixed technical buckets, never file paths or financial values."""
    names=ACCOUNTS|{'history','paired-supervisor','local-archive','backups'}
    buckets={};count=0;stack=[Path(root)]
    while stack:
        directory=stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info=entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                    raise ValueError('linked storage inventory member')
                path=Path(entry.path)
                if stat.S_ISDIR(info.st_mode):stack.append(path);continue
                if not stat.S_ISREG(info.st_mode):raise ValueError('nonregular storage inventory member')
                count+=1
                if count>100000:raise ValueError('storage inventory exceeds bound')
                parts=path.relative_to(root).parts
                if len(parts)>1:bucket=parts[0] if parts[0] in names else 'other_directories'
                elif path.suffix in {'.jsonl','.log'}:bucket='root_logs'
                elif path.suffix in {'.db','.sqlite'}:bucket='root_databases'
                else:bucket='root_other'
                row=buckets.setdefault(bucket,{'bytes':0,'files':0})
                row['bytes']+=info.st_size;row['files']+=1
    return {'kind':'paper_storage_inventory','buckets':buckets}


def runtime_fingerprint(package):
    package = Path(package)
    hashes = {path.relative_to(package).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(package.rglob('*.py'))}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def read_health(root, *, now=None):
    """Project operational fields only; never return account objects or errors' text."""
    report = dict(kind='paper_service_health', read_status='unavailable',
                  heartbeat_fresh=False, heartbeat_age_seconds=None,
                  worker_status='unknown', mode='unknown', execution='unknown',
                  accounts_observed=None, model_ready=None, entries_blocked=None,
                  entry_block_reasons=[], batch_committed=None,
                  storage_status='unknown', storage_stop=None,
                  storage_state_bytes=None, storage_free_bytes=None,
                  storage_max_state_bytes=None, storage_reserve_bytes=None)
    try:
        path = Path(root) / 'heartbeat.json'
        if path.is_symlink():
            return report
        with path.open('rb') as source:
            raw = source.read(MAX_HEARTBEAT_BYTES + 1)
        if len(raw) > MAX_HEARTBEAT_BYTES:
            return report
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('timestamp'), str):
            return report
        at = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        current = now or datetime.now(timezone.utc)
        if at.tzinfo is None or current.tzinfo is None:
            return report
        age = (current - at).total_seconds()
        accounts = data.get('accounts')
        if not isinstance(accounts, dict) or set(accounts) != ACCOUNTS:
            return report
        if not all(isinstance(row, dict) for row in accounts.values()):
            return report
        admission = data.get('entry_admission')
        admission = admission if isinstance(admission, dict) else {}
        reasons = admission.get('reasons', [])
        reasons = reasons if isinstance(reasons, list) else []
        lifecycle=data.get('worker_lifecycle')
        lifecycle=lifecycle if isinstance(lifecycle,dict) else {}
        storage=lifecycle.get('storage_report')
        storage=storage if isinstance(storage,dict) else {}
        limits=storage.get('limits')
        limits=limits if isinstance(limits,dict) else {}
        report['storage_status']=storage.get('status') if storage.get('status') in ('ready','blocked') else 'unknown'
        report['storage_stop']=lifecycle.get('storage_stop') if type(lifecycle.get('storage_stop')) is bool else None
        for name,source in (('state_bytes',storage),('free_bytes',storage),('max_state_bytes',limits),('reserve_bytes',limits)):
            value=source.get(name)
            report['storage_'+name]=value if type(value) is int and 0<=value<=1024**5 else None
        report.update(read_status='observed', heartbeat_fresh=0 <= age <= 300,
                      heartbeat_age_seconds=round(age, 3),
                      worker_status=data['status'] if data.get('status') in ('ok','degraded','stopped') else 'unknown',
                      mode='paper' if data.get('mode') == 'paper' else 'unknown',
                      execution='simulated' if data.get('execution') == 'simulated' else 'unknown',
                      accounts_observed=sum(row.get('status') == 'observed' for row in accounts.values()),
                      model_ready=data.get('model_ready') if type(data.get('model_ready')) is bool else None,
                      entries_blocked=admission.get('blocked') if type(admission.get('blocked')) is bool else None,
                      entry_block_reasons=[reason if isinstance(reason, str) and reason in REASONS else 'other'
                                           for reason in reasons[:16]],
                      batch_committed=bool(data.get('batch_id')))
        return report
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return report


def _emit(stream, record):
    print(json.dumps(record, sort_keys=True), file=stream, flush=True)


async def serve(root, *, runner=None, stream=None, health_interval=60):
    """Observe alongside the existing worker; leave its state and safeguards intact."""
    if not 10 <= health_interval <= 300:
        raise ValueError('health interval must be between 10 and 300 seconds')
    stream = stream if stream is not None else sys.stdout
    if runner is None:
        from hermes_trading import paper_boot
        runner = paper_boot.run_paper
        try:
            digest = runtime_fingerprint(Path(paper_boot.__file__).parent)
        except OSError:
            digest = None
        _emit(stream, {'kind':'paper_service_source', 'runtime_sha256':digest})

    async def observe():
        while True:
            try:
                _emit(stream, read_health(root))
            except (OSError, ValueError):
                # Telemetry is not a reason to interrupt protective handling.
                # Retry on the next interval, without logging exception text.
                try:
                    _emit(sys.stderr, {'kind':'paper_health_output_unavailable'})
                except (OSError, ValueError):
                    pass
            await asyncio.sleep(health_interval)

    task = asyncio.create_task(observe(), name='paper-service-health')
    try:
        try:_emit(stream,storage_inventory(Path(root)))
        except (OSError,ValueError):_emit(stream,{'kind':'paper_storage_inventory','status':'unavailable'})
        result = await runner(Path(root))
        _emit(stream, {'kind':'paper_worker_stopped'})
        return result
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/app/state'))
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.root))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        _emit(sys.stderr, {'kind':'paper_worker_failed', 'error_type':type(error).__name__})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
