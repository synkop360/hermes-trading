"""Disabled, admitted V2 replay evidence in a dedicated rollback database.

The caller retains the expected generation independently. This detects resets,
not restoration of an older same-generation copy. No listener is installed and
the proposed footprint is not an operating-system quota or horizon proof.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading

from hermes_trading.archive_capacity import CapacityLease, StorageOwner, _safe_info
from hermes_trading.archive_protective_witness import _binding, _identity
from hermes_trading.archive_store import _root

PAGE_BYTES = 4096
MAX_PAGES = 256
MAX_ROWS = 256
NONCE_PEAK_BYTES = 4 * 1024 * 1024
PURPOSE = 'paired_archive_nonce_v2'
APPLICATION_ID = 0x484E4332
SCHEMA = (
    'CREATE TABLE nonce_meta(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)',
    'CREATE TABLE nonce_entries(nonce TEXT PRIMARY KEY,accepted_us INTEGER NOT NULL,expires_us INTEGER NOT NULL) WITHOUT ROWID',
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_US = 253402300799999999


class NonceIntegrityError(ValueError):
    """Original nonce evidence/configuration cannot be established."""


class NonceCapacityError(RuntimeError):
    """The bounded store cannot retain another live nonce."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _token(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise ValueError('nonce/generation must be 32 lowercase hexadecimal characters')
    return value


