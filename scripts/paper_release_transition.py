"""Explicit operator correction on supplied closed copies; never starts a writer.

Supported deltas are the reviewed backup correction, paper reporting correction,
or supervisor recovery/reporting correction from their pinned predecessors.
No transport, provider, scheduling, deployment or experiment initialization is
performed here. Inspection is the default; apply requires an explicit flag and
approval reference. Live use still requires separate exact operator approval.
"""
import argparse
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath, PurePosixPath
import re
import sqlite3
import sys
import tempfile
import time

from hermes_trading.archive_snapshot import _safe_root
from hermes_trading.paired_inception import read_paired_inception
from hermes_trading.paired_primary import resolve_primary, validate_primary_limits
from hermes_trading.reflection_supervision import _hash, _json, _time, load_owner_policy
from hermes_trading.paper_publication import _temporary, _sync_dir

BASE_COMMIT = '6c9d520da5f8473b51c912d1eb0e269ee94d4b1b'
REPORTING_BASE_COMMIT = '49c851315b98bb33b1eb78180fb789afe2a49f3d'
REPORTING_MODULES = {'notifications.py','paired_paper.py','paired_worker.py','paper_boot.py'}
SNAPSHOT_BASE_COMMIT = 'ed439e897a4dd388fdcf6425e7e8b6171d5a88a9'
SNAPSHOT_BASE_RELEASE = 'fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844'
SNAPSHOT_BASE_RUNTIME = '0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c'
RECOVERY_BASE_COMMIT = '20eb6af2a441c934618e124af30d72f411a3938a'
RECOVERY_BASE_RELEASE = 'b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7'
RECOVERY_BASE_RUNTIME = '38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2'
RECOVERY_MODULES = {'notifications.py','paper_safety.py','paper_supervisor_health.py','supervisor_sync.py'}
RELIABILITY_BASE_COMMIT = 'ea752c5f3990acff2a6f7164be733fe2bf92df35'
RELIABILITY_BASE_RELEASE = 'd8289ff44d543bebca1df87a566335f05366607ffc767d05eb00b75c6eddcf04'
RELIABILITY_BASE_RUNTIME = 'ffe95446f146d79441862a3eae14228865fd7bf460fc7c7311f5ead3510834eb'
TARGET = dict(project='f78f42d9-20ec-44f6-ae3f-f8bb79ee7000',
              service='8b94cbc9-293e-4222-a8a7-698b656985f7',
              environment='fdcc71a3-00a9-410c-8d6f-59af9bc98d36')
PURPOSE = 'paper_backup_plus_release_transition_v1'
REPORTING_PURPOSE = 'paper_discord_release_transition_v1'
SNAPSHOT_PURPOSE = 'paper_snapshot_busy_release_transition_v1'
RECOVERY_PURPOSE = 'paper_supervisor_recovery_release_transition_v1'
RELIABILITY_PURPOSE = 'paper_supervisor_reliability_release_transition_v1'
NAMES = {f'{arm}_{wallet}' for arm in ('adaptive', 'control') for wallet in (50, 100, 150, 200)}
COMPONENTS = (
    ('worker_renewal', 'worker', 'adaptive_200/model_access_refresh.sqlite', 'binding'),
    ('worker_usage', 'worker', 'adaptive_200/supervisor_usage_feed.sqlite', 'binding'),
    ('worker_limits', 'worker', 'adaptive_200/supervisor_limits.json', 'release_hash'),
    ('pc_limits', 'supervisor', 'supervisor_limits.json', 'release_hash'),
    ('pc_lifecycle', 'supervisor', 'supervisor_lifecycle.json', 'binding'),
    ('pc_launch', 'launch', '', 'release_hash'),
)
_DEADLINE = ContextVar('paper_transition_deadline', default=None)


def _check():
    deadline = _DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('transition operation exceeded thirty seconds')


def _bounded(function):
    @wraps(function)
    def call(*args, **kwargs):
        current = time.monotonic(); outer = _DEADLINE.get()
        token = _DEADLINE.set(min(current+30, outer) if outer is not None else current+30)
        try: return function(*args, **kwargs)
        finally: _DEADLINE.reset(token)
    return call


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate transition metadata key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite metadata')))
    if not isinstance(value, dict): raise ValueError('transition metadata object required')
    return value


def _read(path):
    _check()
    path = _safe_root(path)
    if not path.is_file() or path.stat().st_size > 4*1024**2:
        raise ValueError('transition metadata missing or exceeds bound')
    return _decode(path.read_bytes())


def _save(path, value):
    _check()
    path = _safe_root(path)
    temporary = _temporary(path, (_json(value)+'\n').encode())
    try: os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)
    _sync_dir(path.parent)


def _layout(worker_root, supervisor_root, launch_file, audit_dir=None):
    result = {key: _safe_root(Path(value).absolute()) for key, value in
              (('worker', worker_root), ('supervisor', supervisor_root), ('launch', launch_file))}
    roots = (result['worker'], result['supervisor'])
    if any(not root.is_dir() for root in roots) or not result['launch'].is_file():
        raise ValueError('existing closed worker/supervisor copies and launch required')
    if roots[0] == roots[1] or any(left in right.parents for left, right in (roots, roots[::-1])):
        raise ValueError('worker and supervisor copies must be separate')
    if any(result['launch'].is_relative_to(root) for root in roots):
        raise ValueError('launch must remain separate from state copies')
    if audit_dir is not None:
        result['audit'] = _safe_root(Path(audit_dir).absolute())
        if any(result['audit'] == root or result['audit'].is_relative_to(root) or
               root.is_relative_to(result['audit']) for root in (*roots, result['launch'])):
            raise ValueError('transition audit must be outside all inputs')
    return result


