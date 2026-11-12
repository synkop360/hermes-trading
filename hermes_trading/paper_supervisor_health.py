"""Small identity-bound paper supervisor health receiver; no model dispatch.

SSH carries one base64 JSON argument, never stdin or exception text. The worker
root owns the safety state; its manifest selects the adaptive reflection owner.
The existing safety recorder enforces latest-event retry and failure latches.
"""
import argparse
import base64
import json
from pathlib import Path
import re
from contextlib import contextmanager, ExitStack, closing, nullcontext
from datetime import timedelta
import hashlib
import os
import sqlite3
from time import monotonic
import uuid

from hermes_trading import paper_safety
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash, assert_writer_owner


PURPOSE = 'paper_supervisor_health_v1'
MAX_REQUEST_BYTES = 2048
FIELDS = {'schema_version', 'purpose', 'experiment_id', 'policy_hash', 'event_id',
          'observed_at', 'success', 'error_kind'}


def validate_request(request):
    if (not isinstance(request, dict) or set(request) != FIELDS or
            type(request['schema_version']) is not int or request['schema_version'] != 1 or
            request['purpose'] != PURPOSE):
        raise ValueError('paper supervisor health request differs')
    experiment = request['experiment_id']
    digest = request['policy_hash']
    if (not isinstance(experiment, str) or not 1 <= len(experiment) <= 128 or not experiment.strip() or
            not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None):
        raise ValueError('paper supervisor health identity differs')
    paper_safety._event_id(request['event_id'])
    paper_safety._result(request['success'], request['error_kind'])
    observed = request['observed_at']
    if not isinstance(observed, str) or len(observed) > 64:
        raise ValueError('paper supervisor health time differs')
    paper_safety._time(observed)
    if len(json.dumps(request, allow_nan=False).encode()) > MAX_REQUEST_BYTES:
        raise ValueError('paper supervisor health request exceeds bound')
    return dict(request)


def build_request(experiment_id, policy_hash, *, event_id, now, success, error_kind=None):
    return validate_request(dict(schema_version=1, purpose=PURPOSE, experiment_id=experiment_id,
        policy_hash=policy_hash, event_id=event_id, observed_at=paper_safety._time(now).isoformat(),
        success=success, error_kind=error_kind))


def receive_health(root, request):
    """Validate all bindings before the first safety-state/lock mutation."""
    request = validate_request(request)
    root = paper_safety._directory(Path(root))
    safety = paper_safety.load_policy(root, request['experiment_id'])
    if safety is None:
        raise ValueError('paper supervisor health requires explicit safety policy')
    manifest = paper_safety._read(root/'manifest.json')
    primary = resolve_primary(manifest)
    state = paper_safety._directory(root/primary.profile_directory(manifest, 'adaptive'))
    # Bound and reject linked metadata before the existing owner reader opens it.
    original = paper_safety._read(state/'reflection_owner.json')
    owner = assert_writer_owner(state, 'external_llm')
    if (owner is None or owner != original or owner['experiment_id'] != request['experiment_id'] or
            _hash(owner) != request['policy_hash']):
        raise ValueError('paper supervisor health owner differs')
    paper_safety.record_supervisor_result(root, success=request['success'], event_id=request['event_id'],
        now=paper_safety._time(request['observed_at']), error_kind=request['error_kind'])
    return dict(request, status='recorded')


RECOVERY_PURPOSE = 'paper_supervisor_recovery_v1'
RECOVERY_FIELDS = {'schema_version', 'purpose', 'request_id', 'experiment_id', 'approval_ref',
    'prepared_at', 'valid_until', 'state_hash', 'supervisor_event_id', 'supervisor_observed_at',
    'bindings', 'pc_evidence'}
PC_FIELDS = {'schema_version', 'observed_at', 'experiment_id', 'policy_hash', 'account_hash',
    'primary_wallet_eur', 'primary_selection_sha256', 'release_hash', 'runtime_hash',
    'launch_hash', 'limits_hash', 'wrapper_hash', 'lifecycle_hash'}


def _digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('recovery digest differs')
    return value


