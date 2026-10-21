"""Disabled exact-cut V2 receiver/nonce archive. No retirement or activation.

Caller-pinned digests select a particular cut. This local format supplies no
independent recovery anchor and does not reconcile newer offline worker claims.
"""
from contextlib import closing,contextmanager,nullcontext
from copy import deepcopy
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import uuid
import zipfile

from hermes_trading.archive_capacity import _safe_info
from hermes_trading.archive_capture_io import CaptureBudget,CaptureSourceChanged
from hermes_trading.archive_nonce import BoundedArchiveNonceStore,NONCE_PEAK_BYTES,PAGE_BYTES,MAX_ROWS,MAX_PAGES,_token
from hermes_trading.archive_protective_receiver import ProtectiveReceiver,_capture_fingerprint
from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_store import _root,sync_file,_sync_directory
from hermes_trading.reflection_supervision import _hash,_json

MAX_BYTES=2**31-1
MAX_CERT_BYTES=16384
PURPOSE='paired_protective_export_v2'
MEMBERS=('receiver.sqlite','nonces.sqlite','certificate.json')
CERT_FIELDS={'schema_version','purpose','capture_id','binding','identity','receiver','nonce'}
RECEIVER_FIELDS={'head','schema_sha256','rows_sha256','event_count','database_bytes','database_sha256'}
NONCE_FIELDS={'metadata','row_count','schema_sha256','rows_sha256','database_bytes','database_sha256'}


def _bounds(enabled,max_bytes,seconds,ceiling):
    if enabled is not True:raise ValueError('V2 pair archive is disabled')
    if type(max_bytes) is not int or not 0<max_bytes<=MAX_BYTES:raise ValueError('V2 pair artifact byte bound differs')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=ceiling:
        raise ValueError('V2 pair deadline exceeds original bound')


def _digest(value):return witness.original.journal._digest(value)


def _file_hash(path,budget,max_bytes):
    info=_safe_info(path);identity=(info.st_dev,info.st_ino)
    digest=hashlib.sha256();size=0
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=identity:raise ValueError('V2 pair opened file identity differs')
        while raw:=stream.read(1024*1024):
            budget.check();size+=len(raw)
            if size>max_bytes:raise ValueError('V2 pair file byte bound exceeded')
            digest.update(raw)
    after=_safe_info(path)
    if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=(
            info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns):
        raise ValueError('V2 pair file changed during hashing')
    return size,digest.hexdigest()


def _stage(target,lease):
    target=lease.require_path(_root(target));_safe_info(target.parent,directory=True)
    if os.path.lexists(target):raise FileExistsError('V2 pair target already exists')
    stage=target.with_name('.v2-pair-'+uuid.uuid4().hex);stage.mkdir()
    info=_safe_info(stage,directory=True)
    return stage,(info.st_dev,info.st_ino)


def _cleanup(stage,identity,target,lease):
    if stage is None or not stage.exists():return
    checked=lease.require_path(stage);info=_safe_info(checked,directory=True)
    if (checked.parent!=target.parent or not checked.name.startswith('.v2-pair-') or
            (info.st_dev,info.st_ino)!=identity or checked.resolve()!=checked):
        raise ValueError('V2 pair cleanup does not own its original staging directory')
    shutil.rmtree(checked)


@contextmanager
def _admitted_stage(owner,peak,seconds,ceiling,target):
    state=[None,None]
    try:
        with owner.admit(peak,timeout_seconds=seconds,timeout_ceiling_seconds=ceiling) as lease:
            yield lease,state
    finally:
        stage,identity=state;original_error=sys.exc_info()[1]
        if stage is not None and stage.exists():
            if isinstance(original_error,CaptureSourceChanged):
                original_error.add_note('Owned capture stage retained for source reconciliation: '+str(stage))
            else:
                try:
                    # Cleanup only, after the original operation admission has
                    # closed. This cannot renew its publication deadline.
                    with owner.admit(0,timeout_seconds=10) as cleanup_lease:
                        _cleanup(stage,identity,target,cleanup_lease)
                except BaseException as error:
                    if original_error is None:raise
                    original_error.add_note('Owned stage cleanup deferred: '+str(error))


def _identity(path):
    info=_safe_info(path);return info.st_dev,info.st_ino


def _verify_stage(stage,identity,files,budget,max_bytes):
    info=_safe_info(stage,directory=True)
    if (info.st_dev,info.st_ino)!=identity:raise ValueError('V2 pair original staging directory changed')
    for name,(file_identity,size,digest) in files.items():
        path=stage/name
        if _identity(path)!=file_identity or _file_hash(path,budget,max_bytes)!=(size,digest):
            raise ValueError('V2 pair original staged bytes/identity changed before publication')