def _micros(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('nonce times must be aware datetimes')
    delta = value.astimezone(timezone.utc) - _EPOCH
    result = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    if not 0 <= result <= _MAX_US:
        raise ValueError('nonce time outside supported UTC range')
    return result


class BoundedArchiveNonceStore:
    def __init__(self, path, *, owner, expected_store_id, binding, identity,
                 max_rows=MAX_ROWS, max_pages=MAX_PAGES, enabled=False):
        if enabled is not True:
            raise ValueError('bounded archive nonce storage is disabled')
        if not isinstance(owner, StorageOwner):
            raise ValueError('nonce storage requires a retained storage owner')
        _token(expected_store_id)
        _binding(binding)
        _identity(identity)
        if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
            raise ValueError('nonce row bound differs')
        if type(max_pages) is not int or not 3 <= max_pages <= MAX_PAGES:
            raise ValueError('nonce page bound differs')
        self._path = _root(path)
        self._owner = owner
        self._process_thread = os.getpid(), threading.get_ident()
        self._core = dict(schema_version=2, purpose=PURPOSE, store_id=expected_store_id,
                          binding_sha256=_digest(binding), identity_sha256=_digest(identity),
                          page_bytes=PAGE_BYTES, max_pages=max_pages, max_rows=max_rows)
        self._arm = None
        self._capture = None
        self._invalid = False
        self._floor = 0
        self.fault = None
        info = _safe_info(self._path)
        self._file_identity = info.st_dev, info.st_ino
        self._inspect()

    @property
    def owner(self):
        return self._owner

    @property
    def path(self):
        return self._path

    @property
    def policy(self):
        self._thread();return deepcopy(self._core)

    def require_scope(self, binding, identity):
        self._thread()
        _binding(binding)
        _identity(identity)
        if (_digest(binding) != self._core['binding_sha256'] or
                _digest(identity) != self._core['identity_sha256']):
            raise NonceIntegrityError('nonce original receiver scope differs')

    def _thread(self):
        if self._process_thread != (os.getpid(), threading.get_ident()):
            raise ValueError('nonce store requires its original process/thread')

    def _inspect(self):
        if self._invalid:
            raise NonceIntegrityError('nonce source changed; explicit recovery required')
        try:
            info = _safe_info(self.path)
            if (info.st_dev, info.st_ino) != self._file_identity:
                raise NonceIntegrityError('original nonce database was replaced')
            if not 100 <= info.st_size <= self._core['max_pages'] * PAGE_BYTES:
                raise NonceIntegrityError('nonce database size differs')
            with self.path.open('rb') as stream:
                header = stream.read(100)
                opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != self._file_identity:
                raise NonceIntegrityError('opened nonce database identity differs')
            if (header[:16] != b'SQLite format 3\x00' or
                    int.from_bytes(header[16:18], 'big') != PAGE_BYTES or
                    header[18:20] != b'\x01\x01'):
                raise NonceIntegrityError('nonce database requires original rollback format')
            total = info.st_size
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = Path(str(self.path) + suffix)
                try:
                    sideinfo = _safe_info(sidecar)
                except FileNotFoundError:
                    continue
                if suffix != '-journal':
                    raise NonceIntegrityError('unexpected nonce WAL/SHM storage')
                if sideinfo.st_size > self._core['max_pages'] * (PAGE_BYTES + 8) + 65536:
                    raise NonceCapacityError('nonce rollback journal exceeds its bound')
                total += sideinfo.st_size
            if total > NONCE_PEAK_BYTES:
                raise NonceCapacityError('nonce database extents exceed the proposed envelope')
            return total
        except (OSError, ValueError) as error:
            if isinstance(error, NonceIntegrityError):
                raise
            raise NonceIntegrityError('original nonce file evidence unavailable') from error

    def _lease(self, lease):
        self._thread()
        if not isinstance(lease, CapacityLease) or lease.owner is not self.owner:
            raise ValueError('nonce lease must belong to its original storage owner')
        lease.require_path(self.path)
        lease.check()

    @contextmanager
    def armed(self, lease, *, deadline):
        self._lease(lease)
        now = self.owner.domain.clock()
        if (type(deadline) not in (int, float) or not math.isfinite(deadline) or
                not math.isfinite(now) or not now < deadline <= now + 10):
            raise ValueError('nonce request deadline must be finite and at most ten seconds')
        if self._arm is not None or self._capture is not None:
            raise ValueError('nonce arming cannot nest')
        lease.check(remaining_peak_bytes=NONCE_PEAK_BYTES)
        self._arm = dict(lease=lease, started=now, deadline=deadline, used=False)
        try:
            yield self
            self._check()
        finally:
            self._arm = None

    def _check(self):
        self._thread()
        arm = self._arm
        if arm is None:
            raise ValueError('nonce write requires an armed storage admission')
        self._lease(arm['lease'])
        now = self.owner.domain.clock()
        if not math.isfinite(now) or not arm['started'] <= now < arm['deadline']:
            raise TimeoutError('nonce original request deadline/clock unavailable')
        self._inspect()

    def _connect(self):
        # mode=rw never manufactures missing replay evidence. Recovery also
        # belongs inside the already armed admission, before receiver SQL.
        self._check()
        return sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                               timeout=0, isolation_level=None)

    def _configure(self, db, check=None):
        check = check or self._check
        check()
        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
            raise NonceIntegrityError('nonce journal configuration differs')
        if db.execute('PRAGMA page_size').fetchone()[0] != PAGE_BYTES:
            raise NonceIntegrityError('nonce page size differs')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA cache_spill=OFF')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA busy_timeout=0')
        db.execute('PRAGMA cache_size=256')
        result = db.execute('PRAGMA max_page_count=' + str(self._core['max_pages'])).fetchone()[0]
        if result != self._core['max_pages']:
            raise NonceCapacityError('nonce existing page count exceeds configured cap')
        check()

    def _validate(self, db, *, expected_path=None):
        if db.execute('PRAGMA database_list').fetchall() != [(0, 'main', str(expected_path or self.path))]:
            raise NonceIntegrityError('nonce attached database inventory differs')
        objects = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master LIMIT 4').fetchall()
        expected = {('table', 'nonce_meta', 'nonce_meta', SCHEMA[0]),
                    ('table', 'nonce_entries', 'nonce_entries', SCHEMA[1])}
        if len(objects) != 2 or set(objects) != expected:
            raise NonceIntegrityError('nonce schema inventory differs')
        if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or
                db.execute('PRAGMA user_version').fetchone()[0] != 2):
            raise NonceIntegrityError('nonce application identity differs')
        records = db.execute('SELECT id,payload FROM nonce_meta LIMIT 2').fetchall()
        if len(records) != 1 or records[0][0] != 1 or type(records[0][1]) is not str or len(records[0][1].encode()) > 2048:
            raise NonceIntegrityError('nonce metadata row differs')
        try:
            meta = json.loads(records[0][1])
            floor = meta['accepted_floor_us']
            if (type(meta) is not dict or set(meta) != set(self._core) | {'accepted_floor_us'} or
                    any(type(meta[k]) is not type(v) or meta[k] != v for k, v in self._core.items()) or
                    type(floor) is not int or not self._floor <= floor <= _MAX_US or
                    records[0][1] != _json(meta)):
                raise ValueError('nonce metadata binding/floor differs')
        except (ValueError, TypeError, KeyError) as error:
            raise NonceIntegrityError('nonce metadata binding/floor differs') from error
        rows = db.execute('SELECT nonce,accepted_us,expires_us FROM nonce_entries LIMIT ' + str(MAX_ROWS + 1)).fetchall()
        if len(rows) > self._core['max_rows']:
            raise NonceIntegrityError('nonce live-row bound differs')
        for nonce, accepted, expires in rows:
            try:
                _token(nonce)
            except ValueError as error:
                raise NonceIntegrityError('nonce row token differs') from error
            if (type(accepted) is not int or type(expires) is not int or
                    not 0 <= accepted <= floor or expires != accepted + 300000000 or expires > _MAX_US):
                raise NonceIntegrityError('nonce row times/floor differ')
        return meta, rows

    @staticmethod
    def _sql_error(error):
        if getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
            return NonceCapacityError('nonce SQLite page capacity exhausted')
        return NonceIntegrityError('nonce SQLite evidence/ownership unavailable')

    def _fault(self, point):
        if self.fault is not None:
            self.fault(point)

    @contextmanager
    def capture(self, lease, *, deadline):
        """Validate/copy all replay evidence without acceptance or expiry."""
        from hermes_trading.archive_capture_io import CaptureBudget
        self._lease(lease)
        budget=CaptureBudget(lease,self.owner,deadline)
        if self._arm is not None or self._capture is not None:
            raise ValueError('nonce capture cannot nest with acceptance/capture')
        lease.check(remaining_peak_bytes=NONCE_PEAK_BYTES)
        self._capture=object();db=capture=None
        def check():
            budget.check();self._thread();self._inspect()
        try:
            check()
            db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)
            self._configure(db,check)
            db.execute('PRAGMA locking_mode=EXCLUSIVE');db.execute('BEGIN IMMEDIATE')
            meta,rows=self._validate(db);check();db.commit()
            capture=NonceCapture(self,db,budget,meta,rows);self._capture=capture
            yield capture
            capture.verify_source();budget.check()
        except sqlite3.Error as error:
            budget.check()
            raise self._sql_error(error) from error
        finally:
            if capture is not None:capture._active=False
            try:
                if db is not None:
                    try:
                        if db.in_transaction:db.rollback()
                    finally:db.close()
            finally:self._capture=None

    def add_once(self, nonce, *, accepted_at, expires_at):
        self._thread()
        self._check()
        if self._arm['used']:
            raise ValueError('nonce arming permits only one replay operation')
        self._arm['used'] = True
        _token(nonce)
        accepted, expires = _micros(accepted_at), _micros(expires_at)
        if expires != accepted + 300000000:
            raise ValueError('nonce expiry must be exactly 300 seconds after acceptance')
        db = None
        try:
            db = self._connect()
            self._configure(db)
            db.execute('BEGIN IMMEDIATE')
            meta, rows = self._validate(db)
            if accepted < meta['accepted_floor_us']:
                raise ValueError('nonce acceptance clock regressed')
            if any(row[0] == nonce and row[2] > accepted for row in rows):
                db.rollback()
                self._floor = meta['accepted_floor_us']
                self._check()
                return False
            live = sum(row[2] > accepted for row in rows)
            if live >= self._core['max_rows']:
                raise NonceCapacityError('nonce live-row capacity exhausted')
            self._check()
            db.execute('DELETE FROM nonce_entries WHERE expires_us<=?', (accepted,))
            db.execute('INSERT OR ROLLBACK INTO nonce_entries VALUES(?,?,?)', (nonce, accepted, expires))
            meta['accepted_floor_us'] = accepted
            db.execute('UPDATE OR ROLLBACK nonce_meta SET payload=? WHERE id=1', (_json(meta),))
            if db.execute('PRAGMA page_count').fetchone()[0] > self._core['max_pages']:
                raise NonceCapacityError('nonce page capacity exhausted')
            self._fault('before_commit')
            self._check()
            db.commit()
            self._floor = accepted
            self._fault('after_commit')
            self._check()
            return True
        except sqlite3.Error as error:
            raise self._sql_error(error) from error
        finally:
            if db is not None:
                try:
                    if db.in_transaction:
                        db.rollback()
                finally:
                    db.close()

    @classmethod
    def initialize(cls, path, *, lease, owner, expected_store_id, binding, identity,
                   max_rows=MAX_ROWS, max_pages=MAX_PAGES, enabled=False):
        # Validate the same configuration before creating even an empty file.
        _token(expected_store_id)
        _binding(binding)
        _identity(identity)
        if (enabled is not True or not isinstance(owner, StorageOwner) or
                type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS or
                type(max_pages) is not int or not 3 <= max_pages <= MAX_PAGES or
                not isinstance(lease, CapacityLease) or lease.owner is not owner):
            raise ValueError('explicit nonce initialization requires original admitted configuration')
        path = lease.require_path(_root(path))
        lease.check(remaining_peak_bytes=NONCE_PEAK_BYTES)
        for suffix in ('-wal', '-shm', '-journal'):
            if os.path.lexists(str(path) + suffix):
                raise NonceIntegrityError('new nonce target has existing sidecar evidence')
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            created = os.fstat(descriptor)
            created_identity = created.st_dev, created.st_ino
        finally:
            os.close(descriptor)
        def original_file():
            lease.check()
            current = _safe_info(path)
            if (current.st_dev, current.st_ino) != created_identity:
                raise NonceIntegrityError('new nonce initialization file was replaced')
        db = None
        core = dict(schema_version=2, purpose=PURPOSE, store_id=expected_store_id,
                    binding_sha256=_digest(binding), identity_sha256=_digest(identity),
                    page_bytes=PAGE_BYTES, max_pages=max_pages, max_rows=max_rows)
        try:
            original_file()
            db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0, isolation_level=None)
            original_file()
            db.execute('PRAGMA page_size=4096')
            if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
                raise NonceIntegrityError('nonce rollback journal unavailable')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA cache_spill=OFF')
            db.execute('PRAGMA temp_store=MEMORY')
            db.execute('PRAGMA max_page_count=' + str(max_pages))
            db.execute('BEGIN IMMEDIATE')
            for sql in SCHEMA:
                db.execute(sql)
            db.execute('PRAGMA application_id=' + str(APPLICATION_ID))
            db.execute('PRAGMA user_version=2')
            db.execute('INSERT OR ROLLBACK INTO nonce_meta VALUES(1,?)', (_json(dict(core, accepted_floor_us=0)),))
            original_file()
            db.commit()
            original_file()
        except sqlite3.Error as error:
            raise cls._sql_error(error) from error
        finally:
            if db is not None:
                try:
                    if db.in_transaction:
                        db.rollback()
                finally:
                    db.close()
        # An incomplete new file is retained on initialization failure. The
        # caller must reconcile its retained generation, never reset it.
        result = cls(path, owner=owner, expected_store_id=expected_store_id,
                     binding=binding, identity=identity, max_rows=max_rows,
                     max_pages=max_pages, enabled=enabled)
        result._inspect()
        lease.check()
        return dict(core)


