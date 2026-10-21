"""Disabled cooperative whole-domain peak admission under a retained owner.

No filesystem quota, stage creation, receiver mutation or activation is provided.
Installation must register all state/stages and make every writer participate;
each writer must enforce the additional peak it requests here.
"""
from contextlib import contextmanager
from copy import deepcopy
import errno
import json
import math
import os
from pathlib import Path
import shutil
import stat
import threading
import time

from hermes_trading.archive_store import _root

OWNER_NAME = '.archive-capacity-v1.json'
MAX_RECORD_BYTES = 16 * 1024
MAX_BYTES = 1024**4
BOOTSTRAP_PEAK_BYTES = 64 * 1024


class CapacityUnavailable(RuntimeError):
    """Admission cannot establish its required bounded evidence/ownership."""


class CapacityBusy(CapacityUnavailable):
    pass


class CapacityExceeded(CapacityUnavailable):
    pass


def _integer(value, *, maximum=MAX_BYTES, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= maximum:
        raise ValueError('capacity requires finite integer bounds')
    return value


def _safe_info(path, *, directory=False):
    path = _root(path)
    value = path.lstat()
    if not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)):
        raise ValueError('capacity inventory requires ordinary directories/files')
    if not directory and value.st_nlink != 1:
        raise ValueError('hard-linked files are outside capacity ownership')
    if not value.st_ino:
        raise ValueError('capacity filesystem identity is unavailable')
    return value


def _identity(value):
    return value.st_dev, value.st_ino


class _Deadline:
    def __init__(self, seconds, clock, *, ceiling_seconds=10):
        _integer(ceiling_seconds, maximum=120)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= ceiling_seconds:
            raise ValueError('capacity admission deadline exceeds its finite bound')
        self.clock = clock
        self.started = clock()
        if not math.isfinite(self.started):
            raise CapacityUnavailable('capacity admission clock unavailable')
        self.deadline = self.started + seconds
        self.check()

    def check(self):
        current = self.clock()
        if not math.isfinite(current) or current < self.started or current >= self.deadline:
            raise CapacityUnavailable('capacity admission deadline/clock unavailable')


def _take_lock(handle):
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise CapacityBusy('capacity domain ownership busy') from error
        raise


