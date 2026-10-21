"""Disabled owner-held receiver export and full original-chain restore.

One bounded ZIP_STORED artifact contains an original SQLite backup and a pinned
certificate. Atomic hard-link publication never replaces an existing target.
This exports neither secrets nor nonce state and never authorizes retirement.
"""
from contextlib import closing,contextmanager,nullcontext
from copy import deepcopy
import hashlib,json,math,os,shutil,sqlite3,stat,struct,time,uuid,zipfile

from hermes_trading import archive_witness as witness
from hermes_trading import archive_store as store
from hermes_trading.reflection_supervision import _hash,_json

MAX_BYTES=2**31-1
CHUNK=1024*1024
PURPOSE='paired_receiver_export_v1'
CERTIFICATE_FIELDS={'schema_version','purpose','binding','identity','head','database_bytes',
    'database_sha256','schema_sha256','rows_sha256','event_count'}


class _Budget:
    def __init__(self,max_bytes,seconds,ceiling,clock):
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:raise ValueError('receiver export byte budget differs')
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0 < seconds <= ceiling:
            raise ValueError('receiver export deadline differs')
        self.max_bytes=max_bytes;self.clock=clock;self.started=clock();self.deadline=self.started+seconds;self.check()
    def check(self):
        at=self.clock()
        if not math.isfinite(at) or at < self.started:raise ValueError('receiver export clock differs')
        if at >= self.deadline:raise TimeoutError('receiver export deadline exceeded')
    def remaining(self):self.check();return self.deadline-self.clock()
    def attach(self,db):
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,witness.MAX_EVENT_BYTES+2048)
        db.set_progress_handler(lambda:int(self.clock() >= self.deadline),1000)


