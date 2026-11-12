"""Resume the reviewed paper cutover on an existing persistent volume only."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import shutil
import sqlite3
import uuid
from contextlib import closing
import zipfile

from scripts import paper_release_transition as transition
from scripts import hermes_watch, native_hermes_watch
from scripts.paper_local_transport import _pairs, _constant
from hermes_trading.reflection_supervision import _hash
from hermes_trading.supervisor_launch import validate_launch
from hermes_trading.paper_publication import _archive_once

EXPERIMENT='paper-beta-v1-20261021-6c9d520'
MAX_UNCOMPRESSED=64*1024*1024


def _json(raw):
    result=json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant)
    if not isinstance(result,dict):raise ValueError('checkpoint object required')
    return result


def decode_checkpoint(raw, expected_sha256, experiment_id):
    if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError('checkpoint archive checksum differs')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members=archive.infolist();names=[row.filename for row in members]
            if (len(names)!=len(set(names)) or len(names)>1026 or
                    sum(row.file_size for row in members)>MAX_UNCOMPRESSED):
                raise ValueError('checkpoint archive bounds differ')
            for row in members:
                mode=stat.S_IFMT(row.external_attr>>16)
                if row.flag_bits&1 or row.is_dir() or mode not in (0,stat.S_IFREG):
                    raise ValueError('checkpoint archive member differs')
            manifest=_json(archive.read('manifest.json'))
            if (type(manifest.get('schema_version')) is not int or manifest['schema_version']!=1 or
                    manifest.get('experiment_id')!=experiment_id or not isinstance(manifest.get('files'),dict)):
                raise ValueError('checkpoint identity differs')
            files={}
            for name,digest in manifest['files'].items():
                path=PurePosixPath(name)
                if (not isinstance(name,str) or path.is_absolute() or '\\' in name or
                        any(part in {'.','..'} for part in name.split('/')) or
                        re.fullmatch(r'[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*',name) is None or
                        name.endswith(('-wal','-shm','-journal')) or
                        not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None):
                    raise ValueError('checkpoint file identity differs')
                data=archive.read('supervisor/'+name)
                if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('checkpoint file checksum differs')
                files[name]=data
            if not files or set(names)!={'manifest.json','launch.json',*('supervisor/'+name for name in files)}:
                raise ValueError('checkpoint member inventory differs')
            launch=archive.read('launch.json')
            if hashlib.sha256(launch).hexdigest()!=manifest.get('source_launch_sha256'):
                raise ValueError('checkpoint launch checksum differs')
            return files,launch,manifest
    except (zipfile.BadZipFile,KeyError,UnicodeError,RuntimeError) as error:
        raise ValueError('checkpoint archive invalid') from None


def _same_or_create(path, data):
    path=transition._safe_root(Path(path).absolute())
    if path.exists():
        if path.is_symlink() or path.read_bytes()!=data:raise ValueError('existing installation file differs')
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    _archive_once(path,data)


def _digest_file(path):
    with path.open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()


def _verify_evidence(path):
    with zipfile.ZipFile(path) as archive:
        members=archive.infolist()
        if len(members)>20000 or sum(p.file_size for p in members)>2*1024**3:
            raise ValueError('evidence archive exceeds bound')
        if archive.getinfo('archive-manifest.json').file_size>4*1024*1024:
            raise ValueError('evidence manifest exceeds bound')
        hashes=_json(archive.read('archive-manifest.json'))
        if len(members)!=len(hashes)+1 or set(archive.namelist())!=set(hashes)|{'archive-manifest.json'}:
            raise ValueError('evidence archive inventory differs')
        for name,digest in hashes.items():
            if not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None:
                raise ValueError('evidence digest differs')
            with archive.open(name) as source:
                if hashlib.file_digest(source,'sha256').hexdigest()!=digest:raise ValueError('evidence archive checksum differs')
        return hashes


def _archive_evidence(base, paths, destination):
    temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
    hashes={}
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                transition._safe_root(path)
                name=path.relative_to(base).as_posix();hashes[name]=_digest_file(path)
                archive.write(path,name)
            archive.writestr('archive-manifest.json',json.dumps(hashes,sort_keys=True))
        if _verify_evidence(temporary)!=hashes:raise ValueError('evidence archive verification failed')
        with temporary.open('r+b') as handle:os.fsync(handle.fileno())
        os.replace(temporary,destination);transition._sync_dir(destination.parent)
        return hashes
    finally:
        if temporary.exists():transition._safe_root(temporary).unlink()


def _retire_cold_journal(database, db, expected_sha256):
    """Retire only an unchanged, archived PERSIST journal with a zero header.

    SQLite lockingv3 identifies a nonzero valid header as necessary for a hot
    rollback journal. Never infer journal safety from its filename or size.
    """
    journal=transition._safe_root(database.with_name(database.name+'-journal'))
    if not journal.exists():return False
    if (db.in_transaction or db.execute('PRAGMA locking_mode').fetchone()!=('exclusive',) or
            db.execute('PRAGMA journal_mode').fetchone()!=('delete',) or
            not isinstance(expected_sha256,str) or _digest_file(journal)!=expected_sha256):
        raise ValueError('rollback journal lacks closed ownership or exact archived evidence')
    with journal.open('rb') as source:header=source.read(8)
    if any(header):raise ValueError('nonzero rollback journal requires SQLite recovery')
    journal.unlink();transition._sync_dir(database.parent)
    return True


def recover_journals(root, auxiliary):
    """Checkpoint only closed SQLite journals, preserving raw and logical evidence."""
    sidecars=[p for p in root.rglob('*') if p.is_file() and not p.is_relative_to(auxiliary) and p.name.endswith(('-wal','-shm','-journal'))]
    if not sidecars:return dict(databases=0)
    databases=set()
    for path in sidecars:
        transition._safe_root(path);database=path.with_name(path.name.rsplit('-',1)[0])
        if not database.is_file() or database.suffix not in {'.sqlite','.db'}:raise ValueError('orphan SQLite recovery input')
        databases.add(database)
    paths=sorted(set(sidecars)|databases)
    destination=auxiliary/('closed-journals-'+uuid.uuid4().hex+'.zip')
    archived=_archive_evidence(root,paths,destination)
    records=[]
    for path in sorted(databases):
        with closing(sqlite3.connect(path,timeout=0,isolation_level=None)) as db:
            db.execute('PRAGMA locking_mode=EXCLUSIVE');db.execute('BEGIN EXCLUSIVE')
            before=transition._database_hash(db);db.execute('COMMIT')
            result=db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
            if not result or result[0][0]!=0:raise ValueError('SQLite recovery is busy')
            if db.execute('PRAGMA journal_mode=DELETE').fetchone()!=('delete',):
                raise ValueError('SQLite recovery cannot close the journal')
            after=transition._database_hash(db)
            if before!=after:raise ValueError('SQLite recovery changed logical history')
            journal=transition._safe_root(path.with_name(path.name+'-journal'))
            journal_name=journal.relative_to(root).as_posix()
            journal_digest=archived.get(journal_name);generated_archive=None;retired=False
            if journal.exists() and _digest_file(journal)!=journal_digest:
                # WAL->DELETE itself may create an inert rollback artifact.
                # Preserve that exact post-checkpoint artifact too, under the
                # same retained exclusive lease. Never waive archive evidence.
                if journal.stat().st_size>8*1024*1024:raise ValueError('generated rollback journal exceeds bound')
                generated_archive=auxiliary/('post-checkpoint-journal-'+uuid.uuid4().hex+'.zip')
                generated=_archive_evidence(root,[journal],generated_archive)
                # This artifact belongs to SQLite's still-open pager (notably
                # Windows). Let SQLite release/delete it on connection close.
                # The post-close sidecar assertion below remains mandatory.
            else:
                retired=_retire_cold_journal(path,db,journal_digest)
            # SQLite on Windows can retain an orphan SHM from an abruptly
            # exited process after switching back to DELETE. It contains lock
            # metadata, not rows. Remove it only with our main DB still locked
            # exclusively, no WAL remaining, and the raw input already archived.
            shm=path.with_name(path.name+'-shm')
            if shm.exists():
                if path.with_name(path.name+'-wal').exists() or db.execute('PRAGMA locking_mode').fetchone()!=('exclusive',):
                    raise ValueError('SQLite shared-memory ownership differs')
                transition._safe_root(shm).unlink()
            if transition._database_hash(db)!=before:raise ValueError('journal retirement changed logical history')
            records.append(dict(path=path.relative_to(root).as_posix(),logical_hash=before,cold_journal_retired=retired,
                generated_journal_archive=None if generated_archive is None else generated_archive.name))
        if any(path.with_name(path.name+suffix).exists() for suffix in ('-wal','-shm','-journal')):
            raise ValueError('SQLite recovery left an open sidecar')
    receipt=dict(databases=len(records),archive=destination.name,records=records)
    transition._save(destination.with_suffix('.receipt.json'),receipt)
    return receipt


def compact_audits(auxiliary):
    """Retain verified byte-exact archives before removing owned rollback copies."""
    transition._safe_root(auxiliary)
    receipt_path=auxiliary/'installed.json';receipt=transition._read(receipt_path)
    archive=auxiliary/'native-transition-audits.zip'
    if receipt.get('compacted_audits') is True:
        if not archive.is_file() or _digest_file(archive)!=receipt.get('audit_archive_sha256'):
            raise ValueError('retained audit archive differs')
        return
    directories=[auxiliary/name for name in ('entry-history','cloud-source') if (auxiliary/name).exists()]
    if not directories:return
    for directory in directories:transition._safe_root(directory)
    paths=[p for directory in directories for p in directory.rglob('*') if p.is_file()]
    if not archive.exists():_archive_evidence(auxiliary,paths,archive)
    hashes=_verify_evidence(archive)
    for path in paths:
        transition._safe_root(path)
        if hashes.get(path.relative_to(auxiliary).as_posix())!=_digest_file(path):
            raise ValueError('rollback copy changed before compaction')
    receipt['audit_archive_sha256']=_digest_file(archive)
    transition._save(receipt_path,receipt)
    for directory in directories:shutil.rmtree(directory)
    transition._sync_dir(auxiliary)
    transition._save(receipt_path,dict(receipt,compacted_audits=True))


def _phase(root, auxiliary, release_plan, name, approval):
    options=dict(worker_root=root,supervisor_root=auxiliary/'supervisor',
        launch_file=auxiliary/'launch.json',audit_dir=auxiliary/name)
    intent=options['audit_dir']/'intent.json'
    if intent.exists():
        plan=transition._read(intent)['plan']
        if plan['release_plan']!=release_plan:raise ValueError('retained transition source plan differs')
    else:
        identity=transition._identity(transition._layout(root,options['supervisor_root'],options['launch_file']),check_economics=True)
        plan=transition.prepare_plan(root,options['supervisor_root'],options['launch_file'],
            release_plan=release_plan,expected_identity=identity)
    return transition.apply_transition(plan,**options,apply=True,reconcile=intent.exists(),approval_ref=approval)


def required_cutover_space(root, auxiliary, supervisor_bytes, *, phases):
    # Existing preparation stages already reduce filesystem free space. Never
    # delete them or treat an unpublished stage as a completed rollback copy.
    roots={'worker':root,'supervisor':auxiliary/'supervisor','launch':auxiliary/'launch.json'}
    component_bytes=sum(transition._safe_root(roots[role]/name if role!='launch' else roots[role]).stat().st_size
        for _,role,name,_ in transition.COMPONENTS)
    names=('entry-history','cloud-source') if phases==2 else ('cloud-source',)
    pending=sum(not (auxiliary/name/'intent.json').is_file() for name in names)
    return pending*component_bytes+supervisor_bytes+16*1024*1024


def install(project, cloud_plan, *, entry_plan=None, env=None, experiment_id=EXPERIMENT, observer=None):
    observe=observer if observer is not None else lambda _:None
    env=dict(os.environ if env is None else env)
    if env.get('HERMES_TRADING_MODE')!='paper' or env.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false':
        raise ValueError('explicit paper-only installation required')
    if any(env.get('RAILWAY_'+name.upper()+'_ID')!=value for name,value in transition.TARGET.items()):
        raise ValueError('Railway installation destination differs')
    project=transition._safe_root(Path(project).absolute());root=transition._safe_root(project/'state')
    if not (root/'manifest.json').is_file():raise ValueError('existing paper experiment required')
    release=hermes_watch._supervisor_release()
    transition.validate_release_plan(cloud_plan)
    if cloud_plan['new']!=release or cloud_plan['target']['experiment_id']!=experiment_id:
        raise ValueError('installed successor source or experiment differs')
    auxiliary=root/'.paper-cloud-cutover';launch_path=root/'cloud-supervisor-launch.json'
    completed=auxiliary/'installed.json'
    if completed.exists():
        receipt=transition._read(completed)
        if receipt.get('release_hash')!=_hash(release) or receipt.get('experiment_id')!=experiment_id:
            raise ValueError('installed cloud receipt differs')
        # An expired supervisor never authorizes more cycles. Its unchanged
        # identity can still be verified so the normal protective worker boots.
        validate_launch(launch_path,project,release,allow_expired=True)
        native_hermes_watch.validate_local_transport(project)
        compact_audits(auxiliary)
        observe('already_installed')
        return dict(status='already_installed',launch_file=str(launch_path))
    approval=env.get('HERMES_CLOUD_CUTOVER_APPROVAL')
    transition._approval(approval)
    expected_sha=env.get('HERMES_CLOUD_CHECKPOINT_SHA256','')
    if re.fullmatch('[0-9a-f]{64}',expected_sha) is None:raise ValueError('checkpoint checksum required')
    archive_path=auxiliary/'checkpoint.zip'
    if archive_path.exists():
        transition._safe_root(archive_path)
        if archive_path.stat().st_size>1024*1024:raise ValueError('retained checkpoint exceeds bound')
        raw=archive_path.read_bytes()
    else:
        try:count=int(env.get('HERMES_CLOUD_CHECKPOINT_PARTS','0'))
        except ValueError:raise ValueError('checkpoint part count invalid') from None
        if not 1<=count<=32:raise ValueError('checkpoint part count invalid')
        encoded=''.join(env.get(f'HERMES_CLOUD_CHECKPOINT_{i:02d}','') for i in range(count))
        if len(encoded)>1400000:raise ValueError('checkpoint transfer exceeds bound')
        try:raw=base64.b64decode(encoded,validate=True)
        except ValueError:raise ValueError('checkpoint transfer invalid') from None
    files,original_launch,manifest=decode_checkpoint(raw,expected_sha,experiment_id)
    observe('checkpoint_verified')
    initial=_json(original_launch)
    if initial.get('experiment_id')!=experiment_id or initial.get('paper_safety_enabled') is not True:
        raise ValueError('original paper safety launch required')
    owner=dict(schema_version=1,purpose='paper_cloud_auxiliary_v1',worker_root=str(root),
        policy_hash=_hash(transition._read(root/'adaptive_200/reflection_owner.json')),
        account_hash=_hash(transition._read(root/'adaptive_200/wallet_account.json')))
    if _hash(_json(files['reflection_owner.json']))!=owner['policy_hash'] or _hash(_json(files['wallet_account.json']))!=owner['account_hash']:
        raise ValueError('checkpoint does not match the retained worker primary')
    if auxiliary.exists() and not (auxiliary/'owner.json').exists() and set(p.name for p in auxiliary.iterdir())-{'checkpoint.zip'}:
        raise ValueError('unowned auxiliary namespace already exists')
    auxiliary.mkdir(exist_ok=True)
    _same_or_create(archive_path,raw)
    _same_or_create(auxiliary/'owner.json',(json.dumps(owner,sort_keys=True)+'\n').encode())
    extracted=auxiliary/'checkpoint-extracted.json'
    if not extracted.exists():
        for name,data in files.items():_same_or_create(auxiliary/'supervisor'/name,data)
        _same_or_create(auxiliary/'launch.json',original_launch)
        transition._save(extracted,dict(archive_sha256=expected_sha))
    if transition._read(extracted)!={'archive_sha256':expected_sha}:raise ValueError('retained extraction differs')
    transitioned=auxiliary/'sources-transitioned.json'
    with transition.volume_cutover_layout(root):
        if not transitioned.exists():
            recover_journals(root,auxiliary)
            observe('closed_journals_verified')
            # The owned-volume native transition copies only its six changed
            # binding components. Untouched history stays protected in place.
            supervisor_bytes=sum(len(data) for data in files.values())
            if shutil.disk_usage(root).free<required_cutover_space(root,auxiliary,supervisor_bytes,phases=2 if entry_plan is not None else 1):
                raise ValueError('insufficient space for durable cutover evidence')
            if not (auxiliary/'cloud-source/intent.json').exists() and entry_plan is not None:
                transition.validate_release_plan(entry_plan)
                if entry_plan['new']!=cloud_plan['old']:raise ValueError('source transition chain differs')
                limits=transition._read(auxiliary/'supervisor/supervisor_limits.json')
                if (auxiliary/'entry-history/intent.json').exists() or limits.get('release_hash')!=_hash(entry_plan['new']):
                    observe('entry_history_transition')
                    _phase(root,auxiliary,entry_plan,'entry-history',approval)
            observe('cloud_source_transition')
            _phase(root,auxiliary,cloud_plan,'cloud-source',approval)
            transition._save(transitioned,dict(release_hash=_hash(release),archive_sha256=expected_sha))
        if transition._read(transitioned)!={'release_hash':_hash(release),'archive_sha256':expected_sha}:
            raise ValueError('source transition completion differs')
    base=root/'paired-supervisor'/experiment_id;target=base/'adaptive_200'
    stage=auxiliary/'supervisor'
    current={p.relative_to(stage).as_posix():transition._safe_root(p).read_bytes() for p in stage.rglob('*') if p.is_file()}
    if target.exists() and any(p.is_file() and p.relative_to(target).as_posix() not in current for p in target.rglob('*')):
        raise ValueError('target supervisor has additional retained files')
    for name,data in current.items():_same_or_create(target/name,data)
    config=transition._read(auxiliary/'launch.json')
    if config.get('paper_safety_enabled') is not True:raise ValueError('original paper safety launch opt-in required')
    config=dict(config,project_dir=str(project),local_state_dir=str(target),mirror_dir=str(base/'mirror'),
        remote_state_dir='/app/state/adaptive_200',transport='local',wrapper_sha256=release['native_launcher'])
    candidate=auxiliary/'relocated-launch.json'
    _same_or_create(candidate,(json.dumps(config,sort_keys=True)+'\n').encode())
    validate_launch(candidate,project,release)
    observe('supervisor_relocated')
    native_hermes_watch.validate_local_transport(project)
    wrapper=Path(native_hermes_watch.__file__).read_bytes()
    if hashlib.sha256(wrapper).hexdigest()!=release['native_launcher']:raise ValueError('native wrapper differs')
    _same_or_create(launch_path.with_suffix('.py'),wrapper)
    _same_or_create(launch_path,candidate.read_bytes())
    receipt=dict(schema_version=1,status='installed',experiment_id=experiment_id,
        release_hash=_hash(release),archive_sha256=expected_sha,launch_hash=_hash(config),
        qualification='retained_state_source_transition_no_profitability_claim')
    transition._save(completed,receipt)
    compact_audits(auxiliary)
    observe('installed')
    return dict(status='installed',launch_file=str(launch_path))