def _recovery_release():
    from hermes_trading.paper_publication import _release_hashes
    project = Path(__file__).resolve().parent.parent
    return dict(runtime=_release_hashes(),
        watcher=hashlib.sha256((project/'scripts/hermes_watch.py').read_bytes()).hexdigest(),
        native_launcher=hashlib.sha256((project/'scripts/native_hermes_watch.py').read_bytes()).hexdigest())


def _bounded_bytes(path, limit=1024*1024):
    paper_safety._directory(path.parent)
    info = paper_safety._regular(path)
    if info.st_size > limit: raise ValueError('recovery input exceeds bound')
    raw = path.read_bytes()
    if len(raw) > limit: raise ValueError('recovery input exceeds bound')
    return raw


def prepare_recovery_pc_evidence(project, launch_file, *, now=None):
    """Read actual local launch/source. This attestation is NOT authentication.

    Run on the real PC during the separately authorized cutover, transfer using
    the authenticated operator transport, and apply within 120 seconds.
    """
    from hermes_trading.supervisor_launch import validate_launch
    project = paper_safety._directory(Path(project))
    launch_file = Path(launch_file)
    at = paper_safety._time(paper_safety._utc_now() if now is None else now)
    release = _recovery_release()
    package = paper_safety._directory(project/'hermes_trading')
    actual = {p.relative_to(package).as_posix():hashlib.sha256(_bounded_bytes(p)).hexdigest()
              for p in package.rglob('*.py')}
    if actual != release['runtime']: raise ValueError('PC runtime source differs')
    for name, key in (('hermes_watch.py','watcher'),('native_hermes_watch.py','native_launcher')):
        if hashlib.sha256(_bounded_bytes(project/'scripts'/name)).hexdigest() != release[key]:
            raise ValueError('PC supervisor source differs')
    raw = _bounded_bytes(launch_file, 65536)
    config = json.loads(raw, object_pairs_hook=paper_safety._pairs)
    state = paper_safety._directory(Path(config['local_state_dir']))
    pc_files={name:paper_safety._read(state/name) for name in
              ('reflection_owner.json','wallet_account.json','supervisor_limits.json','supervisor_lifecycle.json')}
    checked = validate_launch(launch_file, project, release)
    if (config != {key:str(value) if isinstance(value,Path) else value for key,value in checked.items()} or
            config.get('primary_wallet_eur') != 200 or config.get('schema_version') != 2 or
            config.get('wrapper_sha256') != release['native_launcher'] or
            type(config.get('max_script_seconds')) is not int or not 1 <= config['max_script_seconds'] <= 240 or
            hashlib.sha256(_bounded_bytes(launch_file.with_suffix('.py'))).hexdigest() != release['native_launcher']):
        raise ValueError('PC native launch binding differs')
    for path in (project/'state/watcher_maintenance.json', state/'watcher_maintenance.json',
                 state/'paper_supervisor_health_pending.json',state/'supervisor_operator_stop.json'):
        if paper_safety._regular(path, missing=True) is not None: raise ValueError('PC supervision pending or stopped')
    limits=pc_files['supervisor_limits.json'];lifecycle=pc_files['supervisor_lifecycle.json']
    if (lifecycle.get('experiment_id')!=config['experiment_id'] or lifecycle.get('status')!='running' or lifecycle.get('stop_reason') or
            lifecycle.get('binding')!=_hash(dict(limits=limits,policy=pc_files['reflection_owner.json'],release=release)) or
            lifecycle.get('deadline')!=config['expires_at'] or not at < paper_safety._time(lifecycle['deadline']) or
            not 0 <= (at-paper_safety._time(lifecycle['last_observed_at'])).total_seconds() <= 60):
        raise ValueError('PC supervisor lifecycle not current and running')
    for key,maximum in (('cycles_started',limits['max_cycles']),('model_attempts_reserved',limits['max_model_attempts'])):
        if type(lifecycle.get(key)) is not int or not 0 <= lifecycle[key] < maximum:
            raise ValueError('PC supervisor budget exhausted')
    if launch_file.read_bytes() != raw or any(paper_safety._read(state/name)!=value for name,value in pc_files.items()):
        raise ValueError('PC launch changed')
    return dict(schema_version=1, observed_at=at.isoformat(), experiment_id=config['experiment_id'],
        policy_hash=config['policy_hash'], account_hash=config['account_hash'], primary_wallet_eur=200,
        primary_selection_sha256=config['primary_selection_sha256'], release_hash=_hash(release),
        runtime_hash=_hash(release['runtime']), launch_hash=_hash(config),
        limits_hash=_hash(limits), wrapper_hash=release['native_launcher'],lifecycle_hash=_hash(lifecycle))


