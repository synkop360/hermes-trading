"""Disabled coordinated quote-control checkpoint transition.

Only verified original controls are compacted. Financial rows and artifacts
are never restored or replaced. A partial transition blocks ordinary work;
each account retains its individually applied replay authority for protection.
"""
from contextlib import closing, nullcontext
import json
from pathlib import Path
import sqlite3
import tempfile

from hermes_trading import archive_quote_state as state, archive_quote_checkpoint_state as cp, archive_store as store
from hermes_trading.archive_capacity import StorageOwner
from hermes_trading.archive_quote_retention import _readonly, _verify_archive, _verify_plan_originals
from hermes_trading.archive_quote_segment import _segment_pin, _prefix, _ControlBytes, _control_footprint, _quiescent_database, _exclusive
from hermes_trading.archive_shared_resolver import _Budget
from hermes_trading.archive_snapshot import _owner_path
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash, _json, _now


def _shared(root, manifest):
    return root/resolve_primary(manifest).profile_directory(manifest, 'adaptive')/'paper_publications.sqlite'


def _removable_floors(account, name, proposed, previous, plans, budget):
    """Select exactly the old terminal controls this account will remove."""
    covered = {plan['operation_id'] for plan in plans}
    prior = {} if previous is None else previous['accounts'][name]['active_floors']
    retained = proposed['accounts'][name]['active_floors']
    terminal = proposed['accounts'][name]['terminal']
    result = []
    for key, raw in account.execute("SELECT incident_id,reason FROM incidents WHERE incident_id LIKE 'archive:quote-floor:%'"):
        budget.check()
        budget.decoded(len(raw.encode()))
        floor = state.unpack(raw)
        if (key not in retained and (floor['operation_id'] in covered or prior.get(key) == floor) and
                cp.terminal_quote_retired(account, floor['asset'], floor['timeframe'], floor['through'], terminal)):
            result.append(key)
    return result


def _previous_original(db, root, original, manifest, previous, proposed, plans, budget):
    """Recheck overwritten originals, including still-unapplied resume members.

    The shared predecessor remains until all eight members acknowledge. A
    locally applied member instead has the pending checkpoint receipt: its
    deleted old rows must not be required or restored during recovery.
    """
    controls = _ControlBytes(budget)
    with closing(_readonly(_shared(original, manifest))) as archived:
        if cp.read_checkpoint(archived) != previous:
            raise ValueError('quote-checkpoint archived predecessor differs')
        if previous is not None:
            sql = 'SELECT rowid,state,sha256 FROM paired_quote_control_checkpoint WHERE id=1'
            left = controls.rows(db, sql, limit=1)
            right = controls.rows(archived, sql, limit=1)
            if len(left) != 1 or left != right:
                raise ValueError('quote-checkpoint predecessor raw shared record differs')
    for profile in manifest['profiles']:
        budget.check()
        name = f"{profile['arm']}_{profile['wallet_eur']}"
        member = profile['directory']+'/trading.db'
        with closing(_readonly(root/member)) as current, closing(_readonly(original/member)) as archived:
            current.set_progress_handler(lambda: budget.check() or 0, 1000)
            archived.set_progress_handler(lambda: budget.check() or 0, 1000)
            local = cp.read_local(current)
            if local == cp.local_receipt(proposed, name):
                continue
            if local != (None if previous is None else cp.local_receipt(previous, name)):
                raise ValueError('quote-checkpoint unapplied original local authority differs')
            sql = 'SELECT rowid,* FROM incidents WHERE incident_id=?'
            keys = [cp.INCIDENT]+[state.RECEIPT+plan['operation_id'] for plan in plans]
            for key in keys:
                left = controls.rows(current, sql, (key,), limit=1)
                right = controls.rows(archived, sql, (key,), limit=1)
                expected_count = 0 if key == cp.INCIDENT and previous is None else 1
                if len(left) != expected_count or left != right:
                    raise ValueError('quote-checkpoint original raw account receipt differs')
            # Later detailed operations may legitimately advance other floors.
            # Only rows this transition deletes must be exact archived originals.
            for key in _removable_floors(current, name, proposed, previous, plans, budget):
                left = controls.rows(current, sql, (key,), limit=1)
                right = controls.rows(archived, sql, (key,), limit=1)
                if len(left) != 1 or left != right:
                    raise ValueError('quote-checkpoint removed floor raw original differs')