def _certificate(value,binding,identity,expected_head,store_id,max_rows,max_pages):
    witness._binding(binding);witness._identity(identity);_token(store_id);_digest(expected_head)
    if (type(value) is not dict or set(value)!=CERT_FIELDS or type(value['schema_version']) is not int or
            value['schema_version']!=2 or value['purpose']!=PURPOSE or
            _hash(value['binding'])!=_hash(binding) or _hash(value['identity'])!=_hash(identity)):
        raise ValueError('V2 pair certificate original scope differs')
    _token(value['capture_id']);rx=value['receiver'];nonce=value['nonce']
    if type(rx) is not dict or set(rx)!=RECEIVER_FIELDS or type(nonce) is not dict or set(nonce)!=NONCE_FIELDS:
        raise ValueError('V2 pair certificate database fields differ')
    head=witness.validate_head(rx['head'],binding,identity)
    if head['head_sha256']!=expected_head or type(rx['event_count']) is not int or rx['event_count']!=head['sequence']+1:
        raise ValueError('V2 pair original head/event cut differs')
    for part in (rx,nonce):
        if type(part['database_bytes']) is not int or not 0<part['database_bytes']<=MAX_BYTES:
            raise ValueError('V2 pair database byte bound differs')
        for key in ('database_sha256','schema_sha256','rows_sha256'):_digest(part[key])
    meta=nonce['metadata']
    if (type(meta) is not dict or meta.get('store_id')!=store_id or
            meta.get('binding_sha256')!=_hash(binding) or meta.get('identity_sha256')!=_hash(identity) or
            type(meta.get('max_rows')) is not int or meta['max_rows']!=max_rows or
            type(meta.get('max_pages')) is not int or meta['max_pages']!=max_pages or
            type(nonce['row_count']) is not int or not 0<=nonce['row_count']<=max_rows or
            nonce['database_bytes']>max_pages*PAGE_BYTES):
        raise ValueError('V2 pair original nonce generation/limits differ')


def _write_archive(path,stage,certificate,budget,max_bytes,fault,copy_seals):
    raw=_json(certificate).encode()
    if len(raw)>MAX_CERT_BYTES:raise ValueError('V2 pair certificate exceeds bound')
    (stage/'certificate.json').write_bytes(raw)
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
        for name in MEMBERS:
            file=stage/name
            if name=='certificate.json':expected_size=len(raw);expected_hash=hashlib.sha256(raw).hexdigest()
            else:_,expected_size,expected_hash=copy_seals[name]
            info=zipfile.ZipInfo(name);info.file_size=expected_size
            written=0;digest=hashlib.sha256()
            with file.open('rb') as source,archive.open(info,'w',force_zip64=False) as output:
                if name in copy_seals:
                    opened=os.fstat(source.fileno())
                    if (opened.st_dev,opened.st_ino)!=copy_seals[name][0]:raise ValueError('V2 pair opened copy was replaced')
                while data:=source.read(1024*1024):
                    budget.check()
                    if fault:fault('archive_chunk',stage)
                    budget.check();written+=len(data);digest.update(data)
                    if written>expected_size:raise ValueError('V2 pair archive member grew beyond certificate')
                    output.write(data)
                    if path.stat().st_size>max_bytes:raise ValueError('V2 pair archive exceeds byte bound')
            if written!=expected_size or digest.hexdigest()!=expected_hash:
                raise ValueError('V2 pair archive member differs from certificate')
    sync_file(path)
    return _file_hash(path,budget,max_bytes),hashlib.sha256(raw).hexdigest()


