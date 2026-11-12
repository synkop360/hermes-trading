"""Disabled local exact-cut coherence; never startup or retirement authority.

The caller must retain the expected digest outside restored evidence. Neither
API proves it is the latest cut, fences a former worker, or observes later
offline claims. Only the original verifier release is currently supported.
"""
from copy import deepcopy
import json,math,stat

from hermes_trading import archive_store as store
from hermes_trading import archive_protective_export as pair
from hermes_trading.archive_capacity import _safe_info
from hermes_trading.archive_capture_io import CaptureBudget
from hermes_trading.archive_certificate import certify,_database,_verified_inventory
from hermes_trading.archive_coordinator import _review_history
from hermes_trading.archive_nonce import NONCE_PEAK_BYTES,MAX_ROWS,MAX_PAGES
from hermes_trading.archive_protective_session import validate_cache,reconcile_claim,MAX_CACHE_BYTES
from hermes_trading.archive_protective_source import source_view
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash,_json,_now

PURPOSE='paired_original_cohort_recovery_cut_v2'
PAIR_MEMBERS=set(pair.MEMBERS)|{'RESTORE_COMPLETE.json'}
FLAGS=dict(independent_backup_verified=False,post_cut_activity_reconciled=False,
           runtime_resumption_authorized=False,retirement_authorized=False)


class _InspectionBudget(CaptureBudget):
    """Reads check retained ownership/deadline; growth gets full admission checks.

    The original lease is measured on entry, before/after nonce validation and
    on exit. Nonce capture retains its own full growth checks. Hashing sealed
    read-only files does not rescan every domain file on every read chunk.
    """
    def check(self,*,remaining_peak_bytes=0):
        value=self.clock()
        if not math.isfinite(value) or value<self.started:raise ValueError('inspection clock regressed')
        if value>=self.deadline:raise TimeoutError('inspection original absolute deadline exceeded')
        if remaining_peak_bytes:return self.lease.check(remaining_peak_bytes=remaining_peak_bytes)
        return self.lease.owner


def _preflight(cohort,restored,artifact,lease,budget,max_bytes):
    """Register/bound every path before file reads or any SQLite open."""
    files={};directories={};identities=set();total=0;visited=0
    stack=[lease.require_path(cohort),lease.require_path(restored)]
    while stack:
        budget.check();path=stack.pop();info=_safe_info(path,directory=True)
        ident=(info.st_dev,info.st_ino)
        if ident in identities:raise ValueError('recovery directory alias differs')
        identities.add(ident);directories[path]=ident
        for child in path.iterdir():
            budget.check();visited+=1
            if visited>2*store.MAX_FILES+16:raise ValueError('recovery inventory count bound exceeded')
            child=lease.require_path(store._root(child));info=child.lstat()
            if stat.S_ISDIR(info.st_mode):stack.append(child);continue
            info=_safe_info(child);ident=(info.st_dev,info.st_ino)
            if ident in identities:raise ValueError('recovery file alias differs')
            identities.add(ident);total+=info.st_size;files[child]=ident
            if total>max_bytes:raise ValueError('recovery aggregate byte bound exceeded')
    artifact=lease.require_path(artifact);info=_safe_info(artifact);ident=(info.st_dev,info.st_ino)
    if ident in identities:raise ValueError('recovery artifact alias differs')
    total+=info.st_size;files[artifact]=ident
    if total>max_bytes:raise ValueError('recovery aggregate byte bound exceeded')
    if {p.name for p in restored.iterdir()}!=PAIR_MEMBERS:
        raise ValueError('recovery pair member inventory or sidecar differs')
    return files,directories,total


def _read_json(path,limit):
    try:return json.loads(store._read(path,limit))
    except (ValueError,RecursionError) as error:raise ValueError('recovery evidence JSON differs') from error


def _cached(worker,binding,identity,head,budget):
    with _database(worker/'worker_runtime.sqlite',check=budget.check) as db:
        rows=db.execute('SELECT id,length(payload),length(sha256) FROM protective_cache').fetchall()
        if len(rows)!=1 or rows[0][0]!=1 or not 0<rows[0][1]<=MAX_CACHE_BYTES or rows[0][2]!=64:
            raise ValueError('recovery original cache shape/byte bound differs')
        raw,digest=db.execute('SELECT payload,sha256 FROM protective_cache').fetchone()
        cache=validate_cache(raw,digest,binding,identity)
    cached=cache['head'];pair.witness.validate_head(cached,binding,identity)
    if cached['sequence']>head['sequence']:raise ValueError('recovery cached receiver head is newer than selected cut')
    return cache