def validate_recovery_request(request):
    if (not isinstance(request, dict) or set(request) != RECOVERY_FIELDS or
            type(request['schema_version']) is not int or request['schema_version'] != 1 or
            request['purpose'] != RECOVERY_PURPOSE or
            not isinstance(request['request_id'],str) or re.fullmatch('[0-9a-f]{32}',request['request_id']) is None or
            not isinstance(request['experiment_id'],str) or re.fullmatch('[a-z0-9][a-z0-9_-]{1,63}',request['experiment_id']) is None or
            not isinstance(request['approval_ref'],str) or re.fullmatch('[A-Za-z0-9_.:/-]{1,160}',request['approval_ref']) is None or
            not isinstance(request['bindings'],dict)):
        raise ValueError('recovery request differs')
    _digest(request['state_hash']); paper_safety._event_id(request['supervisor_event_id'])
    for name in ('prepared_at','valid_until','supervisor_observed_at'):
        if not isinstance(request[name],str) or len(request[name]) > 64:raise ValueError('recovery time differs')
        paper_safety._time(request[name])
    if (paper_safety._time(request['valid_until'])-paper_safety._time(request['prepared_at'])).total_seconds()!=120:
        raise ValueError('recovery validity differs')
    pc=request['pc_evidence']
    if (not isinstance(pc,dict) or set(pc)!=PC_FIELDS or type(pc['schema_version']) is not int or
            pc['schema_version']!=1 or type(pc['primary_wallet_eur']) is not int or pc['primary_wallet_eur']!=200 or
            pc['experiment_id']!=request['experiment_id'] or not isinstance(pc['observed_at'],str) or len(pc['observed_at'])>64):
        raise ValueError('PC recovery evidence differs')
    paper_safety._time(pc['observed_at'])
    for key in PC_FIELDS-{'schema_version','observed_at','experiment_id','primary_wallet_eur'}:_digest(pc[key])
    if len(paper_safety._json(request).encode()) > 8192:raise ValueError('recovery request exceeds bound')
    return json.loads(paper_safety._json(request))