def export_pair(receiver,nonce_store,target,*,enabled=False,expected_head_sha256,max_bytes,timeout_seconds=30,fault=None):
    _bounds(enabled,max_bytes,timeout_seconds,30)
    if not isinstance(receiver,ProtectiveReceiver) or not isinstance(nonce_store,BoundedArchiveNonceStore):
        raise ValueError('V2 pair requires original concrete receiver and nonce owners')
    nonce_store.require_scope(receiver.binding,receiver.identity);policy=nonce_store.policy
    owner=nonce_store.owner;clock=owner.domain.clock
    if receiver.clock is not clock:raise ValueError('V2 pair capture requires the original domain clock')
    target=_root(target);_digest(expected_head_sha256)
    if target in (receiver.path,nonce_store.path):raise ValueError('V2 pair target aliases a source')
    source_bytes=_safe_info(receiver.path).st_size+policy['max_pages']*PAGE_BYTES
    if source_bytes+65536>max_bytes:raise ValueError('V2 pair source cannot fit artifact bound')
    peak=NONCE_PEAK_BYTES+2*source_bytes+65536
    deadline=clock()+timeout_seconds;stage=stage_id=None
    with _admitted_stage(owner,peak,deadline-clock(),30,target) as (lease,staging):
        lease.require_path(target);lease.require_path(receiver.path)
        budget=CaptureBudget(lease,owner,deadline)
        try:
            with receiver.capture(lease,deadline=deadline) as rx,nonce_store.capture(lease,deadline=deadline) as nonce:
                if rx.evidence['head']['head_sha256']!=expected_head_sha256:raise ValueError('V2 pair source head changed')
                stage,stage_id=_stage(target,lease)
                r=rx.backup_to(stage/'receiver.sqlite')
                copy_seals={'receiver.sqlite':(_identity(stage/'receiver.sqlite'),r['database_bytes'],r['database_sha256'])}
                if fault:fault('after_receiver_backup',stage)
                n=nonce.backup_to(stage/'nonces.sqlite')
                copy_seals['nonces.sqlite']=(_identity(stage/'nonces.sqlite'),n['database_bytes'],n['database_sha256'])
                if fault:fault('after_nonce_backup',stage)
                certificate=dict(schema_version=2,purpose=PURPOSE,capture_id=uuid.uuid4().hex,
                    binding=deepcopy(receiver.binding),identity=deepcopy(receiver.identity),receiver=r,nonce=n)
                _certificate(certificate,receiver.binding,receiver.identity,expected_head_sha256,
                             policy['store_id'],policy['max_rows'],policy['max_pages'])
                artifact=stage/'pair.zip'
                _verify_stage(stage,stage_id,copy_seals,budget,max_bytes)
                (size,digest),cert_hash=_write_archive(artifact,stage,certificate,budget,max_bytes,fault,copy_seals)
                sealed={'pair.zip':(_identity(artifact),size,digest)}
                rx.verify_source();nonce.verify_source();budget.check()
                if fault:fault('before_publish',stage)
                rx.verify_source();nonce.verify_source();budget.check()
                _verify_stage(stage,stage_id,sealed,budget,max_bytes)
                # Atomic no-replace file publication. Remove our staged link
                # before domain inventory, which rejects multiply linked files.
                os.link(artifact,target);artifact.unlink();_sync_directory(target.parent)
                if fault:fault('after_publish',stage)
                budget.check()
                return dict(archive_sha256=digest,archive_bytes=size,certificate_sha256=cert_hash,
                    head_sha256=expected_head_sha256,nonce_store_id=policy['store_id'],nonce_rows=n['row_count'],
                    nonce_rows_sha256=n['rows_sha256'],independent_backup_verified=False,
                    runtime_resumption_authorized=False,retirement_authorized=False)
        finally:staging[:]=stage,stage_id