def _active_floors(source, previous, plans, name, terminal, check):
    floors = {} if previous is None else dict(previous['accounts'][name]['active_floors'])
    for plan in plans:
        for item in plan['accounts'][name]['items']:
            check()
            key = state.floor_key(item['asset'], item['timeframe'])
            value = dict(schema_version=1, purpose=state.PURPOSE, asset=item['asset'], timeframe=item['timeframe'],
                         through=item['at'], operation_id=plan['operation_id'], plan_sha256=_hash(plan))
            if key not in floors or state.timestamp(value['through']) > state.timestamp(floors[key]['through']):
                floors[key] = value
    result = {}
    for key, floor in floors.items():
        check()
        identity = floor['timeframe'][len('paper-position:'):]
        row = source.execute('SELECT asset,status FROM positions WHERE position_id=?', (identity,)).fetchone()
        if row is None or row[0] != floor['asset']:
            raise ValueError('quote-checkpoint original replay lineage is missing')
        if cp.terminal_quote_retired(source, floor['asset'], floor['timeframe'], floor['through'], terminal):
            continue
        if identity not in {row[1] for row in terminal['nonterminal_positions']}:
            raise ValueError('quote-checkpoint original active lineage differs')
        result[key] = floor
    if len(result) > len(terminal['nonterminal_positions']):
        raise ValueError('quote-checkpoint active floor bound differs')
    return result


def _proposal(db, original, manifest, request, pin, previous, plans, budget):
    accounts = {}
    for profile in manifest['profiles']:
        budget.check()
        name = f"{profile['arm']}_{profile['wallet_eur']}"
        with closing(_readonly(original/profile['directory']/'trading.db')) as source:
            source.set_progress_handler(lambda: budget.check() or 0, 1000)
            terminal = cp.terminal_summary(source, manifest['assets'], budget.check)
            accounts[name] = dict(directory=profile['directory'], terminal=terminal,
                active_floors=_active_floors(source, previous, plans, name, terminal, budget.check),
                decision_highwater=pin['accounts'][name]['original_rowid_highwaters']['decisions'],
                projection_highwater=pin['accounts'][name]['original_rowid_highwaters']['projection_payloads'])
    legacy = (db.execute('SELECT COALESCE(MAX(rowid),0) FROM paired_quote_retention').fetchone()[0]
              if previous is None else previous['legacy_operation_highwater'])
    value = dict(schema_version=1, purpose=cp.PURPOSE, experiment_id=request['experiment_id'],
        cohort_sha256=request['cohort_sha256'], release_sha256=request['release_sha256'],
        from_ordinal=pin['from_ordinal'], through_ordinal=pin['through_ordinal'],
        cumulative_outcomes=(0 if previous is None else previous['cumulative_outcomes'])+pin['outcome_count'],
        previous_checkpoint_sha256=_hash(previous), segment_pin=pin, segment_pin_sha256=_hash(pin),
        legacy_operation_highwater=legacy, accounts=accounts)
    return cp.validate_checkpoint(value)


def _original_quote_evidence(db, root, manifest, plans, archive, cache, budget, now):
    """Keep original bundle references available before deleting their controls."""
    groups = {}
    for plan in plans:
        groups.setdefault(plan['archive_operation_id'], []).append(plan)
    for identity, selected in groups.items():
        budget.check()
        target = store._root(cache/('original-'+identity))
        target.mkdir()
        request, original, cut = _verify_archive(db, root, manifest, identity, archive, target, budget, now,
            preflight=lambda source: _control_footprint(source, manifest, budget))
        for plan in selected:
            if plan['archive_sha256'] != request['archive_sha256'] or plan['cut_at'] != cut:
                raise ValueError('quote-checkpoint original quote archive reference differs')
            _verify_plan_originals(plan, original, budget)


def _apply_account(root, name, proposed, previous, plans, budget, hit, at):
    directory = root/proposed['accounts'][name]['directory']
    with closing(sqlite3.connect(directory/'trading.db', timeout=0, isolation_level=None)) as account:
        account.set_progress_handler(lambda: budget.check() or 0, 1000)
        account.execute('PRAGMA synchronous=FULL')
        account.execute('BEGIN IMMEDIATE')
        try:
            expected = cp.local_receipt(proposed, name)
            if cp.read_local(account) == expected:
                cp.assert_terminal_summary(account, proposed['accounts'][name]['terminal'], budget.check)
                account.rollback()
                return
            if cp.read_local(account) != (None if previous is None else cp.local_receipt(previous, name)):
                raise ValueError('quote-checkpoint original local authority differs')
            cp.assert_terminal_summary(account, proposed['accounts'][name]['terminal'], budget.check)
            floors = _removable_floors(account, name, proposed, previous, plans, budget)
            for plan in plans:
                account.execute('DELETE FROM incidents WHERE incident_id=?', (state.RECEIPT+plan['operation_id'],))
            for key in floors:
                budget.check()
                account.execute('DELETE FROM incidents WHERE incident_id=?', (key,))
            state.store_incident(account, cp.INCIDENT, expected, at)
            if account.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('quote-checkpoint account foreign key integrity differs')
            account.commit()
        except BaseException:
            account.rollback()
            raise
    hit('after_account_commit', name)


