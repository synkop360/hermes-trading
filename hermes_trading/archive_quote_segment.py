"""Disabled, read-only inclusion proof for original quote-control prefixes.

A later recorded cohort contains the completed controls; the earlier cohorts
referenced by those controls contain the retired quote bundles. This reader
proves only inclusion, never deletion, independent backup or recovery authority.
"""
from contextlib import closing, contextmanager, nullcontext
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile

from hermes_trading import archive_journal as journal, archive_quote_state as state, archive_store as store
from hermes_trading.archive_capacity import StorageOwner
from hermes_trading.archive_quote_retention import _readonly, _verify_archive
from hermes_trading.archive_shared_resolver import _Budget
from hermes_trading.archive_snapshot import _owner_path
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash, _json, _now

PURPOSE = 'paired_quote_control_prefix_v1'
MAX_OPERATIONS = 32
MAX_CONTROL_BYTES = 4 * 1024**2
MAX_PIN_BYTES = 65536


def _bounds(first, last):
    if (type(first) is not int or type(last) is not int or
            not 1 <= first <= last <= 2**63-1 or last-first+1 > MAX_OPERATIONS):
        raise ValueError('quote-control prefix ordinal bounds differ')


def _quiescent_database(path, budget, *, delete_mode=False):
    """Inspect bytes before SQLite can checkpoint, recover or create sidecars."""
    budget.check()
    path = store._root(path)
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            path.with_name(path.name+suffix).lstat()
        except FileNotFoundError:
            continue
        raise ValueError('quote-control input has SQLite sidecars; a quiescent cut is required')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('quote-control database is not a regular file')
        header = stream.read(100)
    budget.read_bytes += len(header)
    if budget.read_bytes > budget.limit:
        raise ValueError('quote-control database header read bound exceeded')
    if (len(header) != 100 or header[:16] != b'SQLite format 3\x00' or
            header[18:20] not in (b'\x01\x01', b'\x02\x02') or
            delete_mode and header[18:20] != b'\x01\x01'):
        raise ValueError('quote-control ownership/journal requires existing DELETE mode')
    budget.check()


@contextmanager
def _exclusive(path, *, query_only=False):
    """Take the real SQLite lease without rewriting its journal mode."""
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True,
            timeout=0, isolation_level=None)) as db:
        db.execute('PRAGMA locking_mode=EXCLUSIVE')
        db.execute('BEGIN EXCLUSIVE')
        db.commit()
        if query_only:
            db.execute('PRAGMA query_only=ON')
        yield db


