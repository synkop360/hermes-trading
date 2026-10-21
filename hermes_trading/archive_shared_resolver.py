"""Disabled original shared-history lookup through recorded store manifests.

The caller's verified current snapshot pin is the root of trust. This reader
does not establish a newest cut, stop a former owner, verify independent backup
or authorize retirement/recovery. Only the explicitly selected archive_store
format is supported. Full restored cohorts are disposable read inputs, never
worker targets. Store reads, restored input bytes and the separate union cache
have finite ceilings; their combined footprint still needs owner admission.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import tempfile
import time

from hermes_trading import archive_journal as journal,archive_store as store
from hermes_trading.archive_certificate import _database,_verified_inventory,certify
from hermes_trading.archive_coordinator import _review_history
from hermes_trading.archive_shared_history import _pin,_requests,shared_history
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.reflection_supervision import _hash,_now

FORMAT='recorded_archive_store_v1'


class _Budget:
    def __init__(self,max_bytes,seconds,clock):
        if type(max_bytes) is not int or not 4096<=max_bytes<=4*1024**3:
            raise ValueError('recorded history byte bound differs')
        if (type(seconds) not in (int,float) or not 0<seconds<=120 or not math.isfinite(seconds)):
            raise ValueError('recorded history deadline differs')
        self.limit=max_bytes;self.clock=clock;self.started=clock();self.last=self.started
        if (type(self.started) not in (int,float) or not 0<=self.started<=10**15 or not math.isfinite(self.started)):
            raise ValueError('recorded history clock differs')
        self.deadline=self.started+seconds;self.read_bytes=0;self.decoded_bytes=0
        self.check()

    def check(self):
        now=self.clock()
        if (type(now) not in (int,float) or not self.last<=now<=10**15 or not math.isfinite(now)):
            raise ValueError('recorded history clock regressed or differs')
        if now>=self.deadline:raise TimeoutError('recorded history absolute deadline exceeded')
        self.last=now

    def read(self,path,limit):
        self.check();remaining=self.limit-self.read_bytes
        if remaining<0:raise ValueError('recorded history store read byte bound exceeded')
        raw=store._read(path,min(limit,remaining));self.read_bytes+=len(raw);self.check()
        return raw

    def decoded(self,size):
        self.check();self.decoded_bytes+=size
        if self.decoded_bytes>self.limit:raise ValueError('recorded history restored input byte bound exceeded')


def _json_file(path,budget):
    # Restored metadata is already content-bound by the store manifest.
    budget.check()
    try:return json.loads(store._read(path,store.MAX_MANIFEST_BYTES))
    except (UnicodeError,ValueError,RecursionError) as error:
        raise ValueError('recorded history metadata is malformed') from error


def _chain(archive,head,requests,experiment,budget):
    reverse=[];seen=set()
    while head is not None:
        budget.check();store._digest(head)
        if head in seen or len(seen)>=1024:raise ValueError('recorded history archive chain exceeds bound or cycles')
        seen.add(head)
        raw=budget.read(archive/'manifests'/(head+'.json'),store.MAX_MANIFEST_BYTES)
        value=store.manifest_bytes(raw,head,check=budget.check)
        if value['experiment']!=experiment:raise ValueError('recorded history archive experiment differs')
        reverse.append((head,value));head=value['parent']
    chain=list(reversed(reverse));store.check_chain([value for _,value in chain])
    indices={digest:index for index,(digest,_) in enumerate(chain)};last=-1
    for request in reversed(requests):
        index=indices.get(request['archive_sha256'])
        if index is None or index<=last:raise ValueError('recorded history retirement archives are missing or out of order')
        last=index
    return chain


def _restore(archive,manifest,target,budget):
    budget.decoded(sum(row['bytes'] for row in manifest['files']))
    target.mkdir()
    for row in manifest['files']:
        budget.check();path=store._root(target/row['path']);path.parent.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256();size=0
        with path.open('xb') as stream:
            for index,obj in enumerate(row['objects']):
                budget.check();raw=store._inflate(budget.read(archive/'objects'/obj,store.CHUNK_BYTES+131072))
                expected=min(store.CHUNK_BYTES,row['bytes']-index*store.CHUNK_BYTES)
                if len(raw)!=expected or hashlib.sha256(raw).hexdigest()!=obj:
                    raise ValueError('recorded history original object differs')
                stream.write(raw);digest.update(raw);size+=len(raw)
        if size!=row['bytes'] or digest.hexdigest()!=row['sha256']:
            raise ValueError('recorded history restored original file differs')
    budget.check()


def _cohort(root,manifest,relative,cohort,experiment,now,budget):
    files={row['path']:row for row in manifest['files']}
    required={'snapshot_inventory.json','cohort_capture.json','worker/snapshot_inventory.json',
              'worker/manifest.json','supervisor/snapshot_inventory.json','worker/'+relative}
    if not required<=files.keys():raise ValueError('recorded history coordinated cohort files are missing')
    _verified_inventory(root,files['snapshot_inventory.json']['sha256'],budget.check)
    metadata=_json_file(root/'cohort_capture.json',budget)
    if (not isinstance(metadata,dict) or set(metadata)!={'schema_version','purpose','certificate','review_reconciliation',
            'worker_inventory_sha256','supervisor_inventory_sha256'} or type(metadata['schema_version']) is not int or
            metadata['schema_version']!=1 or metadata['purpose']!='paired_coordinated_capture_v1'):
        raise ValueError('recorded history coordinated metadata differs')
    worker_manifest=_json_file(root/'worker/manifest.json',budget)
    if _hash(worker_manifest)!=cohort:raise ValueError('recorded history cohort differs')
    selected=resolve_primary(worker_manifest)
    if selected.profile_directory(worker_manifest,'adaptive')+'/paper_publications.sqlite'!=relative:
        raise ValueError('recorded history selected journal differs')
    path=root/'worker'/relative
    with _database(path,check=budget.check) as db:
        source=journal._read_journal(db,journal._checkpoint(db),budget.check)
        row=db.execute("SELECT payload FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:raise ValueError('recorded history completed tail missing')
        release=load_packet(db,row[0])['release']
    stored=metadata['certificate']
    if not isinstance(stored,dict):raise ValueError('recorded history cut certificate missing')
    certificate=certify(root/'worker',expected_inventory_sha256=metadata['worker_inventory_sha256'],
        experiment_id=experiment,expected_release=release,through_sequence=stored['through_sequence'],
        supervisor_root=root/'supervisor',expected_supervisor_inventory_sha256=metadata['supervisor_inventory_sha256'],
        now=now,check=budget.check)
    if _hash(certificate)!=_hash(stored):raise ValueError('recorded history original cut certificate differs')
    if manifest['last_batch']!=source['complete'] or certificate['observed_complete']!=source['complete']:
        raise ValueError('recorded history manifest captured head differs')
    reviews=_review_history(root/'worker',root/'supervisor',now=now,check=budget.check)
    if _hash(reviews)!=_hash(metadata['review_reconciliation']):
        raise ValueError('recorded history original review reconciliation differs')
    return certificate,dict(path=path,sha256=files['worker/'+relative]['sha256'])


def _bind(request,certificate):
    for field in ('experiment_id','cohort_sha256','release_sha256','account_snapshots',
                  'through_sequence','prefix_sha256','next_input_hash','previous_checkpoint_sha256'):
        if _hash(request[field])!=_hash(certificate[field]):
            raise ValueError('recorded history retirement/certificate '+field+' differs')
    if certificate['observed_complete']>request['observed_complete']:
        raise ValueError('recorded history archive head exceeds retirement observation')


@contextmanager
def recorded_shared_history(current, *, enabled=False, format, archive_root,
                            shared_relative_path, expected_cohort_sha256,
                            expected_experiment_id, cache_root, max_bytes,
                            timeout_seconds, now=None, clock=time.monotonic):
    """Resolve exact recorded ancestors and yield a disposable report-only view.

    Current must be a caller-verified consistent snapshot with path/sha256.
    Original object-store manifest coverage and SQL retirement cuts are checked
    independently. No bare digest/path list substitutes for cohort evidence.
    The deadline spans restoration, native verification and consuming the view.
    """
    if enabled is not True:raise ValueError('recorded shared history is disabled')
    if format!=FORMAT:raise ValueError('recorded history archive format is unsupported')
    budget=_Budget(max_bytes,timeout_seconds,clock)
    store._digest(expected_cohort_sha256)
    if not isinstance(expected_experiment_id,str) or not 1<=len(expected_experiment_id)<=128:
        raise ValueError('recorded history experiment identity differs')
    relative=store._name(shared_relative_path)
    if not relative.endswith('/paper_publications.sqlite'):raise ValueError('recorded history journal member differs')
    if not isinstance(current,dict) or set(current)!={'path','sha256'}:
        raise ValueError('recorded history current pin differs')
    path=store._root(current['path']);store._digest(current['sha256'])
    cache_root=store._root(cache_root);archive=store._root(archive_root)
    if not cache_root.is_dir():raise ValueError('recorded history cache root must exist')
    budget.decoded(path.stat().st_size);_pin(path,current['sha256'],budget.check,max_bytes)
    with _database(path,check=budget.check) as db:
        checkpoint=journal._checkpoint(db);observed=journal._read_journal(db,checkpoint,budget.check)
        if observed['cohort_sha256']!=expected_cohort_sha256 or observed['pending']:
            raise ValueError('recorded history current cohort is inconsistent or pending')
        requests=_requests(db,checkpoint,expected_experiment_id,budget.check)
    view_now=_now(now)
    with tempfile.TemporaryDirectory(prefix='.recorded-history-',dir=cache_root) as temporary:
        stage=store._root(temporary);sources=[]
        if requests:
            if not archive.is_dir():raise ValueError('recorded history archive is unavailable')
            chain=_chain(archive,requests[0]['archive_sha256'],requests,expected_experiment_id,budget)
            by_digest={request['archive_sha256']:request for request in requests}
            for index,(digest,manifest) in enumerate(chain):
                target=stage/f'cohort-{index}';_restore(archive,manifest,target,budget)
                certificate,source=_cohort(target,manifest,relative,expected_cohort_sha256,
                    expected_experiment_id,view_now,budget)
                if digest in by_digest:_bind(by_digest[digest],certificate)
                sources.append(source)
        sources.append(current);budget.check()
        with shared_history(sources,enabled=True,expected_cohort_sha256=expected_cohort_sha256,
            expected_experiment_id=expected_experiment_id,cache_root=stage,max_bytes=max_bytes,
            timeout_seconds=budget.deadline-clock(),clock=clock) as view:
            budget.check();yield view;budget.check()
        _pin(path,current['sha256'],budget.check,max_bytes)
