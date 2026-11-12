"""Disabled V2 receiver, full preparation followed by exclusively owned reads.

No event is pruned. A new process revalidates the complete original history;
its independently verified recovery floor and whole-state capacity remain
deployment requirements. Preparation alone does not certify independence.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib,sqlite3,threading,time,uuid

from hermes_trading import archive_protective_witness as witness
from hermes_trading.archive_store import _root
from hermes_trading.reflection_supervision import _hash,_json,_now


class ProtectiveReceiver:
    def __init__(self,path,binding,identity,*,enabled=False,max_bytes=witness.original.MAX_WITNESS_BYTES,clock=time.monotonic):
        witness._binding(binding);witness._identity(identity)
        self.path=_root(path);self.binding=deepcopy(binding);self.identity=deepcopy(identity)
        self.enabled=enabled;self.max_bytes=max_bytes;self.clock=clock
        self._db=None;self._head=None;self._meta=None;self._tail=None;self._stamp=None;self._floor=None
        self._thread=None;self._gate=threading.Lock()

    def _check_thread(self):
        if self._thread is not None and self._thread!=threading.get_ident():raise ValueError('protective receiver requires its original owner thread')

    def close(self):
        self._check_thread()
        if self._db is not None:
            self._db.set_progress_handler(None,0);self._db.close()
        self._db=None;self._head=None;self._meta=None;self._tail=None;self._stamp=None

    def _stat(self):
        value=_root(self.path).stat()
        if not value.st_ino:raise ValueError('protective receiver file ownership identity unavailable')
        return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns

    def _same_file(self):
        if self._stamp is not None and self._stat()[:2]!=self._stamp[:2]:raise ValueError('protective receiver file ownership changed')

    def _remember_commit(self,head):self._floor=deepcopy(head)

    def prepare(self,*,timeout_seconds=120):
        witness.original._enabled(self.enabled);self._check_thread()
        if not self._gate.acquire(blocking=False):raise ValueError('protective receiver ownership busy')
        budget=None
        try:
            self.close();self._thread=threading.get_ident()
            budget=witness.original._ReceiverBudget(self.path,self.max_bytes,timeout_seconds,self.clock,timeout_ceiling_seconds=120)
            before=self._stat()
            self._db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)
            budget.attach(self._db);self._db.execute('PRAGMA locking_mode=EXCLUSIVE');self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('BEGIN EXCLUSIVE')
            head,meta=witness._load(self._db,self.binding,self.identity,budget.check_time)
            if self._floor is not None:
                row=self._db.execute('SELECT sha256 FROM protective_events WHERE sequence=?',(self._floor['sequence'],)).fetchone()
                if head['sequence']<self._floor['sequence'] or row!=(self._floor['head_sha256'],):
                    raise ValueError('protective receiver verified floor regressed; rollback requires recovery')
            tail=self._db.execute('SELECT sequence,payload,sha256 FROM protective_events ORDER BY sequence DESC LIMIT 1').fetchone()
            budget.check()
            if self._stat()[:2]!=before[:2]:raise ValueError('protective receiver ownership changed during preparation')
            self._db.commit();self._head=head;self._meta=meta;self._tail=tail;self._floor=deepcopy(head)
            self._stamp=self._stat();self._version=self._db.execute('PRAGMA data_version').fetchone()[0]
            return deepcopy(head)
        except BaseException as error:self._failure(error,budget)
        finally:self._gate.release()

    def _begin(self,timeout_seconds,request_deadline=None,*,timeout_ceiling_seconds=10):
        witness.original._enabled(self.enabled)
        if self._db is None or self._head is None:raise ValueError('protective receiver requires full preparation')
        budget=witness._RequestBudget(self.path,self.max_bytes,timeout_seconds,self.clock,
            request_deadline=request_deadline,timeout_ceiling_seconds=timeout_ceiling_seconds)
        if self._stat()!=self._stamp:raise ValueError('protective receiver file changed outside owned history')
        budget.attach(self._db)
        if self._db.execute('PRAGMA locking_mode').fetchone()[0].lower()!='exclusive':raise ValueError('protective receiver ownership differs')
        self._db.execute('BEGIN IMMEDIATE')
        if self._db.execute('PRAGMA data_version').fetchone()[0]!=self._version:raise ValueError('protective receiver data changed outside ownership')
        meta=self._db.execute('SELECT payload FROM protective_meta WHERE id=1').fetchone()
        tail=self._db.execute('SELECT sequence,payload,sha256 FROM protective_events ORDER BY sequence DESC LIMIT 1').fetchone()
        if meta!=(_json(self._meta),) or tail!=self._tail:raise ValueError('protective receiver verified head changed')
        budget.check();return budget

    def _failure(self,error,budget):
        self.close()
        if isinstance(error,sqlite3.Error) and budget is not None:budget.sql_error(error)
        raise error

    @contextmanager
    def capture(self,lease,*,deadline):
        """Retain the verified receiver gate through a non-mutating snapshot."""
        from hermes_trading.archive_capture_io import CaptureBudget
        self._check_thread()
        budget=CaptureBudget(lease,lease.owner,deadline)
        if self.clock is not lease.owner.domain.clock:
            raise ValueError('receiver capture requires the original domain clock')
        lease.require_path(self.path)
        if not self._gate.acquire(blocking=False):raise ValueError('protective receiver ownership busy')
        capture=None
        try:
            self._begin(budget.remaining(),deadline,timeout_ceiling_seconds=30)
            # SQLite backup cannot progress from this connection's write txn.
            # Its EXCLUSIVE lock and the outer owner remain held after commit.
            self._db.commit();budget.check()
            capture=ReceiverCapture(self,budget)
            yield capture
            capture.verify_source();budget.check()
        except BaseException:
            try:
                if capture is None or self._stat()!=self._stamp:self.close()
            except BaseException:self.close()
            raise
        finally:
            if capture is not None:capture._active=False
            try:
                if self._db is not None and self._db.in_transaction:self._db.rollback()
            finally:self._gate.release()

    def read(self,*,timeout_seconds=10,request_deadline=None):
        self._check_thread()
        if not self._gate.acquire(blocking=False):raise ValueError('protective receiver ownership busy')
        budget=None
        try:
            budget=self._begin(timeout_seconds,request_deadline);budget.check();self._db.commit();budget.check()
            if self._stat()!=self._stamp:raise ValueError('protective receiver file changed during read')
            return deepcopy(self._head)
        except BaseException as error:self._failure(error,budget)
        finally:self._gate.release()

    def apply(self,view,*,action='check',active_token=None,expected_head_sha256,operation_id,now=None,timeout_seconds=10,fault=None,
              request_deadline=None):
        self._check_thread()
        if not self._gate.acquire(blocking=False):raise ValueError('protective receiver ownership busy')
        budget=None
        try:
            budget=self._begin(timeout_seconds,request_deadline)
            request=dict(action=action,view=view,active_token=active_token,expected_head_sha256=expected_head_sha256,operation_id=operation_id)
            head,event=witness._transact(self._db,self._head,self._meta,request,self.identity,_now(now),budget,
                fault=fault,precommit=self._same_file,committed=self._remember_commit)
            if event is None and self._stat()!=self._stamp:raise ValueError('protective receiver file changed during unchanged operation')
            self._same_file();self._head=head;self._floor=deepcopy(head)
            if event is not None:self._tail=(head['sequence'],_json(event),head['head_sha256'])
            self._stamp=self._stat();self._version=self._db.execute('PRAGMA data_version').fetchone()[0]
            return deepcopy(head)
        except BaseException as error:self._failure(error,budget)
        finally:self._gate.release()


def _capture_fingerprint(db,check):
    schema=db.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()
    digest=hashlib.sha256(b'paired_protective_receiver_rows_v2');counts={}
    for table,columns in (('protective_meta','id,payload'),('protective_events','sequence,operation_id,payload,sha256')):
        digest.update(table.encode()+b'\0');count=0
        for row in db.execute('SELECT '+columns+' FROM '+table+' ORDER BY '+('id' if table=='protective_meta' else 'sequence')):
            if count%128==0:check()
            count+=1
            if count>witness.original.MAX_EVENTS:raise ValueError('receiver capture event bound exceeded')
            raw=_json(list(row)).encode();digest.update(len(raw).to_bytes(8,'big'));digest.update(raw)
        counts[table]=count
    if counts['protective_meta']!=1:raise ValueError('receiver capture metadata count differs')
    check()
    return dict(schema_sha256=_hash(schema),rows_sha256=digest.hexdigest(),event_count=counts['protective_events'])


class ReceiverCapture:
    def __init__(self,receiver,budget):
        self._receiver=receiver;self._budget=budget;self._active=True
        self._head=deepcopy(receiver._head)
        self._evidence=dict(head=self._head,**_capture_fingerprint(receiver._db,self._check))
        if self._evidence['event_count']!=self._head['sequence']+1:raise ValueError('receiver capture cut count differs')

    def _check(self):
        if not self._active:raise ValueError('receiver capture requires its original active context')
        self._receiver._check_thread();self._budget.progress()
        if self._receiver._stat()!=self._receiver._stamp:
            from hermes_trading.archive_capture_io import CaptureSourceChanged
            raise CaptureSourceChanged('receiver original source changed during capture')

    @property
    def evidence(self):
        self._check();return deepcopy(self._evidence)

    def verify_source(self):
        self._check()
        result=dict(head=deepcopy(self._receiver._head),**_capture_fingerprint(self._receiver._db,self._check))
        if result!=self._evidence:raise ValueError('receiver original capture cut changed')
        return self.evidence

    def backup_to(self,target):
        from hermes_trading.archive_capture_io import backup_database
        self.verify_source()
        def validate(db,path):
            head,_=witness._load(db,self._receiver.binding,self._receiver.identity,self._check)
            result=dict(head=head,**_capture_fingerprint(db,self._check))
            if result!=self._evidence:raise ValueError('receiver captured copy differs from original cut')
            return result
        result=backup_database(self._receiver._db,target,self._budget,max_bytes=self._receiver.max_bytes,
                               validate=validate,check_source=self._check)
        self.verify_source();return result

