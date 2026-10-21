"""Daily consistent paper-state copies, without archival retirement or replay.

The worker calls this at a completed cycle boundary with its retained owner.
Keep a small number of verified owned daily copies outside the active volume;
an authenticated operator/supervisor can then copy them off the worker host.
"""
from datetime import timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import time

from hermes_trading.archive_snapshot import _safe_root, snapshot
from hermes_trading.paper_publication import _sync_dir, _temporary
from hermes_trading.reflection_supervision import _json, _now

RECEIPT = 'paper_backup_receipt.json'
PURPOSE = 'paper_daily_state_backup_v1'


def _metadata(path, limit, check):
    """Refuse oversized/nonregular metadata before opening or loading it."""
    check()
    path = _safe_root(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError('paper backup metadata is nonregular or exceeds bound')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('paper backup metadata changed while opening')
        raw = stream.read(limit+1)
    check()
    if len(raw) > limit:
        raise ValueError('paper backup metadata exceeds bound')
    return raw


def _hash_file(path, check):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while raw := stream.read(1024*1024):
            check()
            digest.update(raw)
    check()
    return digest.hexdigest()


def _verified(path, identity, check, max_bytes, *, expected_name=None):
    path = _safe_root(path)
    raw = _metadata(path/RECEIPT, 16384, check)
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or receipt.get('purpose') != PURPOSE or
            type(receipt.get('schema_version')) is not int or receipt['schema_version'] != 1 or
            receipt.get('source_identity') != identity or receipt.get('name') != (expected_name or path.name)):
        raise ValueError('paper backup ownership differs')
    inventory_raw = _metadata(path/'snapshot_inventory.json', 4*1024**2, check)
    if hashlib.sha256(inventory_raw).hexdigest() != receipt.get('sha256'):
        raise ValueError('paper backup inventory changed')
    inventory = json.loads(inventory_raw)
    if (not isinstance(inventory, dict) or inventory.get('schema_version') != 1 or
            inventory.get('purpose') != 'paired_consistent_snapshot_v1' or
            not isinstance(inventory.get('files'), list) or len(inventory['files']) > 4096):
        raise ValueError('paper backup member count exceeds bound')
    expected = {'snapshot_inventory.json', RECEIPT}
    total = len(raw)+len(inventory_raw)
    if total > max_bytes:
        raise ValueError('paper backup metadata exceeds byte budget')
    for item in inventory['files']:
        check()
        if not isinstance(item, dict):
            raise ValueError('paper backup member record differs')
        name = item.get('path')
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or name in expected:
            raise ValueError('paper backup member path differs')
        expected.add(name)
        member = _safe_root(path/name)
        if not member.is_relative_to(path) or not member.is_file():
            raise ValueError('paper backup original member missing')
        total += member.stat().st_size
        if total > max_bytes or member.stat().st_size != item['bytes'] or _hash_file(member, check) != item['sha256']:
            raise ValueError('paper backup original member changed or exceeds bound')
    actual = set()
    for member in path.rglob('*'):
        check()
        _safe_root(member)
        if member.is_file():
            actual.add(member.relative_to(path).as_posix())
    if actual != expected:
        raise ValueError('paper backup has unexpected or missing files')
    return receipt


def create_daily_backup(root, destination, *, enabled=False, owner_db=None, now=None,
                        max_bytes=500_000_000, timeout_seconds=30, keep=2, clock=time.monotonic):
    """Capture once per UTC day, verify, then prune only owned generations.

    Copies are SQLite-consistent under the original worker lease. No source
    evidence is removed; this does not assert off-host copy or recovery proof.
    Caller admission must reserve active state plus simultaneous snapshot space.
    """
    if enabled is not True:
        raise ValueError('paper daily backup is disabled')
    if (type(max_bytes) is not int or not 4096 <= max_bytes <= 1024**4 or
            type(keep) is not int or not 1 <= keep <= 7 or
            type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or
            not 0 < timeout_seconds <= 30):
        raise ValueError('paper backup finite bounds differ')
    root = _safe_root(root)
    destination = _safe_root(destination)
    if root == destination or root in destination.parents or destination in root.parents:
        raise ValueError('paper backup must be outside active state')
    deadline = clock()+timeout_seconds
    def check():
        if clock() >= deadline:
            raise TimeoutError('paper backup deadline exceeded')
    check()
    at = _now(now).astimezone(timezone.utc)
    manifest_path = _safe_root(root/'manifest.json')
    manifest = _metadata(manifest_path, 4*1024**2, check) if manifest_path.exists() else b''
    identity = hashlib.sha256(root.as_posix().encode()+b'\0'+manifest).hexdigest()
    name = 'state-'+at.date().isoformat()+'-'+identity[:12]
    path = _safe_root(destination/name)
    existed = path.exists()
    if existed:
        receipt = _verified(path, identity, check, max_bytes)
    else:
        destination.mkdir(parents=True, exist_ok=True)
        receipt = None
        def complete_stage(stage, inventory_sha256, stage_check):
            nonlocal receipt
            check()
            proposed = dict(schema_version=1, purpose=PURPOSE, source_identity=identity,
                name=name, captured_at=at.isoformat(), sha256=inventory_sha256)
            target = stage/RECEIPT
            temporary = _temporary(target, _json(proposed).encode())
            try:
                temporary.replace(target)
                _sync_dir(stage)
            finally:
                temporary.unlink(missing_ok=True)
            receipt = _verified(stage, identity, stage_check, max_bytes, expected_name=name)
            check()
        snapshot(root, path, enabled=True, max_bytes=max_bytes, timeout_seconds=deadline-clock(),
                 owner_db=owner_db, clock=clock, validate=complete_stage)
        if receipt is None:
            raise ValueError('paper backup staging verification was not completed')
    # A lost reply may leave a complete new copy before retirement starts.
    # Both paths finish that same owned-generation work without recopying state.
    owned = []
    for member in destination.glob('state-*-'+identity[:12]):
        check()
        owned.append(member)
        if len(owned) > 4096:
            raise ValueError('paper backup generation count exceeds bound')
    owned.sort()
    for old in owned[:-keep]:
        check()
        checked = _safe_root(old)
        if checked.parent != destination or checked == path:
            raise ValueError('paper backup retirement escaped destination')
        _verified(checked, identity, check, max_bytes)
        # The exact absolute target and its original owned contents were proved
        # above. Never enumerate a path in one shell and delete it in another.
        shutil.rmtree(checked)
        _sync_dir(destination)
    return dict(receipt, status='already_completed' if existed else 'completed',
                path=str(path), off_host_verified=False)