def _path(layout, component):
    return layout['launch'] if component['role'] == 'launch' else layout[component['role']]/component['path']


@contextmanager
def _database(path, connections=None):
    if connections is not None and path in connections:
        yield connections[path]
    else:
        # Only admitted sidecar-free closed copies use immutable reads. Ordinary
        # mode=ro may create WAL/SHM files and would mutate default inspection.
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True, timeout=0)) as db:
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            yield db


def _members(layout):
    result = {}
    for role in ('worker', 'supervisor'):
        root = layout[role]
        for path in root.rglob('*'):
            _check()
            path = _safe_root(path)
            if len(result) >= 8192: raise ValueError('transition file budget exceeded')
            name = role+'/'+path.relative_to(root).as_posix()
            if path.is_dir():
                result[name+'/'] = path
            elif path.name.endswith(('-wal', '-shm', '-journal')):
                base = path.with_name(path.name.rsplit('-', 1)[0])
                if not base.is_file() or base.suffix not in ('.db', '.sqlite'):
                    raise ValueError('orphan transition SQLite sidecar')
            else:
                result[name] = path
    result['launch'] = layout['launch']
    if sum(p.stat().st_size for key,p in result.items() if not key.endswith('/')) > 1_000_000_000:
        raise ValueError('transition byte budget exceeded')
    return result


def _closed_inputs(layout):
    for role in ('worker','supervisor'):
        for path in layout[role].rglob('*'):
            _check(); _safe_root(path)
            if path.is_file() and path.name.endswith(('-wal','-shm','-journal')):
                raise ValueError('sidecar-free closed copies required before immutable inspection')


def _quote(name):
    return '"'+name.replace('"', '""')+'"'


