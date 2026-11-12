"""Read-only completed-cohort certification of an already consistent export.

This binds source evidence, not independent durability or permission to prune.
Pending reviews are preserved; unfinished batch/publication/projection work
requires recovery before a cut can qualify. No model is invoked.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from hermes_trading import archive_journal as journal
from hermes_trading import archive_store as store
from hermes_trading.archive_snapshot import INVENTORY
from hermes_trading.domain import json_value
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.paired_paper import _profiles, _causal_strategy, _encode, _snapshot
from hermes_trading.loop import _load_yaml
from hermes_trading.paired_performance_report import _canonical_account
from hermes_trading.publication_timeline import PublicationTimeline
from hermes_trading.paired_cost_stress import FixedOrderCostStress
from hermes_trading.wallet_metrics import _decimal
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _hash, _time, _now
from hermes_trading.paired_primary import resolve_primary,validate_primary_account,validate_primary_limits


def _selected_controller(controller,selected,config,policy,view,check):
    """Verify retained lifecycle evidence without admitting or mutating a cycle."""
    from scripts.hermes_watch import _supervisor_release
    account=json.loads(store._read(controller/'wallet_account.json',65536))
    digest=validate_primary_account(account,selected,expected_hash=_hash(config),experiment_id=policy['experiment_id'])
    limits=json.loads(store._read(controller/'supervisor_limits.json',65536))
    validate_primary_limits(limits,selected,digest);release=_supervisor_release()
    if (type(limits.get('schema_version')) is not int or limits['schema_version']!=1 or
            limits.get('experiment_id')!=policy['experiment_id'] or limits.get('policy_hash')!=_hash(policy) or
            limits.get('release_hash')!=_hash(release) or _time(limits.get('activated_at'))!=_time(account['activated_at'])):
        raise ValueError('snapshot supervisor lifecycle limits binding differs')
    for key,minimum,maximum in (('max_cycles',1,259200),('max_runtime_seconds',1,180*86400),
                               ('interval_seconds',60,240),('max_model_attempts',0,1000)):
        if type(limits.get(key)) is not int or not minimum<=limits[key]<=maximum:
            raise ValueError('snapshot supervisor lifecycle bounds differ')
    check();path=controller/'supervisor_lifecycle.json'
    if not path.exists():return
    state=json.loads(store._read(path,65536))
    deadline=_time(limits['activated_at'])+timedelta(seconds=limits['max_runtime_seconds'])
    if (type(state) is not dict or type(state.get('schema_version')) is not int or state['schema_version']!=1 or
            state.get('experiment_id')!=policy['experiment_id'] or
            state.get('binding')!=_hash({'limits':limits,'policy':policy,'release':release}) or
            _time(state.get('deadline'))!=deadline or state.get('status') not in ('running','stopped')):
        raise ValueError('snapshot supervisor lifecycle binding differs')
    for key,maximum in (('cycles_started',limits['max_cycles']),('model_attempts_reserved',limits['max_model_attempts'])):
        if type(state.get(key)) is not int or not 0<=state[key]<=maximum:
            raise ValueError('snapshot supervisor lifecycle counters differ')
    if not _time(limits['activated_at'])<=_time(state.get('last_observed_at'))<=view:
        raise ValueError('snapshot supervisor lifecycle observation differs')


@contextmanager
def _database(path, *, check=None):
    path = store._root(path)
    for suffix in ('-wal', '-journal'):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('snapshot database has unsealed sidecar evidence')
    if check is not None:check()
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True,timeout=0)
    connection.row_factory = sqlite3.Row
    interrupted=[]
    def progress():
        try:check()
        except BaseException as error:
            interrupted.append(error);return 1
        return 0
    try:
        if check is not None:connection.set_progress_handler(progress,1000)
        connection.execute('PRAGMA query_only=ON')
        yield connection
        if check is not None:check()
    except sqlite3.OperationalError:
        if interrupted:raise interrupted[0]
        raise
    finally:
        connection.close()


def _verified_inventory(root, expected, check):
    raw = store._read(root / INVENTORY, store.MAX_MANIFEST_BYTES)
    if hashlib.sha256(raw).hexdigest() != store._digest(expected):
        raise ValueError('snapshot inventory digest differs')
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ValueError('snapshot inventory malformed') from error
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'purpose', 'files'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or
            value['purpose'] != 'paired_consistent_snapshot_v1' or not isinstance(value['files'], list) or
            not 1 <= len(value['files']) <= store.MAX_FILES):
        raise ValueError('snapshot inventory fields differ')
    total = len(raw); names = set(); folded = set()
    for row in value['files']:
        check()
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}:
            raise ValueError('snapshot inventory row differs')
        name = store._name(row['path']); store._digest(row['sha256'])
        if name in names or name.casefold() in folded or name == INVENTORY:
            raise ValueError('snapshot inventory identities collide')
        names.add(name); folded.add(name.casefold())
        if type(row['bytes']) is not int or not 0 <= row['bytes'] <= store.MAX_TOTAL_BYTES:
            raise ValueError('snapshot inventory byte budget differs')
        total += row['bytes']
        if total > store.MAX_TOTAL_BYTES:
            raise ValueError('snapshot inventory total exceeds bound')
        path = store._root(root / name)
        if not path.is_file() or path.stat().st_size != row['bytes']:
            raise ValueError('snapshot inventory evidence missing or changed')
        digest = hashlib.sha256(); read = 0
        with path.open('rb') as stream:
            while chunk := stream.read(store.CHUNK_BYTES):
                check(); read += len(chunk)
                if read > row['bytes']:
                    raise ValueError('snapshot inventory evidence grew')
                digest.update(chunk)
        if read != row['bytes'] or digest.hexdigest() != row['sha256']:
            raise ValueError('snapshot inventory evidence hash differs')
    found = set(); stack = [root]; visited = 0
    while stack:
        check()
        for path in stack.pop().iterdir():
            check(); path = store._root(path); visited += 1
            if visited > 2 * store.MAX_FILES:
                raise ValueError('snapshot inventory traversal exceeds bound')
            if path.is_dir():
                stack.append(path)
            elif path.is_file():
                found.add(path.relative_to(root).as_posix())
            else:
                raise ValueError('snapshot inventory has nonregular evidence')
    if found != names | {INVENTORY}:
        raise ValueError('snapshot inventory evidence set differs')
    return value['files']


def _reconcile_finance(db, path, config, at, observation, check):
    # Reporting may filter a historical window. Certifying a current cut must
    # instead reject any economic activity beyond that cut before extraction.
    for table, fields in (('orders', ('created_at',)), ('order_intents', ('created_at',)),
            ('order_fills', ('filled_at',)), ('positions', ('opened_at', 'closed_at'))):
        for row in db.execute('SELECT '+','.join(fields)+' FROM '+table):
            check()
            if any(stamp is not None and _time(stamp) > at for stamp in row):
                raise ValueError('future financial activity exceeds the settled snapshot cut')
    for order in db.execute('SELECT * FROM orders'):
        check()
        intent = db.execute('SELECT * FROM order_intents WHERE client_order_id=?', (order['client_order_id'],)).fetchone()
        if intent is None or any(order[field] != intent[field] for field in ('asset', 'side', 'order_type')):
            raise ValueError('original fill/intent identity differs')
        if _decimal(order['requested_quantity']) != _decimal(intent['quantity']):
            raise ValueError('original requested fill quantity differs')
        fills = db.execute('SELECT * FROM order_fills WHERE order_id=?', (order['order_id'],)).fetchall()
        if not fills or any(fill['asset'] != order['asset'] or fill['side'] != order['side'] for fill in fills):
            raise ValueError('original fills are missing or mismatched')
        quantity = sum((_decimal(fill['quantity']) for fill in fills), _decimal(0))
        gross = sum((_decimal(fill['quantity'])*_decimal(fill['price']) for fill in fills), _decimal(0))
        fees = sum((_decimal(fill['fee']) for fill in fills), _decimal(0))
        if (any(_decimal(fill['quantity']) <= 0 or _decimal(fill['price']) <= 0 or _decimal(fill['fee']) < 0 for fill in fills) or
                quantity != _decimal(order['filled_quantity']) or fees != _decimal(order['fees']) or
                gross != quantity * _decimal(order['average_fill_price'])):
            raise ValueError('original fill quantity/price/fee does not reconcile')
    orders, positions, _ = _canonical_account(path, at, database=db)
    book = FixedOrderCostStress(config['starting_balance'])
    for order in orders:
        check(); book.apply_order(order)
    opened = {}
    realized = _decimal(0); basis = _decimal(0); entry_fees = _decimal(0)
    for position in positions:
        check()
        if position['status'] == 'closed':
            realized += _decimal(position['realized_pnl'])
        elif position['status'] == 'open':
            asset = position['asset']
            if asset in opened:
                raise ValueError('duplicate original open inventory')
            opened[asset] = position
            basis += _decimal(position['quantity'])*_decimal(position['entry_price'])
            entry_fees += _decimal(position['fees'])
        else:
            raise ValueError('unsettled financial inventory requires reconciliation')
    if set(opened) != set(book.holdings):
        raise ValueError('original fill inventory does not reconcile with positions')
    for asset, position in opened.items():
        holding = book.holdings[asset]; quantity = _decimal(position['quantity'])
        if (quantity != holding['quantity'] or _decimal(position['entry_price']) != holding['base_entry'] or
                str(position['strategy_version']) != holding['strategy_version'] or
                _decimal(position['fees']) != quantity*holding['base_entry']*holding['entry_fee_rate']):
            raise ValueError('original fill inventory/basis/entry fee differs from positions')
    derived_cash = _decimal(config['starting_balance']) + realized - basis - entry_fees
    if derived_cash != book.base_cash:
        raise ValueError('original fills and realized position cash do not reconcile')
    if observation is not None and (_decimal(observation['trading_cash']) != book.base_cash or
            _decimal(observation['closed_net_pnl']) != realized or _decimal(observation['open_basis']) != basis or
            _decimal(observation['open_entry_fees']) != entry_fees):
        raise ValueError('original financial cash/basis does not reconcile with settlement')


def certify(root, *, expected_inventory_sha256, experiment_id, expected_release,
            through_sequence, supervisor_root=None, expected_supervisor_inventory_sha256=None,
            now=None, check=lambda: None):
    """Verify original canonical finance, settlement and pending-review state."""
    view = _now(now)
    root = store._root(root); rows = _verified_inventory(root, expected_inventory_sha256, check)
    journal._ordinal(through_sequence)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    profiles = _profiles(root, manifest)
    if any(config['experiment_id'] != experiment_id for _, _, config in profiles.values()):
        raise ValueError('snapshot experiment differs')
    selected=resolve_primary(manifest)
    primary = profiles[('adaptive', selected.wallet_eur)][0]
    opening=None
    with _database(primary / 'paper_publications.sqlite', check=check) as db:
        from hermes_trading.archive_quote_state import assert_quote_cohort
        assert_quote_cohort(root,db=db,manifest=manifest,sealed=True,check=check)
        checkpoint = journal._checkpoint(db); source = journal._read_journal(db, checkpoint, check=check)
        if source['pending']:
            raise ValueError('pending common batch cannot certify archive cut')
        retired = 0 if checkpoint is None else checkpoint['retired_complete']
        if not retired < through_sequence < source['complete']:
            raise ValueError('snapshot cut must retain a completed tail')
        if source['cohort_sha256'] != _hash(manifest):
            raise ValueError('snapshot cohort manifest differs')
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
        if exists and db.execute('SELECT 1 FROM publications WHERE receipt IS NULL LIMIT 1').fetchone():
            raise ValueError('unresolved publication cannot certify archive cut')
        latest = db.execute("SELECT batch_id,payload,completed,input_hash FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
        packet = load_packet(db, latest['payload']); completed = load_packet(db, latest['completed'])
        if packet.get('release') != expected_release:
            raise ValueError('snapshot runtime release differs')
        if selected.versioned:
            from hermes_trading.paired_risk import validate_risk_state
            validate_risk_state(packet.get('risk_state_before_execution'),selected)
            from hermes_trading.paired_inception import _validate
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_inception'").fetchone():
                raise ValueError('snapshot opening receipt missing')
            row=db.execute('SELECT payload,receipt_hash FROM paired_inception WHERE id=1').fetchone()
            if row is None:raise ValueError('snapshot opening receipt missing')
            opening=_validate(load_packet(db,row[0]),row[1],manifest,profiles)
        if _time(packet['created_at']) > view:
            raise ValueError('snapshot cut is future-dated')
        PublicationTimeline(primary, db, profiles[('adaptive', selected.wallet_eur)][1]['strategy_sha256'], now=view)
        allowed = {arm: {profile['strategy_sha256'] for (other, _), (_, profile, _) in profiles.items() if other == arm}
                   for arm in ('adaptive', 'control')}
        if exists:
            for request_raw, receipt_raw in db.execute('SELECT request,receipt FROM publications'):
                request = json.loads(request_raw); receipt = json.loads(receipt_raw)
                allowed['adaptive'].add(store._digest(request['base_sha256']))
                allowed['adaptive'].add(store._digest(receipt['candidate_sha256']))
        primary_strategy = _load_yaml(primary / 'strategy.yaml')
        effective = _causal_strategy(db, primary_strategy, (primary / 'strategy.yaml').read_bytes(),
            {asset: _snapshot(value) for asset, value in packet['snapshots'].items()},
            now=_time(packet['created_at']), baseline_hash=profiles[('adaptive', selected.wallet_eur)][1]['strategy_sha256'],
            baseline_path=primary / 'history/v0001.yaml')
        if _encode(effective) != packet['strategies']['adaptive']:
            raise ValueError('snapshot adaptive strategy causality differs')
        settlement = db.execute('SELECT payload FROM paired_settlements WHERE batch_id=?', (latest['batch_id'],)).fetchone()
        measured = load_packet(db, settlement[0])
        if any(not isinstance(value, dict) or value.get('finalized') is not True for value in completed.values()):
            raise ValueError('snapshot account finalization incomplete')
        risk = db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()
        if risk is None:
            raise ValueError('snapshot risk state missing')
        if selected.versioned:validate_risk_state(json.loads(risk[0]),selected)
        prefix = journal._prefix(checkpoint, source['rows'], through_sequence)
        next_input = next(row['input_hash'] for row in source['rows'] if row['sequence'] == through_sequence + 1)
    with _database(root / 'worker_runtime.sqlite', check=check) as db:
        lifecycle = json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        if (lifecycle.get('experiment_id') != experiment_id or lifecycle.get('completed_batches') != source['complete'] or
                lifecycle.get('archive_revision', 0) != (0 if checkpoint is None else checkpoint['revision'])):
            raise ValueError('snapshot worker lifetime witness differs')
    at = _time(packet['created_at'])
    for (arm, wallet), (path, profile, config) in profiles.items():
        check(); name = f'{arm}_{wallet}'
        if packet['bindings'][name]['account_hash'] != _hash(config):
            raise ValueError('snapshot account binding differs')
        if packet['bindings'][name]['goal_hash'] != _hash(_encode(_load_yaml(path / 'goal.yaml'))):
            raise ValueError('snapshot goal binding differs')
        current_strategy = hashlib.sha256((path / 'strategy.yaml').read_bytes()).hexdigest()
        if (current_strategy not in allowed[arm] or
                (arm == 'control' and current_strategy != profile['strategy_sha256'])):
            raise ValueError('snapshot fixed control or strategy source differs')
        for history in (path / 'history').glob('v*.yaml'):
            if hashlib.sha256(history.read_bytes()).hexdigest() not in allowed[arm]:
                raise ValueError('snapshot original strategy history contract differs')
        for filename, expected in packet['archives'][name].items():
            if hashlib.sha256((path / 'history' / store._name(filename)).read_bytes()).hexdigest() != expected:
                raise ValueError('snapshot strategy history differs from common batch')
        with _database(path / 'trading.db', check=check) as db:
            repository = StateRepository(db)
            if repository.has_pending_projection_items():
                raise ValueError('snapshot pending projection requires recovery')
            if _hash(json_value(repository.portfolio_positions())) != measured[name]['positions_hash']:
                raise ValueError('snapshot positions differ from final settlement')
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('snapshot original financial linkage differs')
            _reconcile_finance(db, path, config, at, measured[name]['observation'], check)
        if measured[name]['observation'] is not None or opening is not None:
            with _database(path / 'wallet_equity.sqlite', check=check) as db:
                if measured[name]['observation'] is not None:
                    observation = db.execute('SELECT at,payload FROM observations ORDER BY at DESC LIMIT 1').fetchone()
                    if observation is None or _time(observation['at']) != at or load_packet(db, observation['payload']) != measured[name]['observation']:
                        raise ValueError('snapshot wallet observation differs from settlement')
                if opening is not None:
                    row=db.execute('SELECT payload FROM observations WHERE at=?',(opening['at'],)).fetchone()
                    if row is None or load_packet(db,row[0])!=opening['accounts'][name]:
                        raise ValueError('snapshot opening wallet copy differs or is missing')
    policy = json.loads((primary / 'reflection_owner.json').read_text(encoding='utf-8'))
    if supervisor_root is None:
        if expected_supervisor_inventory_sha256 is not None or not (primary / 'llm_review_state.sqlite').is_file():
            raise ValueError('a separately pinned PC supervisor snapshot is required')
        controller = primary; supervisor_inventory = expected_inventory_sha256
        supervisor_scope = 'co_located'
    else:
        controller = store._root(supervisor_root)
        if controller == root or controller in root.parents or root in controller.parents:
            raise ValueError('external supervisor requires a separate snapshot')
        if expected_supervisor_inventory_sha256 is None:
            raise ValueError('external supervisor inventory pin is required')
        _verified_inventory(controller, expected_supervisor_inventory_sha256, check)
        controller_policy = json.loads((controller / 'reflection_owner.json').read_text(encoding='utf-8'))
        if _hash(controller_policy) != _hash(policy):
            raise ValueError('external supervisor policy differs from worker owner')
        supervisor_inventory = expected_supervisor_inventory_sha256
        supervisor_scope = 'separate_snapshot'
    if selected.versioned:
        _selected_controller(controller,selected,profiles[('adaptive',selected.wallet_eur)][2],policy,view,check)
    with _database(controller / 'llm_review_state.sqlite', check=check) as db:
        supervisor = json.loads(db.execute('SELECT state FROM supervisor WHERE id=1').fetchone()[0])
        if supervisor.get('experiment_id') != experiment_id or supervisor.get('policy_hash') != _hash(policy):
            raise ValueError('snapshot supervisor binding differs')
        consumed = supervisor.get('completed_trade_ids')
        if not isinstance(consumed, list) or any(not isinstance(value, str) for value in consumed) or len(set(consumed)) != len(consumed):
            raise ValueError('snapshot review watermark differs')
        pending = supervisor.get('pending_review'); pending_records = []
        if pending is not None and (not isinstance(pending, dict) or
                not isinstance(pending.get('review_id'), str) or not pending['review_id'] or
                pending.get('outcome') != 'pending'):
            raise ValueError('snapshot pending review record differs')
        for review_id, record in db.execute('SELECT review_id,record FROM reviews'):
            check(); review = json.loads(record)
            if review.get('review_id') != review_id:
                raise ValueError('snapshot review identity differs')
            if review.get('outcome') == 'pending':
                # ReviewLease persists the complete request in both places.
                # Matching only an ID would permit a lost/altered trade window,
                # model response or publication intent during recovery.
                pending_records.append(review)
        if _hash(pending_records) != _hash([] if pending is None else [pending]):
            raise ValueError('snapshot pending review state differs')
    accounts = {f'{arm}_{wallet}': _hash([row for row in rows if row['path'].startswith(profile['directory'] + '/')])
                for (arm, wallet), (_, profile, _) in profiles.items()}
    if supervisor_scope == 'separate_snapshot':
        name=selected.profile_key('adaptive')
        accounts[name] = _hash({'worker_account': accounts[name],
                                        'supervisor_inventory': supervisor_inventory})
    return {'schema_version': 1, 'purpose': 'paired_completed_snapshot_v1', 'experiment_id': experiment_id,
        'inventory_sha256': expected_inventory_sha256, 'cohort_sha256': source['cohort_sha256'],
        'release_sha256': _hash(expected_release), 'observed_complete': source['complete'],
        'through_sequence': through_sequence, 'prefix_sha256': prefix, 'next_input_hash': next_input,
        'previous_checkpoint_sha256': _hash(checkpoint), 'worker_lifecycle_sha256': _hash(lifecycle),
        'risk_state_sha256': _hash(json.loads(risk[0])), 'supervisor_state_sha256': _hash(supervisor),
        'supervisor_scope': supervisor_scope, 'supervisor_inventory_sha256': supervisor_inventory,
        'pending_review': pending,
        'account_snapshots': accounts,
        'retirement_authorized': False, 'independent_backup_verified': False}