def _ancestor(path,cache,head,budget):
    with _database(path,check=budget.check) as db:
        row=db.execute('SELECT payload,sha256 FROM protective_events WHERE sequence=?',(cache['head']['sequence'],)).fetchone()
    if row is None:raise ValueError('recovery cached head is not a receiver ancestor')
    event=json.loads(row[0]);ancestor=dict(head,sequence=event['sequence'],head_sha256=row[1],at=event['at'],state=event['state'])
    if _hash(ancestor)!=_hash(cache['head']):raise ValueError('recovery cached head contents differ from original ancestor')


def _original_claim(cache,view):
    if cache['protection'] is None and (cache['spent_generation']!=cache['head']['state']['last_consumed_generation'] or
            any(row['kind']=='protective' for row in view['suffix'])):
        raise ValueError('recovery original spent claim evidence is missing')
    claim=reconcile_claim(cache,cache['head'],view)
    if cache['protection'] is not None and claim is None:
        raise ValueError('recovery original claimed packet evidence is missing')
    return claim


def inspect_cut(cohort_root,pair_root,artifact,*,owner,enabled=False,expected_cohort_sha256,
                expected_archive_sha256,expected_certificate_sha256,binding,identity,
                expected_nonce_store_id,expected_head_sha256,expected_release,through_sequence,
                max_bytes=store.MAX_TOTAL_BYTES,timeout_seconds=120,now=None,fault=None,
                max_nonce_rows=MAX_ROWS,max_nonce_pages=MAX_PAGES):
    """Return deterministic local coherence evidence; never infer a later cut."""
    if enabled is not True:raise ValueError('cohort recovery inspection is disabled')
    if type(max_bytes) is not int or not 0<max_bytes<=store.MAX_TOTAL_BYTES:
        raise ValueError('recovery aggregate byte bound differs')
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 0<timeout_seconds<=120:
        raise ValueError('recovery absolute deadline bound differs')
    if now is None:raise ValueError('recovery requires an explicit fixed evidence cutoff')
    if (type(max_nonce_rows) is not int or not 1<=max_nonce_rows<=MAX_ROWS or
            type(max_nonce_pages) is not int or not 3<=max_nonce_pages<=MAX_PAGES):
        raise ValueError('recovery expected nonce limits differ')
    at=_now(now);binding=deepcopy(binding);identity=deepcopy(identity);expected_release=deepcopy(expected_release)
    for digest in (expected_cohort_sha256,expected_archive_sha256,expected_certificate_sha256,expected_head_sha256):store._digest(digest)
    pair.witness._binding(binding);pair.witness._identity(identity);pair._token(expected_nonce_store_id)
    if expected_release!=_release_hashes():raise ValueError('recovery requires its original verifier release; no silent upgrade')
    cohort,restored,artifact=map(store._root,(cohort_root,pair_root,artifact))
    if cohort==restored or cohort in restored.parents or restored in cohort.parents or any(
            artifact==p or p in artifact.parents or artifact in p.parents for p in (cohort,restored)):
        raise ValueError('recovery input roots overlap')
    deadline=owner.domain.clock()+timeout_seconds
    with owner.admit(NONCE_PEAK_BYTES,timeout_seconds=timeout_seconds,timeout_ceiling_seconds=120) as lease:
        budget=_InspectionBudget(lease,owner,deadline,ceiling=120)
        files,dirs,total=_preflight(cohort,restored,artifact,lease,budget,max_bytes)
        seals={p:(ident,*pair._file_hash(p,budget,max_bytes)) for p,ident in files.items()}
        if seals[artifact][2]!=expected_archive_sha256:raise ValueError('recovery pinned archive digest differs')
        if seals[restored/'certificate.json'][2]!=expected_certificate_sha256:raise ValueError('recovery pinned certificate digest differs')
        with artifact.open('rb') as stream:
            certificate,_=pair._archive(stream,budget,max_bytes,binding,identity,expected_head_sha256,
                expected_nonce_store_id,max_nonce_rows,max_nonce_pages,expected_certificate_sha256,None,None)
        if store._read(restored/'certificate.json',pair.MAX_CERT_BYTES)!=_json(certificate).encode():
            raise ValueError('recovery restored certificate bytes differ')
        marker=_read_json(restored/'RESTORE_COMPLETE.json',pair.MAX_CERT_BYTES)
        expected_marker=dict(schema_version=2,purpose='paired_protective_restore_complete_v2',
            archive_sha256=expected_archive_sha256,certificate_sha256=expected_certificate_sha256,
            head_sha256=expected_head_sha256,nonce_store_id=expected_nonce_store_id,
            independent_backup_verified=False,runtime_resumption_authorized=False,retirement_authorized=False)
        if (_json(marker)!=_json(expected_marker) or
                store._read(restored/'RESTORE_COMPLETE.json',pair.MAX_CERT_BYTES)!=_json(expected_marker).encode()):
            raise ValueError('recovery completion marker differs')
        for name,part in (('receiver.sqlite','receiver'),('nonces.sqlite','nonce')):
            if seals[restored/name][1:]!=(certificate[part]['database_bytes'],certificate[part]['database_sha256']):
                raise ValueError('recovery restored database byte evidence differs')
        lease.check(remaining_peak_bytes=NONCE_PEAK_BYTES)
        pair._verify_copies(restored,certificate,owner,budget,binding,identity,expected_nonce_store_id,max_nonce_rows,max_nonce_pages)
        lease.check()
        _verified_inventory(cohort,expected_cohort_sha256,budget.check)
        metadata=_read_json(cohort/'cohort_capture.json',store.MAX_MANIFEST_BYTES)
        if (type(metadata) is not dict or set(metadata)!={'schema_version','purpose','certificate','review_reconciliation',
                'worker_inventory_sha256','supervisor_inventory_sha256'} or type(metadata['schema_version']) is not int or
                metadata['schema_version']!=1 or metadata['purpose']!='paired_coordinated_capture_v1'):
            raise ValueError('recovery original cohort metadata differs')
        worker=cohort/'worker';controller=cohort/'supervisor'
        financial=certify(worker,expected_inventory_sha256=metadata['worker_inventory_sha256'],
            experiment_id=identity['experiment_id'],expected_release=expected_release,through_sequence=through_sequence,
            supervisor_root=controller,expected_supervisor_inventory_sha256=metadata['supervisor_inventory_sha256'],
            now=at,check=budget.check)
        reviews=_review_history(worker,controller,now=at,check=budget.check)
        if _hash(financial)!=_hash(metadata['certificate']) or _hash(reviews)!=_hash(metadata['review_reconciliation']):
            raise ValueError('recovery original financial/review certificate differs')
        head=certificate['receiver']['head'];cache=_cached(worker,binding,identity,head,budget)
        _ancestor(restored/'receiver.sqlite',cache,head,budget)
        def view_for(selected):return source_view(worker,binding,identity,selected,enabled=True,now=at,
            max_bytes=max_bytes,timeout_seconds=min(10,budget.remaining()),clock=budget.clock,sealed=True)
        original_view=view_for(cache['head'])
        original_claim=_original_claim(cache,original_view)
        selected_view=view_for(head);selected_claim=reconcile_claim(cache,head,selected_view)
        point=selected_view['point']
        if point['complete']!=financial['observed_complete'] or point['checkpoint_sha256']!=financial['previous_checkpoint_sha256']:
            raise ValueError('recovery source point differs from current financial certificate')
        def advance(selected,view):
            return pair.witness._advance(selected['state'],dict(action='check',view=view,active_token=None,
                expected_head_sha256=selected['head_sha256'],operation_id='0'*32),identity,at)
        original_state=advance(cache['head'],original_view);selected_state=advance(head,selected_view)
        if _hash(original_state)!=_hash(selected_state):raise ValueError('recovery original and selected receiver states differ')
        cut=dict(schema_version=2,purpose=PURPOSE,evidence_at=at.isoformat(),binding=binding,identity=identity,
            cohort_inventory_sha256=expected_cohort_sha256,cohort_certificate_sha256=_hash(financial),
            account_snapshots=financial['account_snapshots'],review_reconciliation_sha256=_hash(reviews),
            archive_prefix=dict(through_sequence=through_sequence,prefix_sha256=financial['prefix_sha256'],next_input_hash=financial['next_input_hash']),
            archive_sha256=expected_archive_sha256,certificate_sha256=expected_certificate_sha256,
            nonce_evidence=certificate['nonce'],cached_ancestor_sha256=_hash(cache['head']),
            cache_sha256=_hash(cache),original_claim=original_claim,selected_claim=selected_claim,
            original_view_sha256=_hash(original_view),selected_view_sha256=_hash(selected_view),
            source_point=point,receiver_point=head['state']['point'],dry_run_state_sha256=_hash(selected_state))
        if len(_json(cut).encode())>65536:raise ValueError('recovery cut record byte bound exceeded')
        if fault:fault('before_return')
        after_files,after_dirs,after_total=_preflight(cohort,restored,artifact,lease,budget,max_bytes)
        if after_files!=files or after_dirs!=dirs or after_total!=total:raise ValueError('recovery input inventory changed')
        for path,seal in seals.items():
            if (after_files[path],*pair._file_hash(path,budget,max_bytes))!=seal:raise ValueError('recovery original input evidence changed')
        budget.check();lease.check()
        return dict(cut=cut,cut_sha256=_hash(cut),cut_coherence_verified=True,**FLAGS)


def verify_cut(cohort_root,pair_root,artifact,*,expected_cut_sha256,**options):
    """Require a separately retained exact cut; do not trust an embedded pin."""
    store._digest(expected_cut_sha256)
    result=inspect_cut(cohort_root,pair_root,artifact,**options)
    if result['cut_sha256']!=expected_cut_sha256:raise ValueError('recovery externally selected cut digest differs')
    return result
