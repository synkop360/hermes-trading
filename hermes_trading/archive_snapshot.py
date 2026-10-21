"""Bounded, writer-locked SQLite-consistent export. Disabled by default.

This builds a new local snapshot only. It does not transfer, compact, delete or
certify independent durability. A running worker must explicitly yield its
owned runtime connection, or the exporter must acquire that ownership itself.
"""
from contextlib import ExitStack, closing, contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
import uuid

from hermes_trading.paper_publication import _sync_dir
from hermes_trading.reflection_supervision import _json

CHUNK_BYTES = 1024 * 1024
MAX_FILES = 4096
INVENTORY = 'snapshot_inventory.json'
OWNERS = {'worker': 'worker_runtime.sqlite', 'supervisor': 'watcher_cycle.sqlite'}


def _owner_path(root, role):
    if not isinstance(role, str) or role not in OWNERS:
        raise ValueError('snapshot ownership role is invalid')
    path = _safe_root(root) / OWNERS[role]
    path = _safe_root(path)
    if not path.is_file():
        raise ValueError('snapshot requires an existing ownership database')
    return path


@contextmanager
def ownership(root, *, role, enabled=False):
    """Hold the real worker/cycle lease across an entire coordinated capture.

    A supervisor coordinator must retain this lease through worker export and
    PC capture. Capturing a review database alone cannot freeze a watcher cycle.
    No missing runtime database is created and no watcher/model is started.
    """
    if enabled is not True:
        raise ValueError('archive snapshot ownership is disabled')
    path = _owner_path(root, role)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True,
            timeout=0, isolation_level=None)) as db:
        db.execute('PRAGMA locking_mode=EXCLUSIVE')
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN EXCLUSIVE'); db.commit()
        yield db


def _safe_root(path):
    if '..' in Path(path).parts:
        raise ValueError('snapshot path traversal')
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        _regular_or_directory(info)
    return path


def _regular_or_directory(info):
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400 or
            not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
        raise ValueError('linked or nonregular snapshot evidence')


def _inventory(root, check):
    files = {}; stack = [root]
    while stack:
        check()
        directory = stack.pop()
        for path in directory.iterdir():
            check(); info = path.lstat(); _regular_or_directory(info)
            # Portable object names; reject rather than normalize collisions.
            if re.fullmatch('[A-Za-z0-9_.-]+', path.name) is None or path.name.endswith('.'):
                raise ValueError('nonportable snapshot evidence name')
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
                continue
            name = path.relative_to(root).as_posix()
            files[name] = (info.st_size, info.st_mtime_ns)
            if len(files) > MAX_FILES:
                raise ValueError('snapshot file budget exceeded')
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError('snapshot evidence names collide')
    if INVENTORY in files:
        raise ValueError('snapshot inventory name is reserved')
    sidecars = {name for name in files if name.endswith(('-wal', '-shm', '-journal'))}
    for name in sidecars:
        base = name.rsplit('-', 1)[0]
        if base not in files or not base.endswith(('.db', '.sqlite')):
            raise ValueError('orphan snapshot SQLite sidecar')
    return {name: info for name, info in files.items() if name not in sidecars}


def _hash_file(path, check):
    digest = hashlib.sha256(); size = 0
    with path.open('rb') as stream:
        while raw := stream.read(CHUNK_BYTES):
            check(); digest.update(raw); size += len(raw)
    return size, digest.hexdigest()