def _database_hash(db, *, mask_binding=False):
    schema = list(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    tables = {}
    for kind, name, _, _ in schema:
        _check()
        if kind != 'table': continue
        rows = []
        for row in db.execute('SELECT * FROM '+_quote(name)):
            _check()
            if len(rows) >= 1_000_000: raise ValueError('transition row budget exceeded')
            row = list(row)
            if mask_binding and name == 'binding':
                if len(row) != 2 or row[0] != 1: raise ValueError('unexpected release binding row')
                body = _decode(row[1]); body['release'] = '<release>'; row[1] = _json(body)
            encoded = [{'blob':value.hex()} if isinstance(value, bytes) else value for value in row]
            rows.append(_hash(encoded))
        tables[name] = sorted(rows)
    return _hash({'schema':schema, 'tables':tables})


def _protected(layout, connections=None):
    result = {}
    masks = {('worker/adaptive_200/model_access_refresh.sqlite'): 'database',
             ('worker/adaptive_200/supervisor_usage_feed.sqlite'): 'database',
             ('worker/adaptive_200/supervisor_limits.json'): 'release_hash',
             ('supervisor/supervisor_limits.json'): 'release_hash',
             ('supervisor/supervisor_lifecycle.json'): 'binding', 'launch': 'release_hash'}
    for name, path in sorted(_members(layout).items()):
        if name.endswith('/'): result[name] = 'directory'; continue
        if path.suffix in ('.db', '.sqlite'):
            with _database(path, connections) as db:
                result[name] = _database_hash(db, mask_binding=masks.get(name)=='database')
        elif name in masks:
            body = _read(path); body[masks[name]] = '<release>'; result[name] = _hash(body)
        else:
            result[name] = _sha(path.read_bytes())
    return result


def _pending(layout, connections=None):
    for role in ('worker', 'supervisor'):
        if any(layout[role].rglob('paper_supervisor_health_pending.json')):
            raise ValueError('pending supervisor delivery requires reconciliation')
    for _, path in _members(layout).items():
        if not path.is_file() or path.suffix not in ('.db', '.sqlite'): continue
        with _database(path, connections) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'paired_batches' in tables and db.execute("SELECT 1 FROM paired_batches WHERE status!='complete' LIMIT 1").fetchone():
                raise ValueError('pending shared batch requires original release')
            if 'publications' in tables and db.execute('SELECT 1 FROM publications WHERE receipt IS NULL LIMIT 1').fetchone():
                raise ValueError('pending publication requires reconciliation')
            if 'supervisor' in tables:
                row = db.execute('SELECT state FROM supervisor WHERE id=1').fetchone()
                if row is None or _decode(row[0]).get('pending_review') is not None:
                    raise ValueError('unresolved inference/review requires reconciliation')
            if 'reviews' in tables:
                for record, in db.execute('SELECT record FROM reviews'):
                    if _decode(record).get('outcome') not in {'changed','no_change','rejected','failed','deferred'}:
                        raise ValueError('unresolved review audit requires reconciliation')


def _economics(layout, manifest, policy, opening, accounts):
    from hermes_trading.paired_packet_storage import load_packet
    from hermes_trading.paired_paper import _encode, _load_yaml
    from hermes_trading.paired_storage_admission import validate_storage_limits
    from hermes_trading.paper_safety import load_policy
    root = layout['worker']
    with _database(root/'worker_runtime.sqlite') as db:
        row = db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()
    if row is None: raise ValueError('original worker lifecycle required')
    configuration = dict(manifest=manifest, limits=_read(root/'worker_limits.json'),
        storage=validate_storage_limits(_read(root/'storage_limits.json'),policy['experiment_id']),
        policy=policy, accounts=accounts, opening_receipt_hash=_hash(opening))
    safety = load_policy(root,policy['experiment_id'])
    if safety is not None: configuration['paper_safety'] = safety
    if _decode(row[0]).get('configuration_hash') != _hash(configuration):
        raise ValueError('original worker limits/storage/safety configuration changed')
    with _database(root/'adaptive_200/paper_publications.sqlite') as db:
        row = db.execute("SELECT payload FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None: raise ValueError('original completed common batch required')
        packet = load_packet(db,row[0])
    for profile in manifest['profiles']:
        name = profile['directory']; path = root/name
        # This one-time correction targets the original, still-unsteered beta.
        if _sha((path/'strategy.yaml').read_bytes()) != profile['strategy_sha256']:
            raise ValueError('original frozen strategy economics changed')
        if 'goal_sha256' in profile and _sha((path/'goal.yaml').read_bytes()) != profile['goal_sha256']:
            raise ValueError('original frozen goal changed')
        if packet['bindings'][name]['goal_hash'] != _hash(_encode(_load_yaml(path/'goal.yaml'))):
            raise ValueError('goal differs from original common-batch binding')


def _identity(layout, *, check_economics=False):
    root, supervisor = layout['worker'], layout['supervisor']
    manifest = _read(root/'manifest.json'); primary = resolve_primary(manifest)
    if not primary.versioned or primary.wallet_eur != 200:
        raise ValueError('original versioned primary must remain EUR200')
    @contextmanager
    def reader(path, *, check):
        check()
        with _database(path) as db: yield db
    opening = read_paired_inception(root, database_factory=reader, check=_check)
    policy = load_owner_policy(root/'adaptive_200')
    if policy is None or policy.get('owner') != 'external_llm' or _read(supervisor/'reflection_owner.json') != policy:
        raise ValueError('original worker and supervisor owner differ')
    accounts = {}; configurations = {}
    for profile in manifest['profiles']:
        name = f"{profile['arm']}_{profile['wallet_eur']}"
        if name not in NAMES or profile['directory'] != name or name in accounts:
            raise ValueError('exact original eight account directories required')
        account = _read(root/name/'wallet_account.json')
        if account['experiment_id'] != policy['experiment_id']: raise ValueError('account experiment differs')
        accounts[name] = _hash(account)
        configurations[name] = account
    if set(accounts) != NAMES or _hash(_read(supervisor/'wallet_account.json')) != accounts['adaptive_200']:
        raise ValueError('all eight original accounts and primary mirror required')
    if check_economics: _economics(layout,manifest,policy,opening,configurations)
    return dict(experiment_id=policy['experiment_id'], manifest_sha256=_sha((root/'manifest.json').read_bytes()),
                opening_hash=_hash(opening), policy_hash=_hash(policy), account_hashes=accounts)


def _importer_schema(db, name):
    # Use the exact native CREATE statements, including SQLite's own indexes.
    # Table names alone admit extensions that break positional native inserts.
    statements = ['CREATE TABLE binding (id INTEGER PRIMARY KEY,payload TEXT)']
    if name == 'worker_renewal':
        statements += ['CREATE TABLE renewals (at TEXT PRIMARY KEY,access_hash TEXT,receipt TEXT)']
    else:
        statements += ['CREATE TABLE events (event_id TEXT PRIMARY KEY,payload TEXT)',
                       'CREATE TABLE snapshots (at TEXT PRIMARY KEY,source_hash TEXT,receipt TEXT)']
    query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
    with closing(sqlite3.connect(':memory:')) as expected:
        for statement in statements: expected.execute(statement)
        if list(db.execute(query)) != list(expected.execute(query)):
            raise ValueError('importer schema differs from original native ledger')


def _static_supervisor(layout, release_plan, originals=None):
    """Check retained identity without resolving a foreign host's path strings."""
    from hermes_trading.supervisor_launch import launch_primary
    manifest = _read(layout['worker']/'manifest.json'); primary = resolve_primary(manifest)
    policy = _read(layout['supervisor']/'reflection_owner.json')
    account = _read(layout['supervisor']/'wallet_account.json')
    limits = originals['pc_limits'] if originals else _read(layout['supervisor']/'supervisor_limits.json')
    worker_limits = originals['worker_limits'] if originals else _read(layout['worker']/'adaptive_200/supervisor_limits.json')
    if worker_limits != limits: raise ValueError('original supervisor limit copies differ')
    if (limits.get('experiment_id') != policy['experiment_id'] or limits.get('policy_hash') != _hash(policy)
            or limits.get('release_hash') != _hash(release_plan['old'])):
        raise ValueError('supervisor limit identity differs')
    validate_primary_limits(limits,primary,_hash(account))
    seconds = limits.get('max_runtime_seconds')
    if type(seconds) is not int or not 1 <= seconds <= 180*86400:
        raise ValueError('finite original supervisor lifetime required')
    if _time(limits.get('activated_at')) != _time(account['activated_at']):
        raise ValueError('original supervisor activation differs')
    launch = originals['pc_launch'] if originals else _read(layout['launch'])
    if (launch.get('status') != 'activated' or launch.get('enabled') is not True or launch_primary(launch) != primary
            or launch.get('experiment_id') != policy['experiment_id'] or launch.get('account_hash') != _hash(account)
            or launch.get('policy_hash') != _hash(policy) or launch.get('release_hash') != _hash(release_plan['old'])):
        raise ValueError('original launch identity differs')
    if _time(launch.get('expires_at')) != _time(account['activated_at'])+timedelta(seconds=seconds):
        raise ValueError('original launch deadline differs')
    # Reference copies retain original host paths; enforce their structural
    # relationships with pure paths, never treating them as local authorities.
    def foreign_path(key):
        value=launch.get(key)
        if not isinstance(value,str):raise ValueError('absolute original launch path required')
        path = PureWindowsPath(value) if '\\' in value or re.match(r'^[A-Za-z]:',value) else PurePosixPath(value)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('absolute original launch path required')
        return path
    base=foreign_path('project_dir')/'state/paired-supervisor'/policy['experiment_id']
    if (foreign_path('local_state_dir') != base/primary.profile_key('adaptive') or
            foreign_path('mirror_dir') != base/'mirror' or
            launch.get('remote_state_dir') != '/app/state/'+primary.profile_key('adaptive')):
        raise ValueError('original launch targets differ')


def _component_specs(layout, release_plan):
    old, new = release_plan['old'], release_plan['new']
    policy = _read(layout['supervisor']/'reflection_owner.json')
    limits = _read(layout['supervisor']/'supervisor_limits.json')
    changed_limits = dict(limits, release_hash=_hash(new))
    components = []
    for name, role, relative, field in COMPONENTS:
        component = dict(name=name, role=role, path=relative, field=field,
                         kind='database' if field=='binding' and name.startswith('worker_') else 'json')
        path = _path(layout, component)
        if component['kind'] == 'database':
            with _database(path) as db:
                _importer_schema(db,name)
                rows = db.execute('SELECT id,payload FROM binding').fetchall()
            if len(rows) != 1 or rows[0][0] != 1: raise ValueError('existing single importer binding required')
            before = rows[0][1]; body = _decode(before)
            keys = {'account','policy','release'} | ({'provenance'} if name=='worker_renewal' else set())
            if set(body) != keys or body['account'] != _hash(_read(layout['worker']/'adaptive_200/wallet_account.json')) or body['policy'] != _hash(policy):
                raise ValueError('importer account/owner binding differs')
            if body['release'] != _hash(old['runtime']): raise ValueError('importer old release differs')
            if name == 'worker_renewal' and body['provenance'] != _read(layout['worker']/'adaptive_200/model_access.json')['provenance_ref']:
                raise ValueError('original renewal provenance differs')
            after = _json(dict(body, release=_hash(new['runtime'])))
        else:
            before = path.read_bytes().decode('utf-8'); body = _decode(before)
            expected = _hash({'limits':limits,'policy':policy,'release':old}) if name=='pc_lifecycle' else _hash(old)
            if body.get(field) != expected: raise ValueError('old supervisor release binding differs')
            value = _hash({'limits':changed_limits,'policy':policy,'release':new}) if name=='pc_lifecycle' else _hash(new)
            after = _json(dict(body, **{field:value}))+'\n'
        component.update(before=before, after=after, before_hash=_sha(before.encode()), after_hash=_sha(after.encode()))
        components.append(component)
    return components


@_bounded
def prepare_plan(worker_root, supervisor_root, launch_file, *, release_plan, expected_identity):
    validate_release_plan(release_plan)
    layout = _layout(worker_root, supervisor_root, launch_file)
    _closed_inputs(layout)
    _pending(layout)
    actual = _identity(layout,check_economics=True)
    if actual != expected_identity or actual['experiment_id'] != release_plan['target']['experiment_id']:
        raise ValueError('original opening/manifest/accounts/owner identity differs')
    _static_supervisor(layout,release_plan)
    protected = _protected(layout)
    body = dict(schema_version=1, purpose=_purpose(release_plan), release_plan=deepcopy(release_plan), identity=actual,
                components=_component_specs(layout, release_plan), protected=protected)
    if _protected(layout) != protected: raise ValueError('source changed during plan inspection')
    return dict(body, transition_id=_hash(body))


def _validate_plan(plan, layout):
    if not isinstance(plan, dict) or set(plan) != {'schema_version','purpose','release_plan','identity','components','protected','transition_id'}:
        raise ValueError('exact frozen transition plan required')
    validate_release_plan(plan['release_plan'])
    if type(plan['schema_version']) is not int or plan['schema_version'] != 1 or plan['purpose'] != _purpose(plan['release_plan']):
        raise ValueError('transition schema/purpose differs')
    if plan['transition_id'] != _hash({k:v for k,v in plan.items() if k!='transition_id'}):
        raise ValueError('transition plan hash differs')
    if _identity(layout) != plan['identity']: raise ValueError('original experiment identity changed')
    components = plan['components']
    if not isinstance(components, list) or len(components) != len(COMPONENTS):
        raise ValueError('exact correction components required')
    old, new = plan['release_plan']['old'], plan['release_plan']['new']
    old_limits = _decode(next(c['before'] for c in components if c['name']=='pc_limits'))
    policy = _read(layout['supervisor']/'reflection_owner.json')
    for component, (name,role,path,field) in zip(components, COMPONENTS):
        kind = 'database' if field=='binding' and name.startswith('worker_') else 'json'
        if set(component) != {'name','role','path','field','kind','before','after','before_hash','after_hash'} or component['kind'] != kind:
            raise ValueError('component representation differs')
        if (component.get('name'),component.get('role'),component.get('path'),component.get('field')) != (name,role,path,field):
            raise ValueError('transition component escaped fixed correction scope')
        for side in ('before','after'):
            if not isinstance(component.get(side), str) or _sha(component[side].encode()) != component.get(side+'_hash'):
                raise ValueError('component bytes/hash differ')
        before, after = _decode(component['before']), _decode(component['after'])
        binding_field = 'release' if kind == 'database' else field
        if {k:v for k,v in before.items() if k!=binding_field} != {k:v for k,v in after.items() if k!=binding_field}:
            raise ValueError('transition attempts to change non-release state')
        if kind == 'database':
            with _database(_path(layout,component)) as db: _importer_schema(db,name)
            expected_before, expected_after = _hash(old['runtime']), _hash(new['runtime'])
            if before.get('account') != plan['identity']['account_hashes']['adaptive_200'] or before.get('policy') != _hash(policy):
                raise ValueError('original importer owner/account differs')
        elif name == 'pc_lifecycle':
            expected_before = _hash({'limits':old_limits,'policy':policy,'release':old})
            expected_after = _hash({'limits':dict(old_limits,release_hash=_hash(new)),'policy':policy,'release':new})
        else:
            expected_before, expected_after = _hash(old), _hash(new)
        if before.get(binding_field) != expected_before or after.get(binding_field) != expected_after:
            raise ValueError('component does not implement exact old/new release')
    _static_supervisor(layout,plan['release_plan'],{c['name']:_decode(c['before']) for c in components})


def _component_state(layout, component, connections=None):
    path = _path(layout, component)
    if component['kind'] == 'database':
        with _database(path, connections) as db:
            row = db.execute('SELECT payload FROM binding WHERE id=1').fetchone()
        value = None if row is None else row[0]
    else: value = path.read_bytes().decode('utf-8')
    return 'before' if value == component['before'] else 'after' if value == component['after'] else 'conflict'


@_bounded
def inspect_transition(plan, *, worker_root, supervisor_root, launch_file, audit_dir):
    layout = _layout(worker_root, supervisor_root, launch_file, audit_dir)
    _closed_inputs(layout)
    _validate_plan(plan, layout); _pending(layout)
    if _protected(layout) != plan['protected']:
        raise ValueError('non-binding state changed; reconciliation required')
    result = dict(status='inspected', transition_id=plan['transition_id'],
                  components=[dict(name=c['name'],state=_component_state(layout,c)) for c in plan['components']],
                  writer_quiescence='not_established_until_apply')
    if any(c['state']=='conflict' for c in result['components']): raise ValueError('component state conflicts with frozen plan')
    return result


@contextmanager
def _freeze(layout):
    """Existing real SQLite leases, including importers outside the cycle."""
    paths = sorted({path for path in _members(layout).values()
                    if path.is_file() and path.suffix in ('.db', '.sqlite')})
    if layout['worker']/'worker_runtime.sqlite' not in paths or layout['supervisor']/'watcher_cycle.sqlite' not in paths:
        raise ValueError('native worker and cycle ownership databases required')
    with ExitStack() as scope:
        connections = {}
        for path in paths:
            _check()
            db = scope.enter_context(closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True,
                                                           timeout=0, isolation_level=None)))
            db.execute('PRAGMA locking_mode=EXCLUSIVE')
            db.execute('BEGIN EXCLUSIVE'); db.commit()
            connections[path] = db
        yield connections