@contextmanager
def _regular(path):
    path=store._root(path)
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0)
    descriptor=os.open(path,flags)
    with os.fdopen(descriptor,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('receiver export must be a regular file')
        yield stream


def _digest(stream,budget):
    stream.seek(0);digest=hashlib.sha256();size=0
    while raw:=stream.read(CHUNK):
        budget.check();size+=len(raw)
        if size>budget.max_bytes:raise ValueError('receiver export bytes exceed budget')
        digest.update(raw)
    return size,digest.hexdigest()


def _fingerprint(db,budget):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables!={'witness_meta','witness_events'}:raise ValueError('receiver export schema contains unknown tables')
    schema=db.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()
    digest=hashlib.sha256(b'paired_receiver_original_rows_v1');counts={}
    for table,columns in (('witness_meta','id,witness_id,genesis_sha256,identity_sha256'),
                          ('witness_events','sequence,operation_id,payload,sha256')):
        digest.update(table.encode()+b'\0');count=0
        for row in db.execute('SELECT '+columns+' FROM '+table+' ORDER BY rowid'):
            if count%128==0:budget.check()
            count+=1
            if count>witness.MAX_EVENTS:raise ValueError('receiver export row budget exceeded')
            for value in row:
                if type(value) is int:kind=b'I';raw=str(value).encode()
                elif isinstance(value,str):kind=b'S';raw=value.encode()
                else:raise ValueError('receiver export original row type differs')
                digest.update(kind+len(raw).to_bytes(8,'big'));digest.update(raw)
        counts[table]=count
    if counts['witness_meta']!=1:raise ValueError('receiver export metadata differs')
    return {'schema_sha256':_hash(schema),'rows_sha256':digest.hexdigest(),'event_count':counts['witness_events']}


def _certificate(value,binding,identity,expected_head):
    if (not isinstance(value,dict) or set(value)!=CERTIFICATE_FIELDS or type(value['schema_version']) is not int or
            value['schema_version']!=1 or value['purpose']!=PURPOSE or
            _hash(value['binding'])!=_hash(binding) or _hash(value['identity'])!=_hash(identity)):
        raise ValueError('receiver export certificate binding differs')
    head=witness._worker_head(value['head'],binding,identity)
    if head['head_sha256']!=expected_head:raise ValueError('receiver export original head/cut differs')
    if type(value['database_bytes']) is not int or not 0 < value['database_bytes'] <= MAX_BYTES:
        raise ValueError('receiver export database byte budget differs')
    if type(value['event_count']) is not int or value['event_count']!=head['sequence']+1:
        raise ValueError('receiver export original event count differs')
    for field in ('database_sha256','schema_sha256','rows_sha256'):witness.journal._digest(value[field])


def _zip(stream,budget,binding,identity,expected_head,*,expected_certificate=None,output=None):
    size=os.fstat(stream.fileno()).st_size
    if not 22 <= size <= budget.max_bytes:raise ValueError('receiver export archive byte budget differs')
    # Bound the central directory before ZipFile can allocate/parse it. ZIP64,
    # comments, multiple disks and arbitrary member names are unavailable.
    stream.seek(-22,os.SEEK_END);end=struct.unpack('<4s4H2LH',stream.read(22))
    if (end[0]!=b'PK\x05\x06' or end[1:5]!=(0,0,2,2) or end[7]!=0 or
            end[5]>4096 or end[6]+end[5]!=size-22):
        raise ValueError('receiver export central directory differs')
    with zipfile.ZipFile(stream) as archive:
        infos=archive.infolist()
        if len(infos)!=2 or {item.filename for item in infos}!={'receiver.sqlite','certificate.json'}:
            raise ValueError('receiver export member scope differs')
        for item in infos:
            if item.compress_type!=zipfile.ZIP_STORED or item.flag_bits!=0 or item.compress_size!=item.file_size:
                raise ValueError('receiver export member encoding differs')
        cert_info=archive.getinfo('certificate.json')
        if cert_info.file_size>8192:raise ValueError('receiver export certificate exceeds byte bound')
        raw=archive.read(cert_info);value=json.loads(raw)
        if _json(value).encode()!=raw:raise ValueError('receiver export certificate must be canonical')
        _certificate(value,binding,identity,expected_head)
        if expected_certificate is not None and _hash(value)!=_hash(expected_certificate):
            raise ValueError('receiver export certificate changed')
        info=archive.getinfo('receiver.sqlite')
        if info.file_size!=value['database_bytes']:raise ValueError('receiver export database bytes differ')
        digest=hashlib.sha256();copied=0
        with archive.open(info) as source,(output.open('xb') if output is not None else nullcontext(None)) as destination:
            while raw:=source.read(CHUNK):
                budget.check();copied+=len(raw)
                if copied>budget.max_bytes:raise ValueError('receiver export decoded bytes exceed budget')
                digest.update(raw)
                if destination is not None:destination.write(raw)
            if destination is not None:destination.flush();os.fsync(destination.fileno())
        if copied!=value['database_bytes'] or digest.hexdigest()!=value['database_sha256']:
            raise ValueError('receiver export database digest differs')
    budget.check();return value


def _stage(target):
    target=store._root(target)
    if target.exists():raise ValueError('receiver export requires a new target')
    target.parent.mkdir(parents=True,exist_ok=True)
    stage=target.with_name('.rx-'+uuid.uuid4().hex);stage.mkdir()
    return target,stage


def _cleanup(stage,target):
    if stage is not None and stage.exists():
        checked=store._root(stage).resolve()
        if checked!=stage or checked.parent!=target.parent.resolve() or not stage.name.startswith('.rx-'):
            raise ValueError('receiver export cleanup target escaped owned staging parent')
        shutil.rmtree(checked)


def _publish(source,target):
    store.sync_file(source)
    # Same-filesystem atomic no-replace publication. Unsupported filesystems
    # fail closed; no copy/rename fallback exposes a partial target.
    os.link(source,target)
    return store._sync_directory(target.parent)


def export_receiver(receiver,target,*,enabled=False,expected_head_sha256,max_bytes,timeout_seconds,fault=None):
    witness._enabled(enabled);witness._enabled(receiver.enabled);witness.journal._digest(expected_head_sha256)
    budget=_Budget(max_bytes,timeout_seconds,30,receiver.clock)
    target=store._root(target);stage=None;source_checked=False
    if target==receiver.path or target in receiver.path.parents or receiver.path in target.parents:
        raise ValueError('receiver export source and target must be separate')
    if target.exists():raise ValueError('receiver export requires a new target')
    if not receiver._gate.acquire(blocking=False):raise ValueError('receiver ownership is busy')
    try:
        receiver._begin(budget.remaining(),timeout_ceiling_seconds=30);receiver._db.commit();source_checked=True
        if receiver._stat()!=receiver._stamp:raise ValueError('receiver source changed before export')
        head=deepcopy(receiver._head)
        if head['head_sha256']!=expected_head_sha256:raise ValueError('receiver export head changed; reconcile cut first')
        source_bytes=receiver._db.execute('PRAGMA page_count').fetchone()[0]*receiver._db.execute('PRAGMA page_size').fetchone()[0]
        if source_bytes+16384>max_bytes:raise ValueError('receiver export byte budget exceeded')
        target.parent.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(target.parent).free < 2*source_bytes+65536:
            raise ValueError('receiver export observed staging headroom unavailable')
        target,stage=_stage(target);database=stage/'receiver.sqlite'
        original=_fingerprint(receiver._db,budget)
        with closing(sqlite3.connect(database)) as copy:
            receiver._db.backup(copy,pages=16,sleep=.01,progress=lambda *_:budget.check())
        if fault:fault('after_backup',stage)
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as copy:
            budget.attach(copy)
            if copy.execute('PRAGMA quick_check').fetchone()[0]!='ok' or _fingerprint(copy,budget)!=original:
                raise ValueError('receiver export copy content differs')
        with _regular(database) as stream:size,digest=_digest(stream,budget)
        certificate={'schema_version':1,'purpose':PURPOSE,'binding':deepcopy(receiver.binding),
            'identity':deepcopy(receiver.identity),'head':head,'database_bytes':size,'database_sha256':digest,**original}
        raw=_json(certificate).encode()
        if len(raw)>8192:raise ValueError('receiver export certificate byte budget exceeded')
        artifact=stage/'export.zip'
        with zipfile.ZipFile(artifact,'x',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
            info=zipfile.ZipInfo('receiver.sqlite');info.file_size=size;info.compress_type=zipfile.ZIP_STORED
            written=0
            with _regular(database) as source,archive.open(info,'w',force_zip64=False) as output:
                while chunk:=source.read(CHUNK):
                    if fault:fault('archive_chunk',stage)
                    budget.check();written+=len(chunk)
                    if written>size:raise ValueError('receiver export database changed while archiving')
                    output.write(chunk)
            if written!=size:raise ValueError('receiver export database changed while archiving')
            budget.check();archive.writestr('certificate.json',raw)
        if fault:fault('before_publish',stage)
        with _regular(artifact) as stream:
            _zip(stream,budget,receiver.binding,receiver.identity,expected_head_sha256,expected_certificate=certificate)
            archive_bytes,archive_digest=_digest(stream,budget)
        if receiver._stat()!=receiver._stamp:raise ValueError('receiver source changed during export')
        budget.check();durable=_publish(artifact,target)
        if fault:fault('after_publish',stage)
        return {'archive_sha256':archive_digest,'archive_bytes':archive_bytes,'head':head,
            'event_count':original['event_count'],'reservation_pending':head['state']['reservation'] is not None,
            'directory_fsync_verified':durable,'independent_backup_verified':False,'retirement_authorized':False,
            'nonce_state_included':False,'full_chain_restore_checked':False,'hard_write_quota_verified':False}
    except BaseException:
        if receiver._db is not None:
            try:
                receiver._db.set_progress_handler(None,0);receiver._db.rollback()
                if not source_checked or receiver._stat()!=receiver._stamp:receiver.close()
            except BaseException:receiver.close()
        raise
    finally:
        try:_cleanup(stage,target)
        finally:receiver._gate.release()


def restore_receiver_export(archive,target,*,enabled=False,expected_sha256,binding,identity,
                            expected_head_sha256,max_bytes,timeout_seconds,clock=time.monotonic):
    witness._enabled(enabled);witness._identity(identity)
    witness.journal._digest(expected_sha256);witness.journal._digest(expected_head_sha256)
    budget=_Budget(max_bytes,timeout_seconds,120,clock);archive=store._root(archive);target=store._root(target);stage=None
    if target.exists():raise ValueError('receiver restore requires a new target')
    if archive==target:raise ValueError('receiver restore source and target must be separate')
    try:
        with _regular(archive) as stream:
            _,digest=_digest(stream,budget)
            if digest!=expected_sha256:raise ValueError('receiver export archive digest differs')
            target,stage=_stage(target);database=stage/'receiver.sqlite'
            certificate=_zip(stream,budget,binding,identity,expected_head_sha256,output=database)
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
            budget.attach(db)
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('receiver restore SQLite integrity differs')
            fingerprint=_fingerprint(db,budget)
            if any(fingerprint[key]!=certificate[key] for key in fingerprint):raise ValueError('receiver restore original rows differ')
            head,_,_=witness._load(db,binding,identity,budget.check)
            if _hash(head)!=_hash(certificate['head']):raise ValueError('receiver restore original head differs')
        budget.check();durable=_publish(database,target)
        return {'head':head,'event_count':certificate['event_count'],'archive_sha256':expected_sha256,
            'directory_fsync_verified':durable,'full_original_chain_verified':True,
            'independent_backup_verified':False,'retirement_authorized':False,'activated':False}
    finally:_cleanup(stage,target)