def _result(value):
    return dict(schema_version=1, purpose=cp.PURPOSE, status='complete', checkpoint_sha256=_hash(value),
        through_ordinal=value['through_ordinal'], cumulative_outcomes=value['cumulative_outcomes'],
        accounts={name: True for name in value['accounts']}, activated=False, deployed=False,
        capacity_qualified=False, profitability_qualified=False)


def checkpoint_quote_controls(root, *, archive_root, archive_operation_id, through_ordinal,
                               storage_owner, cache_root, enabled=False, max_bytes,
                               timeout_seconds=120, now=None, owner_db=None, fault=None):
    """Complete/resume one verified all-eight prefix transition; disabled by default."""
    if enabled is not True:
        raise ValueError('quote-control checkpoint is disabled')
    cp.ordinal(through_ordinal)
    if not isinstance(storage_owner, StorageOwner):
        raise ValueError('quote-control checkpoint requires original storage owner')
    if (not isinstance(archive_operation_id, str) or len(archive_operation_id) != 32 or
            any(c not in '0123456789abcdef' for c in archive_operation_id)):
        raise ValueError('quote-control checkpoint archive identity differs')
    if owner_db is not None and not isinstance(owner_db, sqlite3.Connection):
        raise ValueError('quote-control checkpoint worker ownership differs')
    root, archive, cache = store._root(root), store._root(archive_root), store._root(cache_root)
    budget = _Budget(max_bytes, timeout_seconds, storage_owner.domain.clock)
    view_now = _now(now)
    manifest = json.loads(budget.read(root/'manifest.json', store.MAX_MANIFEST_BYTES))
    from hermes_trading.paired_paper import _profiles
    profiles = _profiles(root, manifest)
    shared, owner_path = _shared(root, manifest), _owner_path(root, 'worker')
    sql_paths = [path/'trading.db' for path, _, _ in profiles.values()]+[shared]

    def quiescent():
        _quiescent_database(owner_path, budget, delete_mode=True)
        _quiescent_database(shared, budget, delete_mode=True)
        for path in sql_paths[:-1]:
            _quiescent_database(path, budget)

    quiescent()
    peak = max_bytes+2*sum(path.stat().st_size for path in sql_paths)+2*cp.MAX_CHECKPOINT_BYTES+cp.MAX_CONTROL_BYTES+131072
    with storage_owner.admit(peak, timeout_seconds=budget.deadline-storage_owner.domain.clock(), timeout_ceiling_seconds=120) as lease:
        lease.require_path(root); lease.require_path(cache)
        with (_exclusive(owner_path) if owner_db is None else nullcontext(owner_db)) as worker:
            names = worker.execute('PRAGMA database_list').fetchall()
            if (not names or Path(names[0][2]).resolve() != owner_path.resolve() or
                    worker.execute('PRAGMA locking_mode').fetchone()[0].lower() != 'exclusive' or worker.in_transaction):
                raise ValueError('quote-control checkpoint worker ownership differs')
            worker.execute('BEGIN EXCLUSIVE'); worker.commit()
            quiescent()
            with _exclusive(shared) as db:
                db.set_progress_handler(lambda: budget.check() or 0, 1000)
                _control_footprint(root, manifest, budget, db=db)
                previous, pending, progress = cp.context(db)
                if previous is not None and through_ordinal <= previous['through_ordinal']:
                    if through_ordinal != previous['through_ordinal'] or archive_operation_id != previous['segment_pin']['archive_operation_id']:
                        raise ValueError('quote-control checkpoint old request cannot become a new transition')
                    state.assert_quote_cohort(root, db=db, manifest=manifest, sealed=True, check=budget.check)
                    return _result(previous)
                first = 1 if previous is None else previous['through_ordinal']+1
                if through_ordinal-first+1 > cp.MAX_TAIL:
                    raise ValueError('quote-control checkpoint prefix exceeds bound')
                if pending is not None and (pending['through_ordinal'] != through_ordinal or
                        pending['segment_pin']['archive_operation_id'] != archive_operation_id):
                    raise ValueError('quote-control checkpoint pending request differs')
                state.assert_quote_cohort(root, db=db, manifest=manifest, protective=pending is not None,
                                          sealed=True, check=budget.check)

                def hit(point, name):
                    if fault: fault(point, name)
                    budget.check(); lease.check()

                with tempfile.TemporaryDirectory(prefix='.quote-checkpoint-', dir=cache) as temporary:
                    temporary = Path(temporary)
                    request, original, _ = _verify_archive(db, root, manifest, archive_operation_id,
                        archive, temporary, budget, view_now,
                        preflight=lambda source: _control_footprint(source, manifest, budget))
                    with closing(_readonly(_shared(original, manifest))) as source:
                        source.set_progress_handler(lambda: budget.check() or 0, 1000)
                        controls = _ControlBytes(budget)
                        rows = _prefix(source, first, through_ordinal, controls)
                        if rows != _prefix(db, first, through_ordinal, controls):
                            raise ValueError('quote-control checkpoint original raw prefix differs')
                        if pending is None:
                            pin = _segment_pin(db, root, original, manifest, request, first, through_ordinal, budget)['pin']
                        else:
                            pin = _segment_pin(source, original, original, manifest, request, first, through_ordinal, budget)['pin']
                    plans = [state.unpack(row[2]) for row in rows]
                    proposed = _proposal(db, original, manifest, request, pin, previous, plans, budget)
                    if pending is not None and proposed != pending:
                        raise ValueError('quote-control checkpoint original pending proposal differs')
                    _previous_original(db, root, original, manifest, previous, proposed, plans, budget)
                    _original_quote_evidence(db, root, manifest, plans, archive, temporary, budget, view_now)
                    if pending is None:
                        progress = dict(accounts={name: False for name in proposed['accounts']})
                        # Reserve the largest intermediate state, including
                        # both singleton bodies and every new local receipt.
                        # Do not strand a partially applied cohort above its
                        # own logical admission ceiling.
                        added = (len(state.envelope(proposed).encode())+len(state.envelope(progress).encode())+
                                 len(_json(proposed).encode())+64+512)
                        for name in proposed['accounts']:
                            added += len(state.envelope(cp.local_receipt(proposed,name)).encode())+len(cp.INCIDENT)+512
                        if _control_footprint(root,manifest,budget,db=db)+added > cp.MAX_CONTROL_BYTES:
                            raise ValueError('quote-control checkpoint metadata byte headroom unavailable before pending mutation')
                        db.execute('BEGIN IMMEDIATE')
                        db.execute('''CREATE TABLE IF NOT EXISTS paired_quote_control_checkpoint(
                            id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL,sha256 TEXT NOT NULL)''')
                        db.execute('''CREATE TABLE IF NOT EXISTS paired_quote_control_pending(
                            id INTEGER PRIMARY KEY CHECK(id=1),plan TEXT NOT NULL,progress TEXT NOT NULL)''')
                        db.execute('INSERT INTO paired_quote_control_pending VALUES(1,?,?)',
                                   (state.envelope(proposed), state.envelope(progress)))
                        db.commit()
                    hit('after_plan_commit', None)
                    for name in sorted(proposed['accounts']):
                        if progress['accounts'][name]:
                            continue
                        _apply_account(root, name, proposed, previous, plans, budget, hit, view_now.isoformat())
                        progress['accounts'][name] = True
                        db.execute('UPDATE paired_quote_control_pending SET progress=? WHERE id=1', (state.envelope(progress),))
                        db.commit()
                        hit('after_account_ack', name)
                    state.assert_quote_cohort(root, db=db, manifest=manifest, protective=True, sealed=True, check=budget.check)
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('DELETE FROM paired_quote_retention WHERE rowid BETWEEN ? AND ?', (first, through_ordinal))
                    db.execute('INSERT OR REPLACE INTO paired_quote_control_checkpoint VALUES(1,?,?)', (_json(proposed), _hash(proposed)))
                    db.execute('DELETE FROM paired_quote_control_pending WHERE id=1')
                    db.commit()
                    state.assert_quote_cohort(root, db=db, manifest=manifest, sealed=True, check=budget.check)
                    hit('after_cohort_complete', None)
                    return _result(proposed)