@contextmanager
def _recovery_preflight(root, pc, *, now=None):
    """Hold read leases before safety lock. No migrations, writes or network.

    The rollback-journal publication lease excludes the sole paired writer.
    Account WAL read transactions retain that settlement's view. Files are checked again before
    yielding; operator configuration changes must remain quiescent during apply.
    """
    from hermes_trading.paired_paper import _profiles, _encode, _load_yaml
    from hermes_trading.paired_inception import read_paired_inception
    from hermes_trading.paired_packet_storage import load_packet
    from hermes_trading.paper_publication import assert_no_pending_paired_batch
    from hermes_trading.supervisor_sync import make_snapshot, validate_snapshot, FILES
    from hermes_trading.persistence.repository import StateRepository
    from hermes_trading.domain import json_value
    at = paper_safety._time(paper_safety._utc_now() if now is None else now)
    deadline=monotonic()+20
    def check():
        if monotonic() >= deadline:raise ValueError('recovery preflight deadline exceeded')
    pins={}
    def read(path):
        raw=_bounded_bytes(path); pins[path]=raw
        return json.loads(raw,object_pairs_hook=paper_safety._pairs)
    manifest=read(root/'manifest.json'); selection=resolve_primary(manifest)
    if manifest.get('execution_enabled') is not True or not selection.versioned or selection.wallet_eur!=200:
        raise ValueError('recovery requires activated primary 200')
    for profile in manifest.get('profiles',[]):
        name=profile.get('directory')
        if not isinstance(name,str) or Path(name).name!=name or name in ('.','..'):raise ValueError('recovery profile differs')
        path=paper_safety._directory(root/name)
        read(path/'wallet_account.json')
        for filename in ('goal.yaml','strategy.yaml'):pins[path/filename]=_bounded_bytes(path/filename)
    profiles=_profiles(root,manifest); primary=profiles[('adaptive',200)][0]
    owner=read(primary/'reflection_owner.json'); experiment=owner['experiment_id']
    if assert_writer_owner(primary,'external_llm')!=owner:raise ValueError('recovery owner differs')
    from hermes_trading.model_access_refresh import validate_renewed_access
    access=read(primary/'model_access.json');validate_renewed_access(owner,access,now=at)
    safety=paper_safety.load_policy(root,experiment)
    if safety is None or safety.get('supervisor_health_required') is not True:raise ValueError('recovery safety required')
    read(root/'paper_safety.json');limits=read(root/'worker_limits.json')
    from hermes_trading.paired_storage_admission import validate_storage_limits
    storage=validate_storage_limits(read(root/'storage_limits.json'),experiment)
    release=_recovery_release(); accounts={f'{arm}_{wallet}':_hash(row[2]) for (arm,wallet),row in profiles.items()}
    expected=dict(experiment_id=experiment,policy_hash=_hash(owner),account_hash=accounts['adaptive_200'],
        primary_wallet_eur=200,primary_selection_sha256=selection.selection_sha256,
        release_hash=_hash(release),runtime_hash=_hash(release['runtime']),wrapper_hash=release['native_launcher'])
    if any(pc.get(key)!=value for key,value in expected.items()) or not 0 <= (at-paper_safety._time(pc['observed_at'])).total_seconds() <= 120:
        raise ValueError('PC evidence source, identity or age differs')
    for path in (root/'paper_stop.json',root/'watcher_maintenance.json',primary/'experiment_halt.json'):
        if paper_safety._regular(path,missing=True) is not None:raise ValueError('recovery stop marker present')
    with ExitStack() as stack:
        connections={}
        def database(path, **unused):
            check(); path=Path(path)
            if path not in connections:
                paper_safety._directory(path.parent);paper_safety._regular(path)
                db=stack.enter_context(closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0,isolation_level=None)))
                db.row_factory=sqlite3.Row
                db.set_progress_handler(lambda: int(monotonic()>=deadline),1000)
                db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
                db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
                # Only the shared publication journal is the writer-exclusion lease.
                mode=db.execute('PRAGMA journal_mode').fetchone()[0]
                if mode not in ('delete','wal') or (path==primary/'paper_publications.sqlite' and mode!='delete'):
                    raise ValueError('recovery publication lease mode differs')
                connections[path]=db
            return nullcontext(connections[path])
        with database(primary/'paper_publications.sqlite') as publication:
            assert_no_pending_paired_batch(primary,db=publication)
            exists=publication.execute("SELECT 1 FROM sqlite_master WHERE name='publications'").fetchone()
            if exists and publication.execute('SELECT 1 FROM publications WHERE receipt IS NULL LIMIT 1').fetchone():
                raise ValueError('unresolved publication')
            for key,(path,_,_) in sorted(profiles.items()):
                database(path/'trading.db');database(path/'wallet_equity.sqlite')
            opening=read_paired_inception(root,database_factory=database,check=check)
            for name in FILES:
                if (primary/name).exists():pins[primary/name]=_bounded_bytes(primary/name)
            snapshot=make_snapshot(primary,now=at)
            files=validate_snapshot(snapshot,owner,now=at,expected_primary=selection,expected_account_hash=accounts['adaptive_200'])
            heartbeat=json.loads(files['heartbeat.json']);lifecycle=heartbeat.get('worker_lifecycle',{})
            configuration=dict(manifest=manifest,limits=limits,storage=storage,policy=owner,
                accounts={f'{arm}_{wallet}':row[2] for (arm,wallet),row in profiles.items()},
                opening_receipt_hash=_hash(opening),paper_safety=safety)
            if (heartbeat.get('mode')!='paper' or heartbeat.get('execution')!='simulated' or heartbeat.get('model_ready') is not True or
                    lifecycle.get('configuration_hash')!=_hash(configuration) or lifecycle.get('limits')!=limits or
                    lifecycle.get('activated_at')!=opening['at'] or
                    paper_safety._time(lifecycle.get('deadline'))!=paper_safety._time(opening['at'])+timedelta(seconds=limits['max_runtime_seconds']) or
                    not at < paper_safety._time(lifecycle['deadline']) or
                    lifecycle.get('status')!='running' or lifecycle.get('storage_report',{}).get('status')!='ready' or
                    any(lifecycle.get(key) for key in ('storage_stop','local_archive_capture_error','daily_backup_error','stop_reason')) or
                    not 0 <= (at-paper_safety._time(heartbeat['timestamp'])).total_seconds() <= 60):
                raise ValueError('recovery worker health differs')
            backup=lifecycle.get('daily_backup',{})
            if (not isinstance(limits.get('daily_backup'),dict) or backup.get('status') not in ('completed','already_completed') or
                    backup.get('date') != at.date().isoformat()):raise ValueError('recovery daily backup missing or stale')
            _digest(backup.get('sha256'));backup_path=paper_safety._directory(Path(backup['path']))
            from hermes_trading.paper_backup import _verified
            identity=hashlib.sha256(root.as_posix().encode()+b'\0'+pins[root/'manifest.json']).hexdigest()
            backup_receipt=_verified(backup_path,identity,check,500000000)
            if backup_receipt.get('sha256')!=backup['sha256']:raise ValueError('recovery backup receipt differs')
            allowed={'supervisor_error_halt','supervisor_recovery_pending'}
            admission=paper_safety.entry_status(root,policy=safety,now=at)
            if not admission['reasons'] or set(admission['reasons'])-allowed:raise ValueError('other entry blocker present')
            reported=heartbeat.get('entry_admission')
            if not isinstance(reported,dict) or set(reported.get('reasons',[]))-allowed:raise ValueError('worker reports another entry blocker')
            latest=publication.execute("SELECT batch_id,input_hash,payload,completed FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
            if latest is None or latest['batch_id']!=heartbeat['batch_id'] or latest['input_hash']!=heartbeat['input_hash']:
                raise ValueError('recovery batch differs')
            packet=load_packet(publication,latest['payload']);completed=load_packet(publication,latest['completed'])
            row=publication.execute('SELECT payload FROM paired_settlements WHERE batch_id=?',(latest['batch_id'],)).fetchone()
            measured=load_packet(publication,row[0]) if row else {}
            if (set(completed)!=set(accounts) or set(measured)!=set(accounts) or set(heartbeat['accounts'])!=set(accounts) or
                    packet.get('release')!=release['runtime'] or _hash(packet)!=latest['input_hash'] or
                    packet.get('created_at')!=heartbeat.get('observed_at') or completed!=heartbeat['accounts']):
                raise ValueError('recovery cohort differs')
            for (arm,wallet),(path,profile,config) in profiles.items():
                check();name=f'{arm}_{wallet}';trade=connections[path/'trading.db']
                if (packet['bindings'][name]['goal_hash']!=_hash(_encode(_load_yaml(path/'goal.yaml'))) or
                        _encode(_load_yaml(path/'strategy.yaml'))!=packet['strategies'][arm] or
                        (arm=='control' and hashlib.sha256(pins[path/'strategy.yaml']).hexdigest()!=profile['strategy_sha256'])):
                    raise ValueError('recovery strategy or goal differs from settled batch')
                for table, terminal in (('positions',('closed',)),('orders',('filled','canceled','rejected')),('incidents',('closed','resolved'))):
                    placeholders=','.join('?' for _ in terminal)
                    if trade.execute(f'SELECT 1 FROM {table} WHERE status IS NULL OR status NOT IN ({placeholders}) LIMIT 1',terminal).fetchone():
                        raise ValueError('recovery account not flat or resolved')
                repo=StateRepository(trade)
                if repo.has_pending_projection_items() or _hash(json_value(repo.portfolio_positions()))!=measured[name]['positions_hash']:
                    raise ValueError('recovery financial projection differs')
                observation=connections[path/'wallet_equity.sqlite'].execute('SELECT at,payload FROM observations ORDER BY at DESC LIMIT 1').fetchone()
                report=read(path/'wallet_metrics.json')
                if (completed[name].get('finalized') is not True or packet['bindings'][name]['account_hash']!=accounts[name] or
                        observation is None or observation['at']!=packet['created_at'] or
                        load_packet(connections[path/'wallet_equity.sqlite'],observation['payload'])!=measured[name]['observation'] or
                        report.get('observation')!=measured[name]['observation'] or report.get('status')!='observed'):
                    raise ValueError('recovery account settlement differs')
            for path,raw in pins.items():
                check()
                if _bounded_bytes(path)!=raw:raise ValueError('recovery metadata changed during preparation')
            check()
            yield dict(experiment_id=experiment,manifest_hash=_hash(manifest),opening_hash=_hash(opening),
                safety_policy_hash=_hash(safety),owner_hash=_hash(owner),primary_wallet_eur=200,
                selection_hash=selection.selection_sha256,account_hashes=accounts,runtime_hash=_hash(release['runtime']),
                supervisor_release_hash=_hash(release),pc_evidence_hash=_hash(pc),batch_id=latest['batch_id'],
                settlement_hash=_hash(measured),worker_limits_hash=_hash(limits),configuration_hash=_hash(configuration))


def build_recovery_request(root, *, approval_ref, pc_evidence, now=None):
    root=paper_safety._directory(Path(root))
    at=paper_safety._time(paper_safety._utc_now() if now is None else now)
    with _recovery_preflight(root,pc_evidence,now=now) as bindings:
        policy=paper_safety.load_policy(root,bindings['experiment_id'])
        with paper_safety._artifact_lock(root/'paper_safety_state.json'):
            state=paper_safety._state(root,policy)
        supervisor=state['supervisor']
        return validate_recovery_request(dict(schema_version=1,purpose=RECOVERY_PURPOSE,request_id=uuid.uuid4().hex,
            experiment_id=bindings['experiment_id'],approval_ref=approval_ref,prepared_at=at.isoformat(),
            valid_until=(at+timedelta(seconds=120)).isoformat(),state_hash=_hash(state),
            supervisor_event_id=supervisor['event_id'],supervisor_observed_at=supervisor['last_seen'],
            bindings=bindings,pc_evidence=pc_evidence))


def receive_recovery(root, request, *, enabled=False, env=None, now=None, abort=False):
    env=os.environ if env is None else env
    if (enabled is not True or env.get('HERMES_TRADING_MODE')!='paper' or
            env.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false'):
        raise ValueError('explicit paper recovery opt-in required')
    request=validate_recovery_request(request);root=paper_safety._directory(Path(root))
    release=_recovery_release()
    if (request['bindings'].get('runtime_hash')!=_hash(release['runtime']) or
            request['bindings'].get('supervisor_release_hash')!=_hash(release)):
        raise ValueError('recovery receiver source differs')
    return paper_safety.recover_supervisor(root,request,enabled=True,abort=abort,now=now,
        preflight=lambda:_recovery_preflight(root,request['pc_evidence'],now=now))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Receive one code-only paper supervisor health observation')
    parser.add_argument('--root', required=True)
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--request-base64')
    inputs.add_argument('--recovery-base64')
    parser.add_argument('--enable-recovery',action='store_true')
    parser.add_argument('--abort-recovery',action='store_true')
    args = parser.parse_args(argv)
    try:
        recovery=args.recovery_base64 is not None
        encoded=args.recovery_base64 if recovery else args.request_base64
        if not recovery and (args.enable_recovery or args.abort_recovery):raise ValueError('health flags differ')
        if len(encoded) > (12000 if recovery else 4096):
            raise ValueError('paper supervisor health argument exceeds bound')
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > (8192 if recovery else MAX_REQUEST_BYTES):
            raise ValueError('paper supervisor health request exceeds bound')
        if recovery:
            receipt=receive_recovery(args.root,json.loads(raw,object_pairs_hook=paper_safety._pairs),
                enabled=args.enable_recovery,abort=args.abort_recovery)
        else:receipt = receive_health(args.root, json.loads(raw))
    except Exception:
        print(json.dumps(dict(status='rejected', error_kind='supervisor_recovery_rejected' if args.recovery_base64 is not None else 'supervisor_health_rejected')))
        return 2
    print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
