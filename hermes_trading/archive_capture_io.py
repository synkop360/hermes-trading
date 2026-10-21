"""Admitted capture I/O shared by disabled V2 snapshot owners."""
import hashlib
import math
import os
import sqlite3

from hermes_trading.archive_capacity import CapacityLease,_safe_info
from hermes_trading.archive_store import _root,sync_file


class CaptureSourceChanged(ValueError):
    """Retain owned copies for reconciliation; source cut is no longer trusted."""


class CaptureBudget:
    def __init__(self,lease,owner,deadline,*,ceiling=30):
        if not isinstance(lease,CapacityLease) or lease.owner is not owner:
            raise ValueError('capture requires its original live admitted owner')
        self.lease=lease;self.clock=owner.domain.clock;self.started=self.clock()
        if (type(deadline) not in (int,float) or not math.isfinite(deadline) or
                not math.isfinite(self.started) or not self.started<deadline<=self.started+ceiling):
            raise ValueError('capture absolute deadline exceeds its original bound')
        self.deadline=deadline;self.check()

    def progress(self):
        """Read-only progress grants no storage; retain the original live lease."""
        now=self.clock()
        if not math.isfinite(now) or now<self.started:raise ValueError('capture clock regressed')
        if now>=self.deadline:raise TimeoutError('capture original absolute deadline exceeded')
        return self.lease.owner

    def check(self,*,remaining_peak_bytes=0):
        self.progress()
        return self.lease.check(remaining_peak_bytes=remaining_peak_bytes)

    def remaining(self):
        self.check();return self.deadline-self.clock()


def backup_database(source,target,budget,*,max_bytes,validate,check_source):
    """Copy a quiescent connection; never query destination from progress."""
    check_source()
    if source.in_transaction:raise ValueError('capture backup source must have no transaction')
    target=budget.lease.require_path(_root(target))
    expected_bytes=source.execute('PRAGMA page_count').fetchone()[0]*source.execute('PRAGMA page_size').fetchone()[0]
    if not 0<expected_bytes<=max_bytes:raise ValueError('capture database exceeds original byte bound')
    budget.check(remaining_peak_bytes=expected_bytes+65536)
    flags=os.O_CREAT|os.O_EXCL|os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(target,flags,0o600)
    try:
        info=os.fstat(fd);identity=(info.st_dev,info.st_ino)
    finally:os.close(fd)
    def original_target():
        check_source();current=_safe_info(target)
        if (current.st_dev,current.st_ino)!=identity:raise ValueError('capture copy target was replaced')
        if current.st_size>expected_bytes:raise ValueError('capture copy grew beyond original source page bound')
    copy=None
    try:
        original_target()
        copy=sqlite3.connect(target.as_uri()+'?mode=rw',uri=True,timeout=0,isolation_level=None)
        original_target();copy.execute('PRAGMA synchronous=FULL')
        copy.execute('PRAGMA temp_store=MEMORY');copy.execute('PRAGMA cache_spill=OFF')
        source.backup(copy,pages=16,sleep=.01,progress=lambda *_:original_target())
        original_target();budget.check()
        copy.set_progress_handler(lambda:int(budget.clock()>=budget.deadline),1000)
        evidence=validate(copy,target)
        # quick_check opens SQLite's internal empty TEMP schema. Validate the
        # exact main-only inventory first; the dedicated connection then closes.
        if copy.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('capture copy quick check failed')
        original_target()
    except sqlite3.OperationalError as error:
        if 'interrupted' in str(error).lower():raise TimeoutError('capture copy verification deadline exceeded') from error
        raise
    finally:
        if copy is not None:copy.close()
    sync_file(target);original_target()
    digest=hashlib.sha256();size=0
    with target.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=identity:raise ValueError('capture opened copy identity differs')
        while raw:=stream.read(1024*1024):
            original_target();size+=len(raw)
            if size>max_bytes:raise ValueError('capture copy byte bound exceeded')
            digest.update(raw)
    original_target();budget.check()
    return dict(evidence,database_bytes=size,database_sha256=digest.hexdigest())