def _archive(stream,budget,max_bytes,binding,identity,head,store_id,max_rows,max_pages,certificate_hash,stage,fault):
    size=os.fstat(stream.fileno()).st_size
    if not 22<=size<=max_bytes:raise ValueError('V2 pair archive size differs')
    stream.seek(-22,os.SEEK_END);end=struct.unpack('<4s4H2LH',stream.read(22))
    if (end[0]!=b'PK\x05\x06' or end[1:5]!=(0,0,3,3) or end[7]!=0 or
            end[5]>4096 or end[6]+end[5]!=size-22):raise ValueError('V2 pair central directory differs')
    with zipfile.ZipFile(stream) as archive:
        infos=archive.infolist()
        if tuple(i.filename for i in infos)!=MEMBERS:raise ValueError('V2 pair member scope/order differs')
        offset=0
        for info in infos:
            if (info.compress_type!=zipfile.ZIP_STORED or info.flag_bits!=0 or info.extra or info.comment or
                    info.compress_size!=info.file_size or info.header_offset!=offset):
                raise ValueError('V2 pair member encoding/offset differs')
            stream.seek(offset);header=struct.unpack('<4s5H3L2H',stream.read(30))
            if (header[0]!=b'PK\x03\x04' or header[2]!=0 or header[3]!=0 or header[7]!=info.file_size or
                    header[8]!=info.file_size or header[9]!=len(info.filename) or header[10]!=0 or
                    stream.read(header[9])!=info.filename.encode()):raise ValueError('V2 pair local header differs')
            offset+=30+len(info.filename)+info.file_size
        if offset!=end[6]:raise ValueError('V2 pair unaccounted archive bytes')
        if infos[2].file_size>MAX_CERT_BYTES:raise ValueError('V2 pair certificate byte bound exceeded')
        raw=archive.read('certificate.json')
        if hashlib.sha256(raw).hexdigest()!=certificate_hash:raise ValueError('V2 pair pinned certificate digest differs')
        try:value=json.loads(raw)
        except (ValueError,RecursionError) as error:raise ValueError('V2 pair certificate malformed') from error
        if _json(value).encode()!=raw:raise ValueError('V2 pair certificate must be canonical')
        _certificate(value,binding,identity,head,store_id,max_rows,max_pages)
        copy_ids={}
        for name,part in (('receiver.sqlite','receiver'),('nonces.sqlite','nonce')):
            budget.check()
            expected=value[part];info=archive.getinfo(name)
            if info.file_size!=expected['database_bytes']:raise ValueError('V2 pair decoded byte count differs')
            digest=hashlib.sha256();copied=0
            with archive.open(info) as source,((stage/name).open('xb') if stage is not None else nullcontext(None)) as output:
                while data:=source.read(1024*1024):
                    budget.check();copied+=len(data)
                    if copied>max_bytes:raise ValueError('V2 pair decoded bytes exceed bound')
                    digest.update(data)
                    if output is not None:output.write(data)
                if output is not None:output.flush();os.fsync(output.fileno())
            if copied!=expected['database_bytes'] or digest.hexdigest()!=expected['database_sha256']:
                raise ValueError('V2 pair copied database digest differs')
            if stage is not None:copy_ids[name]=_identity(stage/name)
            if fault:fault('after_receiver_extract' if part=='receiver' else 'after_nonce_extract',stage)
            budget.check()
        if stage is not None:
            (stage/'certificate.json').write_bytes(raw);sync_file(stage/'certificate.json')
            copy_ids['certificate.json']=_identity(stage/'certificate.json')
    return value,copy_ids


