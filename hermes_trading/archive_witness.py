"""Disabled durable receiver-side collection reservations and lifetime floors.

This is a local protocol kernel, not an installed external witness. A verified
adapter must supply original source views, authenticate requests and enforce
independent storage/backup policy before runtime installation. Missing/corrupt
evidence is never reinitialized. No financial journal or source file is edited.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid

from hermes_trading import archive_journal as journal
from hermes_trading.archive_store import _root
from hermes_trading.reflection_supervision import _json, _hash, _now, _time

PURPOSE = 'paired_archive_witness_v1'
MAX_EVENTS = 4 * journal.MAX_BATCHES + 1
MAX_EVENT_BYTES = 8192
MAX_WITNESS_BYTES = 4 * 1024**3
IDENTITY_FIELDS = {'experiment_id', 'cohort_sha256', 'release_sha256', 'policy_sha256',
                   'limits_sha256', 'max_cycles', 'deadline'}
POINT_FIELDS = {'complete', 'prefix_sha256', 'archive_revision', 'retired_complete', 'checkpoint_sha256'}
VIEW_FIELDS = POINT_FIELDS | {'identity_sha256', 'prior_prefix_sha256', 'pending',
                              'pending_reservation_id', 'latest_reservation_id'}
EVENT_FIELDS = {'schema_version', 'purpose', 'sequence', 'previous_sha256', 'operation_id',
                'identity_sha256', 'at', 'request', 'state'}


class _ReceiverBudget:
    """Cooperative CPU/SQL deadline and receiver-only SQLite growth ceiling.

    Filesystem stalls require a separately enforced transport/process timeout.
    This does not impose a quota on unrelated files or certify whole-state peak.
    """
    def __init__(self, path, max_bytes, timeout_seconds, clock, *, timeout_ceiling_seconds=10):
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_WITNESS_BYTES:
            raise ValueError('witness storage byte bound differs')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or
                type(timeout_ceiling_seconds) is not int or not 1 <= timeout_ceiling_seconds <= 120 or
                not 0 < timeout_seconds <= timeout_ceiling_seconds):
            raise ValueError('witness deadline exceeds its finite bound')
        self.path=path;self.max_bytes=max_bytes;self.clock=clock;self.started=clock()
        self.deadline=self.started+timeout_seconds
        self.check()

    def check_time(self):
        at=self.clock()
        if not math.isfinite(at) or at < self.started:raise ValueError('witness clock differs')
        if at >= self.deadline:raise TimeoutError('witness verification deadline exceeded')

    def check(self):
        self.check_time()
        if not self.path.is_file():raise ValueError('witness database is missing')
        total=0
        for suffix in ('','-wal','-shm','-journal'):
            path=_root(self.path.with_name(self.path.name+suffix))
            try:total+=path.stat().st_size
            except FileNotFoundError:pass
        if total > self.max_bytes:raise ValueError('witness storage bytes exceed bound')
        return total

    def attach(self, db):
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_EVENT_BYTES+2048)
        db.set_progress_handler(lambda:int(self.clock() >= self.deadline),1000)
        # The journal peak calculation below assumes rollback-journal mode.
        if db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise ValueError('witness storage requires rollback-journal mode')

    def write_ceiling(self, db):
        total=self.check()
        page_size=db.execute('PRAGMA page_size').fetchone()[0]
        pages=db.execute('PRAGMA page_count').fetchone()[0]
        # Three database-sized extents plus64KiB conservatively admit the main
        # file, rollback pages/headers and bounded statement-journal overhead.
        # SQLite enforces the main-file page ceiling; this is not a disk quota.
        other_bytes=max(0,total-self.path.stat().st_size)
        maximum=(self.max_bytes-other_bytes-65536)//(3*page_size)
        if maximum < pages:raise ValueError('witness storage peak exceeds bound')
        actual=db.execute('PRAGMA max_page_count='+str(maximum)).fetchone()[0]
        if actual > maximum:raise ValueError('witness storage page ceiling differs')

    def sql_error(self, error):
        if isinstance(error,sqlite3.OperationalError) and 'interrupted' in str(error).lower():
            raise TimeoutError('witness SQL verification deadline exceeded') from error
        raise error


def _enabled(value):
    if value is not True:
        raise ValueError('archive witness is disabled')


def _token(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('witness reservation/operation token differs')
    return value


def _identity(value):
    if not isinstance(value, dict) or set(value) != IDENTITY_FIELDS:
        raise ValueError('witness identity fields differ')
    if not isinstance(value['experiment_id'], str) or not 1 <= len(value['experiment_id']) <= 128:
        raise ValueError('witness experiment identity differs')
    for field in ('cohort_sha256', 'release_sha256', 'policy_sha256', 'limits_sha256'):
        journal._digest(value[field])
    journal._ordinal(value['max_cycles'])
    if not isinstance(value['deadline'], str) or datetime.fromisoformat(value['deadline']).tzinfo is None:
        raise ValueError('witness deadline must be aware')
    _time(value['deadline'])


def _genesis_state():
    return {'point': {'complete': 0, 'prefix_sha256': _hash([]), 'archive_revision': 0,
            'retired_complete': 0, 'checkpoint_sha256': _hash(None)}, 'reservation': None}


def identity_for(root,*,database_factory=None,check=lambda:None):
    """Read the fixed experiment identity; caller owns the worker boundary."""
    from hermes_trading.paired_paper import _profiles
    from hermes_trading.paper_publication import _release_hashes
    from hermes_trading.paired_primary import resolve_primary
    root = _root(root); manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    profiles = _profiles(root, manifest); selected=resolve_primary(manifest)
    primary = profiles[('adaptive', selected.wallet_eur)][0]
    policy = json.loads((primary / 'reflection_owner.json').read_text(encoding='utf-8'))
    limits = json.loads((root / 'worker_limits.json').read_text(encoding='utf-8'))
    base_limits = {key: value for key, value in limits.items() if key != 'archive_witness'}
    storage = json.loads((root / 'storage_limits.json').read_text(encoding='utf-8'))
    for key, maximum in (('max_cycles', journal.MAX_BATCHES), ('max_runtime_seconds', 180*86400), ('interval_seconds', 240)):
        if type(limits.get(key)) is not int or not 1 <= limits[key] <= maximum:
            raise ValueError('witness original worker limits differ')
    activated = profiles[('adaptive', selected.wallet_eur)][2]['activated_at']
    if 'reporting_protocol' in manifest:
        from hermes_trading.paired_inception import read_paired_inception
        activated = read_paired_inception(root,database_factory=database_factory,check=check)['at']
    result = {'experiment_id': policy['experiment_id'], 'cohort_sha256': _hash(manifest),
        'release_sha256': _hash(_release_hashes()), 'policy_sha256': _hash(policy),
        'limits_sha256': _hash({'worker_limits': base_limits, 'storage': storage,
            'accounts': {f'{arm}_{wallet}': config for (arm, wallet), (_, _, config) in profiles.items()},
            'goals': {f'{arm}_{wallet}': hashlib.sha256((path / 'goal.yaml').read_bytes()).hexdigest()
                      for (arm, wallet), (path, _, _) in profiles.items()}}),
        'max_cycles': limits['max_cycles'],
        'deadline': (_time(activated) + timedelta(seconds=limits['max_runtime_seconds'])).isoformat()}
    _identity(result); return result


def source_view(root, identity, *, prior_complete, enabled=False, database=None):
    """Read real checkpoint-plus-suffix prefixes from one SQLite snapshot.

    No database or missing financial history is created. The optional supplied
    connection belongs to the worker's existing publication lease.
    """
    _enabled(enabled); _identity(identity); journal._ordinal(prior_complete, zero=True)
    root = _root(root)
    if _hash(identity_for(root)) != _hash(identity):
        raise ValueError('witness original worker identity changed')
    from hermes_trading.paired_paper import _profiles
    from hermes_trading.paired_primary import resolve_primary
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    primary = _profiles(root, manifest)[('adaptive', resolve_primary(manifest).wallet_eur)][0]; path = primary / 'paper_publications.sqlite'
    def inspect(db):
        checkpoint = None if db is None else journal._checkpoint(db)
        exists = db is not None and db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_batches'").fetchone() is not None
        if not exists:
            if checkpoint is not None or prior_complete:
                raise ValueError('witness source history regressed or missing')
            return {'identity_sha256': _hash(identity), **_genesis_state()['point'],
                'prior_prefix_sha256': _hash([]), 'pending': False,
                'pending_reservation_id': None, 'latest_reservation_id': None}
        data = journal._read_journal(db, checkpoint)
        retired = 0 if checkpoint is None else checkpoint['retired_complete']
        if data['cohort_sha256'] != identity['cohort_sha256']:
            raise ValueError('witness source cohort differs')
        if checkpoint is not None:
            archived_request = journal._operation(db, checkpoint['operation_id'])['request']
            if (archived_request['experiment_id'] != identity['experiment_id'] or
                    archived_request['release_sha256'] != identity['release_sha256']):
                raise ValueError('witness source checkpoint release/experiment differs')
        if data['complete'] < prior_complete:
            raise ValueError('witness source lifetime regressed')
        if prior_complete < retired:
            raise ValueError('witness source prior prefix is no longer retained; refresh authoritative floor')
        pending = latest = None
        for row in db.execute('SELECT status,payload FROM paired_batches ORDER BY rowid'):
            packet = journal.load_packet(db, row[1])
            if packet['manifest_hash'] != identity['cohort_sha256'] or _hash(packet['release']) != identity['release_sha256']:
                raise ValueError('witness original source packet release/cohort differs')
            if row[0] == 'pending':pending = packet.get('witness_reservation_id')
            else:latest = packet.get('witness_reservation_id')
        view = {'identity_sha256': _hash(identity), 'complete': data['complete'],
            'prefix_sha256': journal._prefix(checkpoint, data['rows'], data['complete']),
            'prior_prefix_sha256': journal._prefix(checkpoint, data['rows'], prior_complete),
            'archive_revision': 0 if checkpoint is None else checkpoint['revision'], 'retired_complete': retired,
            'checkpoint_sha256': _hash(checkpoint), 'pending': data['pending'],
            'pending_reservation_id': pending, 'latest_reservation_id': latest}
        _view(view, identity); return view
    if database is not None:
        names = database.execute('PRAGMA database_list').fetchall()
        if not names or _root(names[0][2]).resolve() != path.resolve():
            raise ValueError('witness source connection differs')
        return inspect(database)
    if not path.exists():return inspect(None)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
        db.execute('BEGIN'); return inspect(db)


def worker_identity(root, config, client):
    """Validate an explicit native-worker opt-in. No adapter is installed here.

    An installed client must authenticate the independently retained receiver,
    enforce each supplied timeout, and use receiver time for reserve deadlines.
    A local test client does not certify these deployment requirements.
    """
    if config is None:
        if client is not None:raise ValueError('archive witness adapter requires explicit configuration')
        return None
    if (not isinstance(config, dict) or set(config) != {'schema_version', 'binding', 'identity_sha256'} or
            type(config['schema_version']) is not int or config['schema_version'] != 1):
        raise ValueError('archive witness configuration differs')
    if client is None or not callable(getattr(client, 'read', None)) or not callable(getattr(client, 'apply', None)):
        raise ValueError('configured archive witness requires a verified adapter')
    binding = config['binding']
    if not isinstance(binding, dict) or set(binding) != {'witness_id', 'genesis_sha256'}:
        raise ValueError('archive witness binding differs')
    _token(binding['witness_id']);journal._digest(binding['genesis_sha256'])
    identity = identity_for(root)
    if config['identity_sha256'] != _hash(identity):
        raise ValueError('archive witness original identity pin differs')
    return identity


def _worker_head(value, binding, identity):
    fields = {'witness_id', 'genesis_sha256', 'identity_sha256', 'head_sha256', 'sequence', 'state'}
    if (not isinstance(value, dict) or set(value) != fields or
            len(_json(value).encode()) > MAX_EVENT_BYTES or
            value['witness_id'] != binding['witness_id'] or value['genesis_sha256'] != binding['genesis_sha256'] or
            value['identity_sha256'] != _hash(identity)):
        raise ValueError('archive witness response binding differs')
    journal._digest(value['head_sha256'])
    if type(value['sequence']) is not int or not 0 <= value['sequence'] < MAX_EVENTS:
        raise ValueError('archive witness response sequence differs')
    state = value['state']
    if not isinstance(state, dict) or set(state) != {'point', 'reservation'} or set(state['point']) != POINT_FIELDS:
        raise ValueError('archive witness response state differs')
    point = state['point'];reservation = state['reservation']
    if reservation is not None:
        if (not isinstance(reservation, dict) or set(reservation) != {'sequence', 'reservation_id'} or
                type(reservation['sequence']) is not int or reservation['sequence'] != point['complete'] + 1):
            raise ValueError('archive witness response reservation differs')
        _token(reservation['reservation_id'])
    _view(dict(point, identity_sha256=_hash(identity), prior_prefix_sha256=point['prefix_sha256'],
        pending=False, pending_reservation_id=None, latest_reservation_id=None), identity)
    if point['complete'] > identity['max_cycles']:
        raise ValueError('archive witness original cycle bound differs')
    return deepcopy(value)


def reconcile_worker(root, config, client, identity, *, reserve=False):
    """Reconcile original journal evidence before any new native collection.

    The injected client is a bounded authenticated transport contract, not a
    network implementation. No automatic cancellation of unfrozen reservations
    is permitted: after an ambiguous failure, explicit recovery is required.
    """
    binding = config['binding']
    head = _worker_head(client.read(binding=binding, identity=identity, timeout=10), binding, identity)
    view = source_view(root, identity, prior_complete=head['state']['point']['complete'], enabled=True)
    action = 'reserve' if reserve else 'check';operation_id = uuid.uuid4().hex
    result = _worker_head(client.apply(view, binding=binding, identity=identity, action=action,
        expected_head_sha256=head['head_sha256'], operation_id=operation_id, timeout=10), binding, identity)
    request = {'action': action, 'view': view, 'active_token': None,
        'expected_head_sha256': head['head_sha256'], 'operation_id': operation_id}
    # Source and receiver both enforce the bound. Actual receiver time remains
    # authoritative; this local check validates the returned transition only.
    expected = _advance(head['state'], request, identity, _time(identity['deadline']) - timedelta(microseconds=1))
    if (_hash(result['state']) != _hash(expected) or
            result['sequence'] not in {head['sequence'], head['sequence'] + 1} or
            (result['sequence'] == head['sequence'] and result != head)):
        raise ValueError('archive witness acknowledged transition differs')
    reservation = result['state']['reservation']
    return None if reservation is None else reservation['reservation_id']


def _view(value, identity):
    if not isinstance(value, dict) or set(value) != VIEW_FIELDS or value['identity_sha256'] != _hash(identity):
        raise ValueError('witness original source identity/view differs')
    for field in ('complete', 'archive_revision', 'retired_complete'):
        journal._ordinal(value[field], zero=True)
    for field in ('prefix_sha256', 'prior_prefix_sha256', 'checkpoint_sha256'):
        journal._digest(value[field])
    if type(value['pending']) is not bool:
        raise ValueError('witness source pending must be boolean')
    for field in ('pending_reservation_id', 'latest_reservation_id'):
        if value[field] is not None:
            _token(value[field])
    if value['pending'] != (value['pending_reservation_id'] is not None):
        raise ValueError('original pending packet has no witness reservation')
    if value['archive_revision'] == 0:
        if value['retired_complete'] != 0 or value['checkpoint_sha256'] != _hash(None):
            raise ValueError('witness original checkpoint differs')
    elif not 0 < value['archive_revision'] <= value['retired_complete'] < value['complete']:
        raise ValueError('witness original checkpoint ordinals differ')
    if value['complete'] == 0 and value['prefix_sha256'] != _hash([]):
        raise ValueError('witness original genesis prefix differs')


def _advance(before, request, identity, at):
    if not isinstance(request, dict) or set(request) != {'action', 'view', 'active_token', 'expected_head_sha256', 'operation_id'}:
        raise ValueError('witness operation request fields differ')
    action = request['action']; view = request['view']; _view(view, identity)
    if action not in {'check', 'reserve', 'cancel'}:
        raise ValueError('witness operation action differs')
    _token(request['operation_id']); journal._digest(request['expected_head_sha256'])
    if request['active_token'] is not None:
        _token(request['active_token'])
    result = deepcopy(before); old = before['point']; reservation = result['reservation']
    if view['complete'] < old['complete']:
        raise ValueError('witness source lifetime regressed: rollback requires recovery')
    if view['prior_prefix_sha256'] != old['prefix_sha256']:
        raise ValueError('witness original prefix differs')
    if view['archive_revision'] < old['archive_revision'] or view['retired_complete'] < old['retired_complete']:
        raise ValueError('witness archive checkpoint regressed')
    if (view['archive_revision'] == old['archive_revision'] and
            (view['retired_complete'] != old['retired_complete'] or view['checkpoint_sha256'] != old['checkpoint_sha256'])):
        raise ValueError('witness same revision checkpoint differs')
    if view['complete'] == old['complete']:
        if view['prefix_sha256'] != old['prefix_sha256']:
            raise ValueError('witness same-count original prefix differs')
    elif (view['complete'] != old['complete'] + 1 or reservation is None or
            reservation['sequence'] != view['complete'] or view['latest_reservation_id'] != reservation['reservation_id'] or view['pending']):
        raise ValueError('witness completion lacks its single original reservation')
    else:
        result['reservation'] = reservation = None
    if reservation is not None:
        if view['pending']:
            if view['pending_reservation_id'] != reservation['reservation_id']:
                raise ValueError('witness original pending reservation differs')
        elif action != 'cancel' or request['active_token'] != reservation['reservation_id']:
            raise ValueError('unresolved reservation without original packet; explicit recovery required')
    elif view['pending']:
        raise ValueError('original pending packet has no current witness reservation')
    result['point'] = {key: view[key] for key in sorted(POINT_FIELDS)}
    if action == 'cancel':
        # This is an explicit recovery decision by a trusted caller, never an
        # automatic expiry or restart path. The token identifies the operation;
        # it is not proof of a live process or independently verified source.
        if view['pending']:
            raise ValueError('cannot cancel a frozen pending packet')
        if reservation is None or request['active_token'] != reservation['reservation_id']:
            raise ValueError('cancel requires the active unfrozen reservation token')
        result['reservation'] = None
    elif action == 'reserve' and reservation is None:
        if view['complete'] >= identity['max_cycles']:
            raise ValueError('witness original cycle bound reached')
        if at >= _time(identity['deadline']):
            raise ValueError('witness original deadline reached')
        result['reservation'] = {'sequence': view['complete'] + 1, 'reservation_id': request['operation_id']}
    return result


def _envelope(meta, event, digest):
    return {'witness_id': meta[0], 'genesis_sha256': meta[1], 'identity_sha256': meta[2],
            'head_sha256': digest, 'sequence': event['sequence'], 'state': event['state']}


def _load(db, binding, identity, check=lambda:None):
    _identity(identity)
    if not isinstance(binding, dict) or set(binding) != {'witness_id', 'genesis_sha256'}:
        raise ValueError('witness binding fields differ')
    _token(binding['witness_id']); journal._digest(binding['genesis_sha256'])
    meta = db.execute('SELECT witness_id,genesis_sha256,identity_sha256 FROM witness_meta WHERE id=1').fetchone()
    if meta != (binding['witness_id'], binding['genesis_sha256'], _hash(identity)):
        raise ValueError('witness binding/identity differs')
    previous = None; head = None; seen = set()
    for ordinal, row in enumerate(db.execute('SELECT sequence,operation_id,payload,sha256 FROM witness_events ORDER BY sequence')):
        check()
        if ordinal >= MAX_EVENTS or row[0] != ordinal or not isinstance(row[2], str) or len(row[2].encode()) > MAX_EVENT_BYTES:
            raise ValueError('witness event chain exceeds bounds or has a gap')
        event = json.loads(row[2])
        if (not isinstance(event, dict) or set(event) != EVENT_FIELDS or type(event['schema_version']) is not int or
                event['schema_version'] != 1 or event['purpose'] != PURPOSE or type(event['sequence']) is not int or
                event['sequence'] != ordinal or event['identity_sha256'] != _hash(identity) or
                event['operation_id'] != row[1] or event['operation_id'] in seen or
                event['previous_sha256'] != previous or _hash(event) != row[3] or _json(event) != row[2]):
            raise ValueError('witness event checksum/chain differs')
        _token(event['operation_id']); at = _time(event['at'])
        if ordinal == 0:
            if (row[3] != binding['genesis_sha256'] or event['operation_id'] != binding['witness_id'] or
                    event['request'] is not None or _hash(event['state']) != _hash(_genesis_state())):
                raise ValueError('witness genesis differs')
        else:
            if at < _time(head['at']) or event['request']['expected_head_sha256'] != previous:
                raise ValueError('witness event time/head chain differs')
            expected = _advance(head['state'], event['request'], identity, at)
            if event['request']['operation_id'] != event['operation_id'] or _hash(expected) != _hash(event['state']):
                raise ValueError('witness historical state transition differs')
        # Keep duplicate-ID evidence, not every decoded historical request/state.
        seen.add(event['operation_id'])
        previous = row[3]; head = event
    if head is None:
        raise ValueError('witness genesis is missing')
    return _envelope(meta, head, previous), {}, head


def initialize(path, identity, *, source_root, enabled=False, now=None):
    _enabled(enabled); _identity(identity); path = _root(path); source_root = _root(source_root)
    if source_root == path or source_root in path.parents or path in source_root.parents:
        raise ValueError('witness and worker source must be separate')
    if path.exists():
        raise ValueError('existing witness cannot be reinitialized')
    if not path.parent.is_dir() or not source_root.is_dir():
        raise ValueError('witness requires existing source and receiver parent directories')
    witness_id = uuid.uuid4().hex
    event = {'schema_version': 1, 'purpose': PURPOSE, 'sequence': 0, 'previous_sha256': None,
        'operation_id': witness_id, 'identity_sha256': _hash(identity), 'at': _now(now).isoformat(),
        'request': None, 'state': _genesis_state()}
    digest = _hash(event)
    # Reserve the exact new pathname atomically; failure never overwrites one.
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError('existing witness cannot be reinitialized') from error
    os.close(descriptor)
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('CREATE TABLE witness_meta(id INTEGER PRIMARY KEY CHECK(id=1),witness_id TEXT,genesis_sha256 TEXT,identity_sha256 TEXT)')
            db.execute('CREATE TABLE witness_events(sequence INTEGER PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,sha256 TEXT NOT NULL)')
            db.execute('INSERT INTO witness_meta VALUES(1,?,?,?)', (witness_id, digest, _hash(identity)))
            db.execute('INSERT INTO witness_events VALUES(0,?,?,?)', (witness_id, _json(event), digest)); db.commit()
        except BaseException:
            db.rollback(); raise
    return {'witness_id': witness_id, 'genesis_sha256': digest}


def read(path, binding, identity, *, enabled=False, max_bytes=MAX_WITNESS_BYTES,
         timeout_seconds=10, clock=time.monotonic):
    _enabled(enabled); path = _root(path)
    budget=_ReceiverBudget(path,max_bytes,timeout_seconds,clock)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
        try:
            budget.attach(db);db.execute('BEGIN')
            result=_load(db,binding,identity,budget.check_time)[0]
            budget.check();return result
        except sqlite3.Error as error:budget.sql_error(error)


def _transact(db, head, prior, request, identity, at, budget, *, fault=None, precommit=None):
    """One identical append/retry transition for stateless and owned receivers."""
    operation_id=request['operation_id']
    found=db.execute('SELECT sequence,payload,sha256 FROM witness_events WHERE operation_id=?',(operation_id,)).fetchone()
    if found is not None:
        event=json.loads(found[1])
        if (type(found[0]) is not int or not 0 <= found[0] <= head['sequence'] or
                event['sequence']!=found[0] or event['operation_id']!=operation_id or
                _hash(event)!=found[2] or _json(event)!=found[1] or _hash(event['request'])!=_hash(request)):
            raise ValueError('witness operation request differs')
        budget.check();db.commit();return head,prior
    if head['head_sha256']!=request['expected_head_sha256']:
        raise ValueError('witness head changed; reconcile original source first')
    if at < _time(prior['at']) or head['sequence']+1 >= MAX_EVENTS:
        raise ValueError('witness time/event bound differs')
    state=_advance(head['state'],request,identity,at)
    if request['action']=='check' and _hash(state)==_hash(head['state']):
        budget.check();db.commit();return head,prior
    event={'schema_version':1,'purpose':PURPOSE,'sequence':head['sequence']+1,
        'previous_sha256':head['head_sha256'],'operation_id':operation_id,'identity_sha256':_hash(identity),
        'at':at.isoformat(),'request':request,'state':state}
    raw=_json(event)
    if len(raw.encode()) > MAX_EVENT_BYTES:raise ValueError('witness event exceeds byte bound')
    digest=_hash(event);budget.write_ceiling(db)
    db.execute('INSERT INTO witness_events VALUES(?,?,?,?)',(event['sequence'],operation_id,raw,digest))
    if fault:fault('before_commit')
    if precommit:precommit()
    budget.check();db.commit()
    if fault:fault('after_commit')
    result=dict(head,head_sha256=digest,sequence=event['sequence'],state=state)
    return result,event


def apply(path, binding, identity, view, *, action='check', expected_head_sha256,
          active_token=None, operation_id=None, enabled=False, now=None, fault=None,
          max_bytes=MAX_WITNESS_BYTES, timeout_seconds=10, clock=time.monotonic):
    _enabled(enabled); journal._digest(expected_head_sha256)
    operation_id = uuid.uuid4().hex if operation_id is None else _token(operation_id)
    request = {'action': action, 'view': view, 'active_token': active_token,
               'expected_head_sha256': expected_head_sha256, 'operation_id': operation_id}
    path = _root(path); at = _now(now)
    budget=_ReceiverBudget(path,max_bytes,timeout_seconds,clock)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=0, isolation_level=None)) as db:
        try:
            budget.attach(db)
            db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
            head, _, prior = _load(db,binding,identity,budget.check_time)
            return _transact(db,head,prior,request,identity,at,budget,fault=fault)[0]
        except BaseException as error:
            # Remove an expired progress callback before rollback itself.
            db.set_progress_handler(None,0);db.rollback()
            if isinstance(error,sqlite3.Error):budget.sql_error(error)
            raise