def _control_footprint(root, manifest, budget, *, db=None, sealed=True, account_controls=True):
    """Admit all control bytes before a guard can materialize their JSON.

    SQLite computes byte lengths without returning the original values. An
    absolute deadline covers scans. Archived inputs are restored, pinned and
    disposable; current inputs must first be proved quiescent under ownership.
    """
    total = 0

    def count(source, table, columns, where=''):
        nonlocal total
        source.set_progress_handler(lambda: budget.check() or 0, 1000)
        if not source.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            return
        lengths = '+'.join('COALESCE(length(CAST('+column+' AS BLOB)),0)' for column in columns)
        rows, size = source.execute('SELECT COUNT(*),COALESCE(SUM('+lengths+'+8),0) FROM '+table+where).fetchone()
        budget.check()
        if (table == 'paired_quote_retention' and rows > state.MAX_OPERATIONS or
                type(size) is not int or size < 0):
            raise ValueError('quote-control aggregate row bound differs')
        total += size
        if total > MAX_CONTROL_BYTES:
            raise ValueError('quote-control aggregate byte bound exceeded before validation')

    relative = resolve_primary(manifest).profile_directory(manifest, 'adaptive')+'/paper_publications.sqlite'
    with (closing(_readonly(root/relative)) if db is None else nullcontext(db)) as shared:
        count(shared, 'paired_quote_retention', ('operation_id', 'plan', 'progress'))
        count(shared, 'paired_quote_control_checkpoint', ('state', 'sha256'))
        count(shared, 'paired_quote_control_pending', ('plan', 'progress'))
    if not account_controls:
        return total
    for profile in manifest['profiles']:
        budget.check()
        path=root/profile['directory']/'trading.db'
        with closing(_readonly(path) if sealed else sqlite3.connect(
                path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as account:
            count(account, 'incidents', ('incident_id', 'status', 'reason', 'severity', 'revision', 'opened_at', 'resolved_at'),
                  " WHERE incident_id LIKE 'archive:quote-%'")
    return total


class _ControlBytes:
    def __init__(self, budget):
        self.budget = budget
        self.total = 0

    def rows(self, db, sql, args=(), *, limit):
        """Bound materialization as well as the aggregate control footprint."""
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CONTROL_BYTES)
        result = []
        for row in db.execute(sql, args):
            self.budget.check()
            if len(result) >= limit:
                raise ValueError('quote-control row bound exceeded')
            values = list(row)
            if any(type(value) not in (str, int, type(None)) for value in values):
                raise ValueError('quote-control original SQL types differ')
            size = len(_json(values).encode())
            self.total += size
            if self.total > MAX_CONTROL_BYTES:
                raise ValueError('quote-control aggregate byte bound exceeded')
            self.budget.decoded(size)
            result.append(values)
        return result


def _prefix(db, first, last, controls):
    rows = controls.rows(db, '''SELECT rowid,operation_id,plan,progress
        FROM paired_quote_retention WHERE rowid BETWEEN ? AND ? ORDER BY rowid''',
        (first, last), limit=MAX_OPERATIONS)
    if [row[0] for row in rows] != list(range(first, last+1)):
        raise ValueError('quote-control original contiguous prefix is missing')
    for _, identity, raw_plan, raw_progress in rows:
        plan, progress = state.unpack(raw_plan), state.unpack(raw_progress)
        if plan['operation_id'] != identity or progress['status'] != 'complete':
            raise ValueError('quote-control prefix is not complete')
    return rows


def _segment_pin(db, root, original, manifest, request, first, last, budget):
    """Logical proof for use under an existing admitted, exclusively held cut.

    This helper deliberately makes no transition and grants no compaction
    authority. A future coordinator must separately bind its committed prefix,
    retry identities, all-account transition and replay-checkpoint semantics.
    """
    _bounds(first, last)
    state.assert_quote_cohort(root, db=db, manifest=manifest, sealed=True, check=budget.check)
    selected = resolve_primary(manifest)
    relative = selected.profile_directory(manifest, 'adaptive')+'/paper_publications.sqlite'
    controls = _ControlBytes(budget)
    with closing(_readonly(original/relative)) as source:
        source.set_progress_handler(lambda: budget.check() or 0, 1000)
        prefix = _prefix(source, first, last, controls)
        if prefix != _prefix(db, first, last, controls):
            raise ValueError('quote-control current raw prefix differs from original')
    plans = [state.unpack(row[2]) for row in prefix]
    inventory = json.loads(budget.read(original/'snapshot_inventory.json', store.MAX_MANIFEST_BYTES))
    members = {row['path']: row['sha256'] for row in inventory['files']}
    accounts = {}
    for profile in sorted(manifest['profiles'], key=lambda p: (p['arm'], p['wallet_eur'])):
        budget.check()
        name = f"{profile['arm']}_{profile['wallet_eur']}"
        member = profile['directory']+'/trading.db'
        with closing(_readonly(original/member)) as source, closing(_readonly(root/member)) as current:
            source.set_progress_handler(lambda: budget.check() or 0, 1000)
            current.set_progress_handler(lambda: budget.check() or 0, 1000)
            receipts = []
            for plan in plans:
                key = state.RECEIPT+plan['operation_id']
                sql = 'SELECT rowid,* FROM incidents WHERE incident_id=?'
                archived = controls.rows(source, sql, (key,), limit=1)
                observed = controls.rows(current, sql, (key,), limit=1)
                if len(archived) != 1 or archived != observed:
                    raise ValueError('quote-control original account receipt differs')
                receipts.extend(archived)
            # Current floors may have advanced through a later valid tail.
            # Capture only B's original floors; the full current guard above
            # proves current authority rather than incorrectly comparing it.
            floors = controls.rows(source, '''SELECT rowid,* FROM incidents
                WHERE incident_id LIKE 'archive:quote-floor:%' ORDER BY rowid''',
                limit=state.MAX_ITEMS*state.MAX_OPERATIONS)
            highwaters = {}
            for table in ('decisions', 'projection_payloads', 'positions'):
                value = source.execute('SELECT COALESCE(MAX(rowid),0) FROM '+table).fetchone()[0]
                if type(value) is not int or value < 0:
                    raise ValueError('quote-control original rowid high-water differs')
                highwaters[table] = value
            nonterminal = controls.rows(source, '''SELECT rowid,position_id,asset,status FROM positions
                WHERE status!='closed' ORDER BY rowid''', limit=len(manifest['assets']))
            if any(row[2] not in manifest['assets'] for row in nonterminal):
                raise ValueError('quote-control nonterminal position asset differs')
            accounts[name] = dict(directory=profile['directory'], database_sha256=members[member],
                receipts_sha256=_hash(receipts), receipt_count=len(receipts),
                captured_floors_sha256=_hash(floors), captured_floor_count=len(floors),
                original_rowid_highwaters=highwaters, captured_nonterminal_positions=nonterminal)
    if set(accounts) != journal.ACCOUNTS:
        raise ValueError('quote-control original all-eight scope differs')
    pin = dict(schema_version=1, purpose=PURPOSE, experiment_id=request['experiment_id'],
        cohort_sha256=request['cohort_sha256'], release_sha256=request['release_sha256'],
        archive_operation_id=request['operation_id'], archive_sha256=request['archive_sha256'],
        archive_request_sha256=_hash(request), shared_database_sha256=members[relative],
        from_ordinal=first, through_ordinal=last, operation_count=len(prefix),
        original_records_sha256=_hash(prefix),
        outcome_count=sum(len(account['items']) for plan in plans for account in plan['accounts'].values()),
        accounts=accounts)
    if len(_json(pin).encode()) > MAX_PIN_BYTES:
        raise ValueError('quote-control inclusion pin exceeds bound')
    budget.check()
    return dict(pin=pin, pin_sha256=_hash(pin), compaction_authorized=False, activated=False)


def quote_control_segment_pin(root, *, archive_root, archive_operation_id, through_ordinal,
                              storage_owner, cache_root, from_ordinal=1, enabled=False,
                              max_bytes, timeout_seconds=120, now=None, owner_db=None):
    """Prove a bounded exact prefix from a later recorded retired cohort.

    Disposable restored evidence is owner-admitted. Live control and financial
    rows and archive objects are read inputs; no checkpoint is installed here.
    """
    if enabled is not True:
        raise ValueError('quote-control inclusion reader is disabled')
    _bounds(from_ordinal, through_ordinal)
    if not isinstance(storage_owner, StorageOwner):
        raise ValueError('quote-control inclusion requires original storage owner')
    if (not isinstance(archive_operation_id, str) or len(archive_operation_id) != 32 or
            any(c not in '0123456789abcdef' for c in archive_operation_id)):
        raise ValueError('quote-control recorded archive identity differs')
    if owner_db is not None and not isinstance(owner_db, sqlite3.Connection):
        raise ValueError('quote-control worker ownership differs')
    root, archive, cache = store._root(root), store._root(archive_root), store._root(cache_root)
    budget = _Budget(max_bytes, timeout_seconds, storage_owner.domain.clock)
    view_now = _now(now)
    manifest = json.loads(budget.read(root/'manifest.json', store.MAX_MANIFEST_BYTES))
    from hermes_trading.paired_paper import _profiles
    profiles = _profiles(root, manifest)
    selected = resolve_primary(manifest)
    shared = profiles[('adaptive', selected.wallet_eur)][0]/'paper_publications.sqlite'
    if not shared.is_file():
        raise ValueError('quote-control current shared journal is missing')
    owner_path = _owner_path(root, 'worker')
    account_paths = [path/'trading.db' for path, _, _ in profiles.values()]

    def quiescent():
        _quiescent_database(owner_path, budget, delete_mode=True)
        _quiescent_database(shared, budget, delete_mode=True)
        for path in account_paths:
            _quiescent_database(path, budget)

    quiescent()
    # No account writes or staged output: reserve restoration plus bounded
    # metadata. Whole-domain installed peak qualification is still separate.
    with storage_owner.admit(max_bytes+2*MAX_PIN_BYTES+65536,
            timeout_seconds=budget.deadline-storage_owner.domain.clock(), timeout_ceiling_seconds=120) as lease:
        lease.require_path(root)
        lease.require_path(cache)
        with (_exclusive(owner_path) if owner_db is None else nullcontext(owner_db)) as worker:
            names = worker.execute('PRAGMA database_list').fetchall()
            if (not names or Path(names[0][2]).resolve() != _owner_path(root, 'worker').resolve() or
                    worker.execute('PRAGMA locking_mode').fetchone()[0].lower() != 'exclusive' or worker.in_transaction):
                raise ValueError('quote-control worker ownership differs')
            worker.execute('BEGIN EXCLUSIVE')
            worker.commit()
            quiescent()
            with _exclusive(shared, query_only=True) as db:
                db.set_progress_handler(lambda: budget.check() or 0, 1000)
                _control_footprint(root, manifest, budget, db=db)
                with tempfile.TemporaryDirectory(prefix='.quote-control-', dir=cache) as temporary:
                    request, original, _ = _verify_archive(db, root, manifest, archive_operation_id,
                        archive, Path(temporary), budget, view_now,
                        preflight=lambda source: _control_footprint(source, manifest, budget))
                    result = _segment_pin(db, root, original, manifest, request,
                        from_ordinal, through_ordinal, budget)
                    budget.check()
                    quiescent()
                    lease.check()
                    return result