def _root_binding(layout):
    return _hash({k:str(layout[k]) for k in ('worker','supervisor','launch')})


def _intent(plan, layout):
    intent = _read(layout['audit']/'intent.json')
    if intent.get('transition_id') != plan['transition_id'] or intent.get('root_binding') != _root_binding(layout):
        raise ValueError('existing audit belongs to a different transition or copies')
    if intent.get('intent_hash') != _hash({k:v for k,v in intent.items() if k!='intent_hash'}):
        raise ValueError('transition intent changed')
    if intent.get('plan') != plan:
        raise ValueError('audit frozen plan differs')
    return intent


def _prepare_audit(plan, layout, connections, approval_ref):
    audit = layout['audit']
    if audit.exists(): raise ValueError('existing audit requires explicit reconciliation')
    audit.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.'+audit.name+'-prepare-', dir=audit.parent))
    copied = dict(worker=stage/'rollback/worker', supervisor=stage/'rollback/supervisor',
                  launch=stage/'rollback/launch.json')
    for role in ('worker','supervisor'): copied[role].mkdir(parents=True)
    for name, path in sorted(_members(layout).items()):
        _check()
        if name == 'launch': target = copied['launch']
        else:
            role, relative = name.split('/',1); target = copied[role]/relative.rstrip('/')
        if name.endswith('/'):
            target.mkdir(parents=True, exist_ok=True); continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path in connections:
            with closing(sqlite3.connect(target)) as db:
                connections[path].backup(db, pages=64, progress=lambda *args:_check())
                db.execute('PRAGMA journal_mode=DELETE'); db.commit()
        else:
            target.write_bytes(path.read_bytes())
        # Flush the complete original copy before publishing any transition
        # intent. SQLite backup commits and plain files share this barrier.
        # Windows FlushFileBuffers needs a writable handle; this is the owned
        # rollback copy, never the input evidence.
        with target.open('r+b') as copied_file: os.fsync(copied_file.fileno())
    for directory in sorted((p for p in (stage/'rollback').rglob('*') if p.is_dir()),reverse=True):
        _sync_dir(directory)
    _sync_dir(stage/'rollback')
    if _protected(copied) != plan['protected']:
        raise ValueError('consistent rollback copy differs from frozen inputs')
    if any(_component_state(copied,c) != 'before' for c in plan['components']):
        raise ValueError('rollback component does not match original bytes')
    body = dict(schema_version=1, transition_id=plan['transition_id'], plan=plan,
                root_binding=_root_binding(layout), approval_ref=approval_ref,
                prepared_at=datetime.now(timezone.utc).isoformat(), rollback_protected_hash=_hash(plan['protected']))
    _save(stage/'intent.json', dict(body,intent_hash=_hash(body)))
    (stage/'components').mkdir(); _sync_dir(stage)
    # This is the first durable intent; no state component has been changed.
    if audit.exists(): raise ValueError('audit appeared during preparation')
    os.replace(stage, audit); _sync_dir(audit.parent)