def _verify_copies(stage,value,owner,budget,binding,identity,store_id,max_rows,max_pages):
    path=stage/'receiver.sqlite'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0,isolation_level=None)) as db:
        db.execute('PRAGMA temp_store=MEMORY');db.execute('PRAGMA cache_spill=OFF')
        sqlbudget=witness._RequestBudget(path,MAX_BYTES,budget.remaining(),budget.clock,
            timeout_ceiling_seconds=120,request_deadline=budget.deadline)
        sqlbudget.attach(db)
        head,_=witness._load(db,binding,identity,budget.check)
        result=dict(head=head,**_capture_fingerprint(db,budget.check))
        expected={k:v for k,v in value['receiver'].items() if k not in ('database_bytes','database_sha256')}
        if result!=expected:raise ValueError('V2 pair original receiver rows/head differ')
        if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('V2 pair receiver quick check failed')
    nonce=BoundedArchiveNonceStore(stage/'nonces.sqlite',owner=owner,expected_store_id=store_id,
        binding=binding,identity=identity,max_rows=max_rows,max_pages=max_pages,enabled=True)
    with nonce.capture(budget.lease,deadline=min(budget.deadline,budget.clock()+30)) as capture:
        expected={k:v for k,v in value['nonce'].items() if k not in ('database_bytes','database_sha256')}
        if capture.evidence!=expected:raise ValueError('V2 pair original nonce rows/floor differ')
    # Exact row/schema validation precedes this separate structural check:
    # quick_check opens SQLite's empty internal TEMP schema on its connection.
    with closing(sqlite3.connect((stage/'nonces.sqlite').as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
        db.execute('PRAGMA temp_store=MEMORY');db.set_progress_handler(lambda:int(budget.clock()>=budget.deadline),1000)
        if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('V2 pair nonce structural quick check failed')
    budget.check()


def _publish_directory(stage,target):
    if os.name=='nt':os.rename(stage,target)
    elif __import__('sys').platform.startswith('linux'):
        libc=ctypes.CDLL(None,use_errno=True)
        try:rename=libc.renameat2
        except AttributeError as error:raise OSError(errno.ENOTSUP,'atomic no-replace directory publication unavailable') from error
        rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
        rename.restype=ctypes.c_int
        if rename(-100,os.fsencode(stage),-100,os.fsencode(target),1)!=0:
            code=ctypes.get_errno();raise OSError(code,os.strerror(code),str(target))
    else:raise OSError(errno.ENOTSUP,'atomic no-replace directory publication unavailable')
    _sync_directory(target.parent)


def restore_pair(artifact,target,*,owner,enabled=False,expected_archive_sha256,expected_certificate_sha256,
                 binding,identity,expected_nonce_store_id,expected_head_sha256,max_bytes,timeout_seconds=120,
                 max_nonce_rows=MAX_ROWS,max_nonce_pages=MAX_PAGES,fault=None):
    _bounds(enabled,max_bytes,timeout_seconds,120)
    for digest in (expected_archive_sha256,expected_certificate_sha256,expected_head_sha256):_digest(digest)
    witness._binding(binding);witness._identity(identity);_token(expected_nonce_store_id)
    if (type(max_nonce_rows) is not int or not 1<=max_nonce_rows<=MAX_ROWS or
            type(max_nonce_pages) is not int or not 3<=max_nonce_pages<=MAX_PAGES):raise ValueError('V2 pair nonce limits differ')
    artifact=_root(artifact);target=_root(target)
    if artifact==target or target in artifact.parents or artifact in target.parents:raise ValueError('V2 pair restore paths overlap')
    source=_safe_info(artifact)
    if not 22<=source.st_size<=max_bytes:raise ValueError('V2 pair archive byte bound exceeded')
    deadline=owner.domain.clock()+timeout_seconds;peak=NONCE_PEAK_BYTES+2*source.st_size+65536
    stage=stage_id=None
    with _admitted_stage(owner,peak,deadline-owner.domain.clock(),120,target) as (lease,staging):
        lease.require_path(artifact);lease.require_path(target)
        budget=CaptureBudget(lease,owner,deadline,ceiling=120)
        try:
            _,digest=_file_hash(artifact,budget,max_bytes)
            if digest!=expected_archive_sha256:raise ValueError('V2 pair pinned archive digest differs')
            stage,stage_id=_stage(target,lease)
            with artifact.open('rb') as stream:
                opened=os.fstat(stream.fileno())
                if (opened.st_dev,opened.st_ino)!=(source.st_dev,source.st_ino):raise ValueError('V2 pair archive replaced')
                value,copy_ids=_archive(stream,budget,max_bytes,binding,identity,expected_head_sha256,expected_nonce_store_id,
                               max_nonce_rows,max_nonce_pages,expected_certificate_sha256,stage,fault)
            _verify_copies(stage,value,owner,budget,binding,identity,expected_nonce_store_id,max_nonce_rows,max_nonce_pages)
            if _file_hash(artifact,budget,max_bytes)[1]!=expected_archive_sha256:raise ValueError('V2 pair archive changed during restore')
            complete=dict(schema_version=2,purpose='paired_protective_restore_complete_v2',
                archive_sha256=expected_archive_sha256,certificate_sha256=expected_certificate_sha256,
                head_sha256=expected_head_sha256,nonce_store_id=expected_nonce_store_id,
                independent_backup_verified=False,runtime_resumption_authorized=False,retirement_authorized=False)
            marker=stage/'RESTORE_COMPLETE.json';marker.write_text(_json(complete),encoding='utf-8');sync_file(marker)
            sealed={}
            for name in (*MEMBERS,'RESTORE_COMPLETE.json'):
                size,digest=_file_hash(stage/name,budget,max_bytes)
                if name in copy_ids and _identity(stage/name)!=copy_ids[name]:raise ValueError('V2 pair extracted copy identity changed')
                if name in ('receiver.sqlite','nonces.sqlite'):
                    part=value['receiver' if name=='receiver.sqlite' else 'nonce']
                    if (size,digest)!=(part['database_bytes'],part['database_sha256']):
                        raise ValueError('V2 pair extracted bytes differ from original certificate')
                if name=='certificate.json' and digest!=expected_certificate_sha256:
                    raise ValueError('V2 pair restored certificate changed')
                sealed[name]=(_identity(stage/name),size,digest)
            _sync_directory(stage);budget.check()
            if fault:fault('before_publish',stage)
            budget.check();_verify_stage(stage,stage_id,sealed,budget,max_bytes)
            if {p.name for p in stage.iterdir()}!=set(sealed):raise ValueError('V2 pair restored member inventory changed')
            _publish_directory(stage,target)
            if fault:fault('after_publish',target)
            budget.check();return complete
        finally:staging[:]=stage,stage_id
