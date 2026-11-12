"""Disabled, exclusively owned, fully prepared original-history receiver.

Preparation validates the whole original chain. Repeated requests reuse that
verified prefix while this process retains the SQLite exclusive lease. No row
is pruned. A changed file or failed operation invalidates the preparation.
"""
from copy import deepcopy
import json
import sqlite3
import threading
import time
import uuid

from hermes_trading import archive_witness as witness
from hermes_trading.archive_store import _root
from hermes_trading.reflection_supervision import _hash,_json,_now


class VerifiedReceiver:
    def __init__(self,path,binding,identity,*,enabled=False,max_bytes=witness.MAX_WITNESS_BYTES,clock=time.monotonic):
        witness._identity(identity)
        if not isinstance(binding,dict) or set(binding)!={'witness_id','genesis_sha256'}:
            raise ValueError('receiver binding differs')
        witness._token(binding['witness_id']);witness.journal._digest(binding['genesis_sha256'])
        self.path=_root(path);self.binding=deepcopy(binding);self.identity=deepcopy(identity)
        self.enabled=enabled;self.max_bytes=max_bytes;self.clock=clock
        self._db=None;self._head=None;self._prior=None;self._stamp=None;self._floor=None
        self._gate=threading.Lock()

    def close(self):
        if self._db is not None:
            self._db.set_progress_handler(None,0);self._db.close()
        self._db=None;self._head=None;self._prior=None;self._stamp=None

    def _stat(self):
        stat=_root(self.path).stat()
        if not stat.st_ino:raise ValueError('receiver file ownership identity unavailable')
        return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)

    def _same_file(self):
        if self._stamp is not None and self._stat()[:2]!=self._stamp[:2]:
            raise ValueError('receiver file ownership changed')

    def prepare(self,*,timeout_seconds=120):
        witness._enabled(self.enabled)
        if not self._gate.acquire(blocking=False):raise ValueError('receiver ownership is busy')
        try:
            self.close()
            budget=witness._ReceiverBudget(self.path,self.max_bytes,timeout_seconds,self.clock,timeout_ceiling_seconds=120)
            original=self._stat()
            self._db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)
            budget.attach(self._db)
            self._db.execute('PRAGMA locking_mode=EXCLUSIVE');self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('BEGIN EXCLUSIVE')
            head,_,prior=witness._load(self._db,self.binding,self.identity,budget.check_time)
            if self._floor is not None:
                row=self._db.execute('SELECT sha256 FROM witness_events WHERE sequence=?',(self._floor['sequence'],)).fetchone()
                if head['sequence'] < self._floor['sequence'] or row!=(self._floor['head_sha256'],):
                    raise ValueError('receiver verified floor regressed or differs: rollback requires recovery')
            budget.check()
            if self._stat()[:2]!=original[:2]:raise ValueError('receiver file ownership changed during preparation')
            self._db.commit()
            self._head=head;self._prior=prior;self._stamp=self._stat()
            self._version=self._db.execute('PRAGMA data_version').fetchone()[0]
            self._floor=deepcopy(head)
            return deepcopy(head)
        except BaseException as error:
            self.close()
            if isinstance(error,sqlite3.Error):budget.sql_error(error)
            raise
        finally:self._gate.release()

    def _begin(self,timeout_seconds,*,timeout_ceiling_seconds=10):
        witness._enabled(self.enabled)
        if self._db is None or self._head is None:raise ValueError('receiver requires verified preparation before requests')
        budget=witness._ReceiverBudget(self.path,self.max_bytes,timeout_seconds,self.clock,timeout_ceiling_seconds=timeout_ceiling_seconds)
        if self._stat()!=self._stamp:raise ValueError('receiver file changed outside owned history')
        budget.attach(self._db)
        if self._db.execute('PRAGMA locking_mode').fetchone()[0]!='exclusive':raise ValueError('receiver ownership lease differs')
        self._db.execute('BEGIN IMMEDIATE')
        if self._db.execute('PRAGMA data_version').fetchone()[0]!=self._version:
            raise ValueError('receiver data changed outside ownership')
        meta=self._db.execute('SELECT witness_id,genesis_sha256,identity_sha256 FROM witness_meta WHERE id=1').fetchone()
        expected=(self.binding['witness_id'],self.binding['genesis_sha256'],_hash(self.identity))
        tail=self._db.execute('SELECT sequence,payload,sha256 FROM witness_events ORDER BY sequence DESC LIMIT 1').fetchone()
        if meta!=expected or tail!=(self._head['sequence'],_json(self._prior),self._head['head_sha256']):
            raise ValueError('receiver verified head changed')
        budget.check();return budget

    def _failure(self,error,budget):
        self.close()
        if isinstance(error,sqlite3.Error) and budget is not None:budget.sql_error(error)
        raise error

    def read(self,*,timeout_seconds=10):
        if not self._gate.acquire(blocking=False):raise ValueError('receiver ownership is busy')
        budget=None
        try:
            budget=self._begin(timeout_seconds);self._db.commit();budget.check()
            if self._stat()!=self._stamp:raise ValueError('receiver file changed during read')
            return deepcopy(self._head)
        except BaseException as error:self._failure(error,budget)
        finally:self._gate.release()

    def apply(self,view,*,action='check',expected_head_sha256,operation_id=None,active_token=None,
              now=None,timeout_seconds=10,fault=None):
        if not self._gate.acquire(blocking=False):raise ValueError('receiver ownership is busy')
        budget=None
        try:
            budget=self._begin(timeout_seconds)
            witness.journal._digest(expected_head_sha256)
            operation=uuid.uuid4().hex if operation_id is None else witness._token(operation_id)
            request={'action':action,'view':view,'active_token':active_token,
                'expected_head_sha256':expected_head_sha256,'operation_id':operation}
            head,prior=witness._transact(self._db,self._head,self._prior,request,self.identity,_now(now),budget,
                fault=fault,precommit=self._same_file)
            if head['head_sha256']==self._head['head_sha256'] and self._stat()!=self._stamp:
                raise ValueError('receiver file changed during unchanged operation')
            self._same_file();self._head=head;self._prior=prior;self._floor=deepcopy(head)
            self._stamp=self._stat();self._version=self._db.execute('PRAGMA data_version').fetchone()[0]
            return deepcopy(head)
        except BaseException as error:self._failure(error,budget)
        finally:self._gate.release()

    def export(self,target,*,enabled=False,expected_head_sha256,max_bytes,timeout_seconds,fault=None):
        from hermes_trading.archive_receiver_export import export_receiver
        return export_receiver(self,target,enabled=enabled,expected_head_sha256=expected_head_sha256,
            max_bytes=max_bytes,timeout_seconds=timeout_seconds,fault=fault)
