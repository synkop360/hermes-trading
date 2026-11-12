"""Condense fixed obsolete legacy logs; retain exact restorable original bytes."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from hermes_trading.archive_snapshot import _safe_root,ownership
from hermes_trading.paper_publication import _temporary,_sync_dir

LEGACY_LOGS=('cost_events.jsonl','decisions.jsonl','fills.jsonl','hypotheses.jsonl',
             'orders.jsonl','positions.jsonl','trades.jsonl')
MAX_ORIGINAL=256*1024*1024
MAX_ARCHIVE=64*1024*1024
MIN_FREE=16*1024*1024


def storage_preflight(root):
    """Read only technical admission facts while retaining the worker lease."""
    from hermes_trading.paired_worker import _stats,_profiles,_active_storage_positions
    from hermes_trading.paired_primary import resolve_primary
    from hermes_trading.paired_storage_admission import inspect_storage
    root=_safe_root(Path(root).absolute())
    with ownership(root,role='worker',enabled=True) as db:
        manifest=json.loads((root/'manifest.json').read_bytes())
        profiles=_profiles(root,manifest);selection=resolve_primary(manifest)
        stats=_stats(profiles[('adaptive',selection.wallet_eur)][0])
        row=db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()
        state=json.loads(row[0]) if row is not None else {}
        storage=inspect_storage(root,json.loads((root/'storage_limits.json').read_bytes()))
        last=stats.get('latest') or {}
        return dict(kind='paper_storage_preflight',all_positions_closed=all(v==0 for v in _active_storage_positions(profiles).values()),
            pending_batch=stats['pending'],last_batch_storage_stop=last.get('packet',{}).get('operational_stop_reason')=='paired_storage_stop',
            worker_running=state.get('status')=='running',storage_stop=state.get('storage_stop') is True,
            storage_ready=storage.get('status')=='ready',state_bytes=storage.get('state_bytes'),free_bytes=storage.get('free_bytes'))


def _hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _save(path,body):
    raw=(json.dumps(body,sort_keys=True)+'\n').encode()
    temporary=_temporary(path,raw)
    try:os.replace(temporary,path);_sync_dir(path.parent)
    finally:temporary.unlink(missing_ok=True)


def _decoded(archive,limit):
    size=0;digest=hashlib.sha256()
    try:
        with gzip.open(archive,'rb') as source:
            while chunk:=source.read(1024*1024):
                size+=len(chunk)
                if size>limit:raise ValueError('legacy archive decoded bound differs')
                digest.update(chunk)
    except (OSError,EOFError):raise ValueError('legacy archive is invalid') from None
    return size,digest.hexdigest()


def compact_history(root,*,enabled=False,retire_originals=False,legacy_database_only=False,fault=None):
    if enabled is not True or os.environ.get('HERMES_TRADING_MODE')!='paper' or os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false':
        raise ValueError('explicit paper-only history compaction required')
    root=_safe_root(Path(root).absolute())
    if type(legacy_database_only) is not bool:raise ValueError('literal legacy database choice required')
    manifest=root/'manifest.json'
    if not manifest.is_file():raise ValueError('retained experiment manifest required')
    owner={'schema_version':1,'purpose':'lossless_legacy_database_v1' if legacy_database_only else 'lossless_legacy_logs_v1','root':str(root),
           'manifest_sha256':_hash(_safe_root(manifest))}
    if type(retire_originals) is not bool:raise ValueError('literal archive retirement choice required')
    reclaimed=0;compacted=0;reclaimable=0
    with ownership(root,role='worker',enabled=True) as worker:
        if legacy_database_only:
            from hermes_trading.paper_publication import _release_hashes
            from hermes_trading.reflection_supervision import _hash as object_hash
            import sqlite3
            from contextlib import closing
            expected={f'{arm}_{wallet}' for arm in ('adaptive','control') for wallet in (50,100,150,200)}
            configuration=json.loads(manifest.read_bytes())
            receipt=_safe_root(root/'.paper-cloud-cutover/installed.json')
            if (configuration.get('status')!='paper_ready' or configuration.get('execution_enabled') is not True or
                    {p.get('directory') for p in configuration.get('profiles',[])}!=expected or
                    not receipt.is_file() or json.loads(receipt.read_bytes()).get('status')!='installed' or
                    object_hash(_release_hashes())!='082042439d5dadd8247b788fe6b29de5c7449f165f339a2e155761bf55c525de'):
                raise ValueError('installed paired source and eight independent active wallets required')
            state=json.loads(worker.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
            database=_safe_root(root/'trading.db')
            if database.exists():
                if state.get('storage_stop') is not True:raise ValueError('legacy database compaction requires a paused worker')
                for name in expected:
                    active=_safe_root(root/name/'trading.db')
                    if not active.is_file():raise ValueError('all eight active wallet databases must exist independently')
                    if os.path.samefile(database,active):raise ValueError('legacy database aliases an active wallet')
                if any(database.with_name(database.name+suffix).exists() for suffix in ('-wal','-shm','-journal')):
                    raise ValueError('legacy database must have closed sidecars before compaction')
                with closing(sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)) as db:
                    db.execute('PRAGMA locking_mode=EXCLUSIVE');db.execute('BEGIN EXCLUSIVE');db.commit()
                    if db.execute('PRAGMA journal_mode').fetchone()!=('delete',):raise ValueError('closed DELETE-mode legacy database required')
                if any(database.with_name(database.name+suffix).exists() for suffix in ('-wal','-shm','-journal')):
                    raise ValueError('legacy database sidecars appeared during closure')
                # The container has one source-pinned paired writer; it never
                # writes the root legacy database. Its worker lease stays held
                # across exact archive validation and original retirement.
        base=_safe_root(root/('.legacy-database-compact' if legacy_database_only else '.legacy-history-compact'))
        if base.exists() and not (base/'owner.json').is_file():raise ValueError('unowned legacy archive namespace')
        base.mkdir(exist_ok=True)
        marker=_safe_root(base/'owner.json')
        if marker.exists():
            if marker.stat().st_size>4096 or json.loads(marker.read_bytes())!=owner:raise ValueError('legacy archive owner differs')
        else:_save(marker,owner)
        for name in (('trading.db',) if legacy_database_only else LEGACY_LOGS):
            original=_safe_root(root/name);archive=_safe_root(base/(name+'.gz'));receipt=_safe_root(base/(name+'.receipt.json'))
            if receipt.exists():
                if receipt.stat().st_size>4096:raise ValueError('legacy receipt exceeds bound')
                record=json.loads(receipt.read_bytes())
                if (not isinstance(record,dict) or record.get('name')!=name or record.get('owner')!=owner or
                        type(record.get('original_bytes')) is not int or not 0<=record['original_bytes']<=MAX_ORIGINAL or
                        not archive.is_file() or archive.stat().st_size>MAX_ARCHIVE or _hash(archive)!=record.get('archive_sha256') or
                        _decoded(archive,record['original_bytes'])!=(record['original_bytes'],record.get('original_sha256'))):
                    raise ValueError('legacy retained archive differs')
            elif original.exists():
                size=original.stat().st_size
                if size>MAX_ORIGINAL:raise ValueError('legacy original exceeds bound')
                digest=_hash(original)
                if not archive.exists():
                    temporary=_safe_root(base/(name+'.'+uuid.uuid4().hex+'.tmp'))
                    try:
                        with original.open('rb') as source,temporary.open('xb') as target:
                            with gzip.GzipFile(fileobj=target,mode='wb',compresslevel=6,mtime=0) as zipped:
                                while chunk:=source.read(1024*1024):
                                    if target.tell()>MAX_ARCHIVE or shutil.disk_usage(root).free<MIN_FREE+2*1024*1024:
                                        raise ValueError('legacy compression headroom exhausted')
                                    zipped.write(chunk)
                            target.flush();os.fsync(target.fileno())
                        if temporary.stat().st_size>=size:
                            continue
                        if temporary.stat().st_size>MAX_ARCHIVE or _decoded(temporary,size)!=(size,digest):
                            raise ValueError('legacy compressed copy differs')
                        os.replace(temporary,archive);_sync_dir(base)
                    finally:temporary.unlink(missing_ok=True)
                if archive.stat().st_size>MAX_ARCHIVE or _decoded(archive,size)!=(size,digest):
                    raise ValueError('legacy original does not match retained archive')
                record=dict(name=name,owner=owner,original_bytes=size,original_sha256=digest,
                            archive_sha256=_hash(archive),archive_bytes=archive.stat().st_size)
                _save(receipt,record)
                if fault:fault('receipt')
            elif archive.exists():raise ValueError('legacy archive has no receipt or original')
            else:continue
            if original.exists():
                if original.stat().st_size!=record['original_bytes'] or _hash(original)!=record['original_sha256']:
                    raise ValueError('legacy original changed before retirement')
                reclaimable+=record['original_bytes']-record['archive_bytes']
                if retire_originals:
                    _safe_root(original).unlink();_sync_dir(root)
                    reclaimed+=record['original_bytes']-record['archive_bytes'];compacted+=1
    return {'kind':'paper_history_compaction','status':'verified','files_compacted':compacted,
            'bytes_reclaimed':reclaimed,'bytes_reclaimable':reclaimable}