def _verify_rollback(plan, layout):
    audit = layout['audit']
    copied = dict(worker=audit/'rollback/worker', supervisor=audit/'rollback/supervisor',
                  launch=audit/'rollback/launch.json')
    _closed_inputs(copied)
    if _protected(copied) != plan['protected'] or any(_component_state(copied,c) != 'before' for c in plan['components']):
        raise ValueError('original consistent rollback evidence changed')


def _change(layout, component, connections, *, reverse=False):
    _check()
    source, target = ('after','before') if reverse else ('before','after')
    path = _path(layout, component)
    state = _component_state(layout, component, connections)
    if state == target: return False
    if state != source: raise ValueError('component changed before checked write')
    if component['kind'] == 'database':
        db = connections[path]
        db.execute('BEGIN EXCLUSIVE')
        try:
            changed = db.execute('UPDATE binding SET payload=? WHERE id=1 AND payload=?',
                                 (component[target], component[source]))
            if changed.rowcount != 1: raise ValueError('release binding compare-and-set failed')
            db.commit()
        except BaseException:
            db.rollback(); raise
    else:
        temporary = _temporary(path, component[target].encode())
        try: os.replace(temporary, path)
        finally: temporary.unlink(missing_ok=True)
        _sync_dir(path.parent)
    if _component_state(layout,component,connections) != target:
        raise ValueError('component commit outcome requires reconciliation')
    return True