def snapshot(root, target, *, enabled=False, max_bytes, timeout_seconds,
             clock=time.monotonic, worker_db=None, owner_db=None, role='worker', fault=None, validate=None):
    """Publish only a complete verified export within finite byte/time bounds.

    All cooperating SQLite writers are excluded across the same capture. WAL
    databases use SQLite's backup API through a read connection while a reserved
    writer lock is held separately. No raw WAL or SHM file is copied.
    """
    if enabled is not True:
        raise ValueError('archive snapshot is disabled')
    if type(max_bytes) is not int or not 0 < max_bytes <= 1024**4:
        raise ValueError('snapshot byte budget is invalid')
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
        raise ValueError('snapshot deadline must be positive and at most thirty seconds')
    root = _safe_root(root); target = _safe_root(target)
    if root == target or root in target.parents or target in root.parents:
        raise ValueError('snapshot requires separate source and target')
    if target.exists():
        raise ValueError('snapshot requires a new target')
    started = clock(); deadline = started + timeout_seconds
    def check():
        if clock() >= deadline:
            raise TimeoutError('snapshot deadline exceeded')
    check()
    worker_path = _owner_path(root, role)
    if worker_db is not None:
        if owner_db is not None or role != 'worker':
            raise ValueError('snapshot ownership connection is ambiguous')
        owner_db = worker_db  # Preserve existing worker-call compatibility.
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name('.s-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        with ExitStack() as leases:
            if owner_db is None:
                owner = leases.enter_context(ownership(root, role=role, enabled=True))
            else:
                owner = owner_db
                names = owner.execute('PRAGMA database_list').fetchall()
                if (not names or Path(names[0][2]).resolve() != worker_path.resolve() or
                        owner.execute('PRAGMA locking_mode').fetchone()[0].lower() != 'exclusive' or owner.in_transaction):
                    raise ValueError('snapshot ownership differs')
                owner.execute('BEGIN EXCLUSIVE'); owner.commit()
            quote_cohort=role=='worker' and (root/'manifest.json').is_file()
            if quote_cohort:
                from hermes_trading.archive_quote_state import assert_quote_cohort
                assert_quote_cohort(root,check=check)
            inventory = _inventory(root, check)
            databases = {}; plain = {}
            for name in sorted(inventory):
                check()
                if name == OWNERS[role]:
                    databases[name] = owner
                elif name.endswith(('.sqlite', '.db')):
                    guard = leases.enter_context(closing(sqlite3.connect(
                        (root / name).resolve().as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=0)))
                    # Prevent all account/review/publication writers before any copy.
                    guard.execute('BEGIN IMMEDIATE')
                    reader = leases.enter_context(closing(sqlite3.connect(
                        (root / name).resolve().as_uri() + '?mode=ro', uri=True, timeout=0)))
                    databases[name] = reader
                else:
                    plain[name] = inventory[name]
            copied = 0; records = []
            for name in sorted(inventory):
                check(); destination = stage / name; destination.parent.mkdir(parents=True, exist_ok=True)
                if name in databases:
                    source = databases[name]
                    pages = source.execute('PRAGMA page_count').fetchone()[0]
                    page_bytes = source.execute('PRAGMA page_size').fetchone()[0]
                    if copied + pages * page_bytes > max_bytes:
                        raise ValueError('snapshot byte budget exceeded')
                    with closing(sqlite3.connect(destination)) as backup:
                        source.backup(backup, pages=16, sleep=0.01, progress=lambda *_: check())
                        backup.set_progress_handler(lambda: int(clock() >= deadline), 1000)
                        if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('snapshot SQLite verification failed')
                else:
                    if copied + inventory[name][0] > max_bytes:
                        raise ValueError('snapshot byte budget exceeded')
                    with (root / name).open('rb') as source, destination.open('xb') as output:
                        count = 0
                        while raw := source.read(CHUNK_BYTES):
                            check(); count += len(raw)
                            if copied + count > max_bytes:
                                raise ValueError('snapshot byte budget exceeded')
                            output.write(raw)
                        output.flush(); os.fsync(output.fileno())
                size, digest = _hash_file(destination, check); copied += size
                if copied > max_bytes:
                    raise ValueError('snapshot byte budget exceeded')
                # Windows _commit/fsync requires a descriptor opened for write.
                with destination.open('r+b') as stream:
                    os.fsync(stream.fileno())
                records.append({'path': name, 'bytes': size, 'sha256': digest})
            if fault:
                fault('before_source_recheck')
            current = _inventory(root, check)
            if set(current) != set(inventory):
                raise ValueError('snapshot source inventory changed')
            for name, original in plain.items():
                record = next(row for row in records if row['path'] == name)
                if current[name] != original or _hash_file(root / name, check) != (record['bytes'], record['sha256']):
                    raise ValueError('snapshot source evidence changed')
            manifest = {'schema_version': 1, 'purpose': 'paired_consistent_snapshot_v1', 'files': records}
            raw = (_json(manifest) + '\n').encode()
            if len(raw) > 4 * CHUNK_BYTES or copied + len(raw) > max_bytes:
                raise ValueError('snapshot manifest byte budget exceeded')
            with (stage / INVENTORY).open('xb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            for directory in sorted((p for p in stage.rglob('*') if p.is_dir()), key=lambda p:len(p.parts), reverse=True):
                _sync_dir(directory)
            _sync_dir(stage)
            if fault:
                fault('before_publish')
            if validate is not None:
                validate(stage,hashlib.sha256(raw).hexdigest(),check)
            check()
            if target.exists():
                raise ValueError('snapshot target appeared during export')
            if quote_cohort:assert_quote_cohort(root,check=check)
            stage.rename(target); _sync_dir(target.parent)
        return {'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'files': len(records),
            'ownership_role': role,
            'snapshot_bytes': copied + len(raw), 'elapsed_seconds': clock() - started,
            'independent_backup_verified': False, 'retirement_authorized': False}
    except BaseException:
        # Only this invocation's newly allocated staging directory is removed.
        # Source and any already published snapshot are never cleanup targets.
        if stage.exists() and stage.parent == target.parent and stage.name.startswith('.s-'):
            checked = _safe_root(stage).resolve()
            if checked != stage or checked.parent != target.parent.resolve():
                raise ValueError('snapshot cleanup target escaped its staging parent')
            shutil.rmtree(checked)
        raise