def _drop_lock(handle):
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class StorageDomain:
    def __init__(self, root, *, domain_id, limit_bytes, reserve_bytes, extra_roots=(),
                 max_entries=100000, enabled=False, clock=time.monotonic):
        _integer(limit_bytes)
        _integer(reserve_bytes)
        _integer(max_entries, maximum=100000)
        if reserve_bytes >= limit_bytes:
            raise ValueError('capacity reserve must remain within the domain limit')
        if not isinstance(domain_id, str) or not 1 <= len(domain_id) <= 128:
            raise ValueError('capacity domain identity differs')
        if not isinstance(extra_roots, (tuple, list)) or len(extra_roots) > 31:
            raise ValueError('capacity root inventory exceeds its bound')
        self._anchor_root = _root(root)
        self._roots = (self.root, *(_root(value) for value in extra_roots))
        self._root_identities = {}
        for index, path in enumerate(self.roots):
            if any(path.is_relative_to(other) or other.is_relative_to(path)
                   for other in self.roots[:index]):
                raise ValueError('capacity counted roots overlap')
            self._root_identities[path] = _identity(_safe_info(path, directory=True))
        if len({value[0] for value in self._root_identities.values()}) != 1:
            raise ValueError('capacity roots must belong to one filesystem domain')
        self._limit_bytes = limit_bytes
        self._reserve_bytes = reserve_bytes
        self._max_entries = max_entries
        self.enabled = enabled
        self.clock = clock
        self._gate = threading.Lock()
        self._policy = dict(schema_version=1, purpose='paired_archive_capacity_v1',
                            domain_id=domain_id, anchor_root=str(self.root),
                            roots=sorted(map(str, self.roots)), limit_bytes=limit_bytes,
                            reserve_bytes=reserve_bytes, max_entries=max_entries)
        # The original first byte is the cross-process lock range on Windows.
        self._raw = b'!' + json.dumps(self._policy, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode() + b'\n'
        if len(self._raw) > MAX_RECORD_BYTES:
            raise ValueError('capacity policy record exceeds its bound')

    @property
    def root(self):
        return self._anchor_root

    @property
    def roots(self):
        return self._roots

    @property
    def owner_path(self):
        return self.root / OWNER_NAME

    @property
    def limit_bytes(self):
        return self._limit_bytes

    @property
    def reserve_bytes(self):
        return self._reserve_bytes

    @property
    def max_entries(self):
        return self._max_entries

    def _enabled(self):
        if self.enabled is not True:
            raise ValueError('storage domain admission is disabled')

    def _verify_roots(self, deadline):
        for path, expected in self._root_identities.items():
            deadline.check()
            if _identity(_safe_info(path, directory=True)) != expected:
                raise ValueError('capacity registered root identity changed')

    def _measure(self, deadline):
        self._verify_roots(deadline)
        total = files = entries_seen = 0
        by_root = {}
        free_values = []
        for root in self.roots:
            subtotal = 0
            stack = [root]
            while stack:
                deadline.check()
                directory = stack.pop()
                before = _safe_info(directory, directory=True)
                with os.scandir(directory) as children:
                    for child in children:
                        deadline.check()
                        entries_seen += 1
                        if entries_seen > self.max_entries:
                            raise CapacityUnavailable('capacity inventory entry bound exceeded')
                        path = Path(child.path)
                        # Windows DirEntry.stat can omit st_dev/st_ino. Direct
                        # lstat supplies the original file identity on both OSes.
                        value = path.lstat()
                        is_directory = stat.S_ISDIR(value.st_mode)
                        checked = _safe_info(path, directory=is_directory)
                        if _identity(checked) != _identity(value) or checked.st_size != value.st_size:
                            raise ValueError('capacity inventory changed during inspection')
                        if checked.st_dev != self._root_identities[root][0]:
                            raise ValueError('capacity inventory crosses filesystem domains')
                        if is_directory:
                            stack.append(path)
                        else:
                            subtotal += checked.st_size
                            files += 1
                after = _safe_info(directory, directory=True)
                if (_identity(before), before.st_mtime_ns, before.st_ctime_ns) != (
                        _identity(after), after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('capacity directory changed during inspection')
            by_root[str(root)] = subtotal
            total += subtotal
            try:
                free = shutil.disk_usage(root).free
            except OSError as error:
                raise CapacityUnavailable('capacity filesystem free bytes unavailable') from error
            if type(free) is not int or free < 0:
                raise CapacityUnavailable('capacity filesystem free bytes unavailable')
            free_values.append(free)
        deadline.check()
        return dict(state_bytes=total, free_bytes=min(free_values), files=files,
                    entries=entries_seen, root_bytes=by_root)

    def _fits(self, report, additional):
        if report['state_bytes'] + additional + self.reserve_bytes > self.limit_bytes:
            raise CapacityExceeded('capacity domain additional peak exceeds protected logical limit')
        if report['free_bytes'] < additional + self.reserve_bytes:
            raise CapacityExceeded('capacity domain additional peak exceeds observed filesystem reserve')

    def initialize(self):
        """Explicit new owner record; never invoked by request admission."""
        self._enabled()
        deadline = _Deadline(10, self.clock)
        if self.owner_path.exists():
            raise FileExistsError('capacity owner already exists; do not reinitialize')
        report = self._measure(deadline)
        self._fits(report, BOOTSTRAP_PEAK_BYTES + len(self._raw))
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(self.owner_path, flags, 0o600)
        # An interrupted explicit initialization remains evidence, never reset.
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(self._raw)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != 'nt':
            directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        deadline.check()

    def _verify_owner(self, handle, expected_identity, deadline):
        deadline.check()
        self._verify_roots(deadline)
        info = _safe_info(self.owner_path)
        opened = os.fstat(handle.fileno())
        if (_identity(info) != expected_identity or _identity(opened) != expected_identity or
                info.st_size != len(self._raw) or opened.st_size != len(self._raw)):
            raise ValueError('capacity original owner record identity/size changed')
        handle.seek(0)
        if handle.read(MAX_RECORD_BYTES + 1) != self._raw:
            raise ValueError('capacity owner policy differs or is corrupt')

    @contextmanager
    def admit(self, additional_peak_bytes, *, timeout_seconds=10):
        """One short operation; long-lived prepared handles use ownership()."""
        self._enabled()
        _integer(additional_peak_bytes, zero=True)
        deadline = _Deadline(timeout_seconds, self.clock)
        with self.ownership(timeout_seconds=timeout_seconds) as owner:
            deadline.check()
            with owner.admit(additional_peak_bytes, timeout_seconds=deadline.deadline - self.clock()) as lease:
                yield lease

    @contextmanager
    def ownership(self, *, timeout_seconds=10):
        """Retain before opening prepared handles; close handles before exit.

        Acquisition is bounded. Each operation must separately admit its peak
        with a fresh finite deadline; this context grants no write allowance.
        """
        self._enabled()
        deadline = _Deadline(timeout_seconds, self.clock)
        if not self._gate.acquire(blocking=False):
            raise CapacityBusy('capacity domain ownership busy')
        handle = owner = None
        locked = False
        lock_pid = os.getpid()
        try:
            try:
                info = _safe_info(self.owner_path)
            except FileNotFoundError as error:
                raise CapacityUnavailable('capacity original owner is missing; explicit initialization required') from error
            if info.st_size > MAX_RECORD_BYTES:
                raise ValueError('capacity owner record exceeds its bound')
            flags = os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            descriptor = os.open(self.owner_path, flags)
            handle = os.fdopen(descriptor, 'r+b', buffering=0)
            _take_lock(handle)
            locked = True
            identity = _identity(info)
            self._verify_owner(handle, identity, deadline)
            owner = StorageOwner(self, handle, identity)
            yield owner
            if owner._busy:
                raise ValueError('capacity owner cannot close with an active admission')
        finally:
            if owner is not None:
                owner._active = False
            try:
                if handle is not None:
                    try:
                        if locked and lock_pid == os.getpid():
                            _drop_lock(handle)
                    finally:
                        handle.close()
            finally:
                self._gate.release()


class StorageOwner:
    """One retained original owner; serial bounded sub-admissions only."""
    def __init__(self, domain, handle, identity):
        self._domain = domain
        self._handle = handle
        self._identity = identity
        self._active = True
        self._busy = False
        self._process_thread = os.getpid(), threading.get_ident()

    @property
    def domain(self):
        return self._domain

    def _check(self, deadline):
        if not self._active or self._process_thread != (os.getpid(), threading.get_ident()):
            raise ValueError('capacity owner requires its active original process/thread')
        self.domain._verify_owner(self._handle, self._identity, deadline)

    @contextmanager
    def admit(self, additional_peak_bytes, *, timeout_seconds=10, timeout_ceiling_seconds=10):
        self.domain._enabled()
        _integer(additional_peak_bytes, zero=True)
        deadline = _Deadline(timeout_seconds, self.domain.clock, ceiling_seconds=timeout_ceiling_seconds)
        self._check(deadline)
        if self._busy:
            raise CapacityBusy('capacity owner already has an active admission')
        self._busy = True
        lease = None
        try:
            report = self.domain._measure(deadline)
            self.domain._fits(report, additional_peak_bytes)
            lease = CapacityLease(self, deadline, report, additional_peak_bytes)
            yield lease
            lease.check()
        finally:
            if lease is not None:
                lease._active = False
            self._busy = False


class CapacityLease:
    def __init__(self, owner, deadline, report, peak):
        self._capacity_owner = owner
        self._domain = owner.domain
        self._deadline = deadline
        self._initial = deepcopy(report)
        self._peak = peak
        self._active = True
        self._owner = os.getpid(), threading.get_ident()

    @property
    def owner(self):
        self._owner_check()
        return self._capacity_owner

    @property
    def report(self):
        self._owner_check()
        return deepcopy(self._initial)

    def _owner_check(self):
        if not self._active or self._owner != (os.getpid(), threading.get_ident()):
            raise ValueError('capacity lease requires its active original process/thread owner')
        self._capacity_owner._check(self._deadline)

    def require_path(self, path):
        self._owner_check()
        path = Path(path)
        if not path.is_absolute() or '..' in path.parts or not any(path.is_relative_to(root) for root in self._domain.roots):
            raise ValueError('capacity staging/write path is not registered')
        return _root(path)

    def check(self, *, remaining_peak_bytes=0):
        self._owner_check()
        _integer(remaining_peak_bytes, zero=True)
        report = self._domain._measure(self._deadline)
        if report['state_bytes'] + remaining_peak_bytes > self._initial['state_bytes'] + self._peak:
            raise CapacityExceeded('capacity operation exceeded its admitted additional peak')
        self._domain._fits(report, remaining_peak_bytes)
        return report
