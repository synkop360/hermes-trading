"""Disabled worker-owner capture capability, valid only during its callback.

No socket installation, retirement or live request queue. An installed adapter
must honor the finite boundary and surface failures. Snapshots still require
separate controller/cohort certification and independent-copy verification.
"""
from contextlib import closing
import hashlib,json,math,sqlite3,time

from hermes_trading import archive_journal as journal
from hermes_trading.archive_snapshot import snapshot
from hermes_trading.archive_certificate import _verified_inventory
from hermes_trading.paired_paper import _profiles
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash


def configuration(limits,callback):
    value=limits.get('archive_exports')
    if value is None:
        if callback is not None:raise ValueError('archive callback requires explicit configuration')
        return None
    if (not isinstance(value,dict) or set(value)!={'schema_version','enabled','max_bytes','timeout_seconds'} or
            type(value['schema_version']) is not int or value['schema_version']!=1 or type(value['enabled']) is not bool or
            type(value['max_bytes']) is not int or not 0 < value['max_bytes'] <= 128*1024*1024 or
            type(value['timeout_seconds']) not in (int,float) or not math.isfinite(value['timeout_seconds']) or
            not 0 < value['timeout_seconds'] <= 30):
        raise ValueError('archive boundary configuration differs')
    if value['enabled'] is not True:
        if callback is not None:raise ValueError('archive callback is disabled')
        return None
    if not callable(callback):raise ValueError('archive configuration requires an explicit callback adapter')
    return value


class WorkerCapture:
    def __init__(self,root,owner,stats,manifest,release,policy,limits,*,clock=time.monotonic):
        self.root=root;self._owner=owner;self.complete=stats['complete'];self._revision=stats.get('archive_revision',0)
        self._input=stats['latest']['input_hash'];self._manifest=_hash(manifest);self._release=_hash(release['hashes'])
        self._experiment=policy['experiment_id'];self._limits=limits;self._clock=clock
        self._deadline=clock()+limits['timeout_seconds'];self._active=True;self.completed_exports=0

    def remaining(self):
        self.check();return self._deadline-self._clock()

    def check(self):
        if not self._active:raise ValueError('archive worker boundary has expired')
        if self._clock()>=self._deadline:raise TimeoutError('archive worker boundary deadline exceeded')

    def close(self):self._active=False

    def capture(self,target,*,expected_complete):
        self.check()
        if type(expected_complete) is not int or expected_complete!=self.complete:
            raise ValueError('archive expected completed cut differs')
        if self.completed_exports:raise ValueError('archive boundary already captured its one cut')
        observed={}
        def validate(stage,inventory,check):
            self.check();_verified_inventory(stage,inventory,check)
            manifest=json.loads((stage/'manifest.json').read_text(encoding='utf-8'))
            if _hash(manifest)!=self._manifest:raise ValueError('archive captured cohort differs')
            primary=_profiles(stage,manifest)[('adaptive',resolve_primary(manifest).wallet_eur)][0]
            with closing(sqlite3.connect((primary/'paper_publications.sqlite').resolve().as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)) as db:
                db.set_progress_handler(lambda:int(self._clock()>=self._deadline),1000)
                checkpoint=journal._checkpoint(db);data=journal._read_journal(db,checkpoint,check=check)
                if (data['pending'] or data['complete']!=self.complete or data['cohort_sha256']!=self._manifest or
                        (0 if checkpoint is None else checkpoint['revision'])!=self._revision or
                        not data['rows'] or data['rows'][-1]['input_hash']!=self._input):
                    raise ValueError('archive captured completed cut differs')
                row=db.execute('SELECT payload FROM paired_batches ORDER BY rowid DESC LIMIT 1').fetchone()
                packet=journal.load_packet(db,row[0])
                if _hash(packet['release'])!=self._release:raise ValueError('archive captured release differs')
                observed.update(complete=self.complete,archive_revision=self._revision,cohort_sha256=self._manifest,
                    release_sha256=self._release,prefix_sha256=journal._prefix(checkpoint,data['rows'],self.complete),
                    latest_input_hash=self._input,checkpoint_sha256=_hash(checkpoint))
            self.check()
        result=snapshot(self.root,target,enabled=True,max_bytes=self._limits['max_bytes'],timeout_seconds=self.remaining(),
            clock=self._clock,owner_db=self._owner,role='worker',validate=validate)
        self.check();self.completed_exports+=1
        return dict(result,experiment_id=self._experiment,cut=observed,cohort_financial_certificate_verified=False,
            retirement_authorized=False,independent_backup_verified=False)