class NonceCapture:
    """Only valid inside the originating admitted capture context."""
    def __init__(self,store,db,budget,meta,rows):
        self._store=store;self._db=db;self._budget=budget;self._active=True
        self._evidence=self._describe(meta,rows)
        info=_safe_info(store.path)
        self._stamp=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

    @staticmethod
    def _describe(meta,rows):
        return dict(metadata=deepcopy(meta),row_count=len(rows),
                    rows_sha256=_digest(sorted(rows,key=lambda row:row[0])),
                    schema_sha256=_digest(SCHEMA))

    def _check(self):
        if not self._active or self._store._capture is not self:
            raise ValueError('nonce capture requires its original active context')
        self._store._thread();self._budget.progress()
        try:
            info=_safe_info(self._store.path)
            if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)!=self._stamp:
                raise ValueError('nonce original physical source stamp changed')
            self._store._inspect()
        except (OSError,ValueError,NonceCapacityError) as error:
            from hermes_trading.archive_capture_io import CaptureSourceChanged
            self._store._invalid=True
            raise CaptureSourceChanged('nonce original source changed during capture; explicit recovery required') from error

    @property
    def evidence(self):
        self._check();return deepcopy(self._evidence)

    def verify_source(self):
        self._check();meta,rows=self._store._validate(self._db)
        if self._describe(meta,rows)!=self._evidence:
            raise NonceIntegrityError('nonce original capture cut changed')
        self._check();return self.evidence

    def backup_to(self,target):
        from hermes_trading.archive_capture_io import backup_database
        self.verify_source()
        def validate(db,path):
            meta,rows=self._store._validate(db,expected_path=path)
            result=self._describe(meta,rows)
            if result!=self._evidence:raise NonceIntegrityError('nonce captured copy differs')
            return result
        result=backup_database(self._db,target,self._budget,max_bytes=self._store._core['max_pages']*PAGE_BYTES,
                               validate=validate,check_source=self._check)
        self.verify_source();return result