def _approval(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('explicit exact-transition approval reference required')


def _validate_component_receipt(value,plan,layout,component,reverse):
    if value.get('transition_id') != plan['transition_id'] or value.get('name') != component['name']:
        raise ValueError('component receipt conflicts with exact transition')
    if (value.get('before_hash'), value.get('after_hash')) != (component['before_hash'],component['after_hash']):
        raise ValueError('component receipt hash differs')
    if value.get('root_binding') != _root_binding(layout) or value.get('reverse') is not reverse:
        raise ValueError('component receipt target/direction differs')


def _preflight(plan,layout,connections=None,*,reverse=False):
    states={c['name']:_component_state(layout,c,connections) for c in plan['components']}
    if 'conflict' in states.values():raise ValueError('component set conflicts before any checked write')
    components={c['name']:c for c in plan['components']}
    for direction,folder in ((False,'components'),(True,'rollback-components')):
        directory=layout['audit']/folder
        if directory.exists():
            for path in directory.iterdir():
                _check()
                if path.name not in {name+'.json' for name in components}:
                    raise ValueError('unknown transition component receipt')
                component=components[path.stem]
                _validate_component_receipt(_read(path),plan,layout,component,direction)
                if direction and not reverse:raise ValueError('reverse work requires rollback reconciliation')
                if direction == reverse and states[path.stem] != ('before' if reverse else 'after'):
                    raise ValueError('component receipt contradicts retained state')
    for direction,filename,status in ((False,'receipt.json','completed'),(True,'rollback-receipt.json','rolled_back')):
        path=layout['audit']/filename
        if not path.exists():continue
        receipt=_read(path)
        if (receipt.get('transition_id') != plan['transition_id'] or receipt.get('root_binding') != _root_binding(layout)
                or receipt.get('status') != status):raise ValueError('completion receipt differs')
        rows=receipt.get('components')
        names=list(components); expected=list(reversed(names)) if direction else names
        if not isinstance(rows,list) or [r.get('name') for r in rows] != expected:
            raise ValueError('completion component inventory differs')
        for row in rows:_validate_component_receipt(row,plan,layout,components[row['name']],direction)
        if direction and not reverse:raise ValueError('completed rollback requires a new transition audit')


def _component_receipt(plan, layout, component, *, recovered=False, reverse=False):
    folder = layout['audit']/('rollback-components' if reverse else 'components')
    folder.mkdir(exist_ok=True)
    path = folder/(component['name']+'.json')
    if path.exists():
        value = _read(path)
        _validate_component_receipt(value,plan,layout,component,reverse)
        return value
    value = dict(transition_id=plan['transition_id'], name=component['name'], reverse=reverse,
                 root_binding=_root_binding(layout),
                 before_hash=component['before_hash'], after_hash=component['after_hash'],
                 observed_at=datetime.now(timezone.utc).isoformat(), recovered_after_unknown_reply=recovered)
    _save(path, value); return value


@_bounded
def apply_transition(plan, *, worker_root, supervisor_root, launch_file, audit_dir,
                     approval_ref=None, apply=False, reconcile=False, fault=None):
    options = dict(worker_root=worker_root, supervisor_root=supervisor_root,
                   launch_file=launch_file, audit_dir=audit_dir)
    if apply is not True: return inspect_transition(plan, **options)
    _approval(approval_ref)
    layout = _layout(**options); _closed_inputs(layout); _validate_plan(plan,layout)
    if (layout['audit']/'rollback-receipt.json').exists():
        raise ValueError('completed rollback requires a new transition audit')
    existed = layout['audit'].exists()
    if existed:
        _intent(plan,layout)
        complete = layout['audit']/'receipt.json'
        if complete.exists():
            _preflight(plan,layout)
            receipt = _read(complete)
            if receipt.get('transition_id') != plan['transition_id'] or receipt.get('status') != 'completed':
                raise ValueError('completed receipt differs')
            if receipt.get('root_binding') != _root_binding(layout): raise ValueError('completed receipt target differs')
            if any(_component_state(layout,c) != 'after' for c in plan['components']):
                raise ValueError('completed binding state differs')
            return receipt
        if reconcile is not True: raise ValueError('incomplete intent requires explicit reconciliation')
    _pending(layout)
    if _protected(layout) != plan['protected']: raise ValueError('non-binding state changed before transition')
    with _freeze(layout) as connections:
        _pending(layout,connections)
        if _protected(layout,connections) != plan['protected']:
            raise ValueError('state changed while acquiring writer leases')
        if not existed:
            if any(_component_state(layout,c,connections) != 'before' for c in plan['components']):
                raise ValueError('initial correction requires all original components')
            _prepare_audit(plan,layout,connections,approval_ref)
            if fault: fault('intent',None)
        else: _verify_rollback(plan,layout)
        _preflight(plan,layout,connections)
        receipts = []
        for component in plan['components']:
            changed = _change(layout,component,connections)
            if fault: fault('component_commit',component['name'])
            receipts.append(_component_receipt(plan,layout,component,recovered=not changed))
            if fault: fault('component_receipt',component['name'])
        if _protected(layout,connections) != plan['protected']:
            raise ValueError('non-binding state changed during transition')
        receipt = dict(status='completed', transition_id=plan['transition_id'],
                       root_binding=_root_binding(layout),
                       original_opening_hash=plan['identity']['opening_hash'], components=receipts,
                       completed_at=datetime.now(timezone.utc).isoformat(),
                       qualification='binding_correction_only_no_profitability_or_activation')
        _save(layout['audit']/'receipt.json',receipt)
        if fault: fault('complete',None)
        return receipt


@_bounded
def rollback_transition(plan, *, worker_root, supervisor_root, launch_file, audit_dir,
                        approval_ref=None, apply=False):
    options = dict(worker_root=worker_root, supervisor_root=supervisor_root,
                   launch_file=launch_file, audit_dir=audit_dir)
    if apply is not True: return inspect_transition(plan, **options)
    _approval(approval_ref); layout = _layout(**options); _closed_inputs(layout); _validate_plan(plan,layout)
    _intent(plan,layout); _pending(layout); _verify_rollback(plan,layout)
    if _protected(layout) != plan['protected']:
        raise ValueError('new work exists; old-state rollback is forbidden')
    with _freeze(layout) as connections:
        _pending(layout,connections)
        if _protected(layout,connections) != plan['protected']:
            raise ValueError('state changed before rollback')
        _preflight(plan,layout,connections,reverse=True)
        receipt_path = layout['audit']/'rollback-receipt.json'
        if receipt_path.exists():
            receipt = _read(receipt_path)
            if any(_component_state(layout,c,connections) != 'before' for c in plan['components']):
                raise ValueError('rolled-back state changed')
            return receipt
        receipts = []
        for component in reversed(plan['components']):
            changed = _change(layout,component,connections,reverse=True)
            receipts.append(_component_receipt(plan,layout,component,recovered=not changed,reverse=True))
        if _protected(layout,connections) != plan['protected']:
            raise ValueError('non-binding state changed during rollback')
        receipt = dict(status='rolled_back', transition_id=plan['transition_id'], components=receipts,
                       root_binding=_root_binding(layout),
                       approval_ref=approval_ref, observed_at=datetime.now(timezone.utc).isoformat())
        _save(receipt_path,receipt); return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='inspect',choices=('inspect','prepare','apply','rollback'))
    for key in ('worker-root','supervisor-root','launch-file','audit-dir'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--plan'); parser.add_argument('--release-plan'); parser.add_argument('--expected-identity')
    parser.add_argument('--apply',action='store_true'); parser.add_argument('--approval-ref')
    parser.add_argument('--reconcile',action='store_true')
    args = parser.parse_args(argv)
    options = dict(worker_root=args.worker_root,supervisor_root=args.supervisor_root,
                   launch_file=args.launch_file,audit_dir=args.audit_dir)
    try:
        if args.action == 'prepare':
            if not args.release_plan or not args.expected_identity: raise ValueError('prepare requires exact release and identity files')
            result = prepare_plan(args.worker_root,args.supervisor_root,args.launch_file,
                                  release_plan=_read(args.release_plan),expected_identity=_read(args.expected_identity))
        else:
            if not args.plan: raise ValueError('frozen plan file required')
            plan = _read(args.plan)
            if args.action == 'inspect': result = inspect_transition(plan,**options)
            elif args.action == 'apply':
                result = apply_transition(plan,**options,apply=args.apply,approval_ref=args.approval_ref,reconcile=args.reconcile)
            else: result = rollback_transition(plan,**options,apply=args.apply,approval_ref=args.approval_ref)
        print(_json(result)); return 0
    except (ValueError,OSError,sqlite3.Error,TimeoutError,KeyError,TypeError) as error:
        print(_json({'status':'refused','error_type':type(error).__name__})); return 1


def _digest(value, length=64):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is None:
        raise ValueError('exact lowercase source digest required')


def _purpose(plan):
    if plan['old_commit'] == RELIABILITY_BASE_COMMIT:
        return RELIABILITY_PURPOSE
    if plan['old_commit'] == RECOVERY_BASE_COMMIT:
        return RECOVERY_PURPOSE
    if plan['old_commit'] == SNAPSHOT_BASE_COMMIT:
        return SNAPSHOT_PURPOSE
    return REPORTING_PURPOSE if plan['old_commit']==REPORTING_BASE_COMMIT else PURPOSE


def validate_release_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {'old_commit', 'new_commit', 'target', 'old', 'new'}:
        raise ValueError('exact correction release plan required')
    if plan['old_commit'] not in {BASE_COMMIT,REPORTING_BASE_COMMIT,SNAPSHOT_BASE_COMMIT,RECOVERY_BASE_COMMIT,RELIABILITY_BASE_COMMIT}:
        raise ValueError('correction must retain approved base commit')
    _digest(plan['new_commit'], 40)
    if plan['new_commit'] == plan['old_commit']:
        raise ValueError('successor must be a different reviewed commit')
    target = plan['target']
    if not isinstance(target, dict) or set(target) != set(TARGET) | {'experiment_id'}:
        raise ValueError('exact deployment target required')
    if any(target[key] != value for key, value in TARGET.items()):
        raise ValueError('deployment target differs')
    if not isinstance(target['experiment_id'], str) or re.fullmatch('[a-z0-9][a-z0-9_-]{1,63}', target['experiment_id']) is None:
        raise ValueError('exact experiment identity required')
    for side in ('old', 'new'):
        release = plan[side]
        if not isinstance(release, dict) or set(release) != {'runtime', 'watcher', 'native_launcher'}:
            raise ValueError('complete supervisor release map required')
        _digest(release['watcher']); _digest(release['native_launcher'])
        runtime = release['runtime']
        if not isinstance(runtime, dict) or not runtime:
            raise ValueError('complete runtime map required')
        for name, value in runtime.items():
            if not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*\.py', name) is None:
                raise ValueError('runtime source path differs')
            _digest(value)
    old, new = plan['old'], plan['new']
    if old['watcher'] != new['watcher'] or old['native_launcher'] != new['native_launcher']:
        raise ValueError('watcher and native launcher must remain unchanged')
    if set(old['runtime']) != set(new['runtime']):
        raise ValueError('runtime source inventory changed')
    if plan['old_commit'] == RELIABILITY_BASE_COMMIT:
        if _hash(old) != RELIABILITY_BASE_RELEASE or _hash(old['runtime']) != RELIABILITY_BASE_RUNTIME:
            raise ValueError('reliability correction predecessor differs from deployed release')
        allowed = {'supervisor_sync.py'}
    elif plan['old_commit'] == RECOVERY_BASE_COMMIT:
        if _hash(old) != RECOVERY_BASE_RELEASE or _hash(old['runtime']) != RECOVERY_BASE_RUNTIME:
            raise ValueError('recovery correction predecessor differs from deployed release')
        allowed = RECOVERY_MODULES
    elif plan['old_commit'] == SNAPSHOT_BASE_COMMIT:
        if _hash(old) != SNAPSHOT_BASE_RELEASE or _hash(old['runtime']) != SNAPSHOT_BASE_RUNTIME:
            raise ValueError('snapshot retry predecessor differs from the deployed release')
        allowed = {'supervisor_sync.py'}
    else:
        allowed = REPORTING_MODULES if plan['old_commit']==REPORTING_BASE_COMMIT else {'archive_snapshot.py'}
    if {name for name in old['runtime'] if old['runtime'][name] != new['runtime'][name]} != allowed:
        raise ValueError('exact reviewed runtime delta required for the selected base')


if __name__ == '__main__':
    raise SystemExit(main())

