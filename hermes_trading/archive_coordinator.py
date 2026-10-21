"""Disabled coordinated worker/controller capture, without retirement or network.

The worker exporter is injected and must implement the supplied finite limits.
No installed transport, independent copy or storage-capacity policy is implied.
"""
import hashlib
import json
import shutil
import time
import uuid

from hermes_trading import archive_store as store
from hermes_trading.archive_certificate import certify, _database, _verified_inventory
from hermes_trading.archive_snapshot import snapshot, ownership, _inventory, _hash_file, INVENTORY
from hermes_trading.archive_review import canonical_closed_rows, reconcile_mirror, reconcile_reviews, reconcile_publications
from hermes_trading.reflection_supervision import _json, _now
from hermes_trading.paired_primary import resolve_primary


def _rows(path, check):
    if not path.exists():
        return []
    rows = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            check()
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _review_history(worker, controller, *, now, check):
    manifest = json.loads((worker / 'manifest.json').read_text(encoding='utf-8'))
    primary = resolve_primary(manifest).profile_directory(manifest,'adaptive')
    primary = worker / store._name(primary)
    with _database(primary / 'trading.db', check=check) as db:
        closed = canonical_closed_rows(db, check=check)
    reconcile_mirror(_rows(primary / 'trades.jsonl', check), closed)
    reconcile_mirror(_rows(controller / 'trades.jsonl', check), closed)
    with _database(controller / 'llm_review_state.sqlite', check=check) as db:
        state = json.loads(db.execute('SELECT state FROM supervisor WHERE id=1').fetchone()[0])
        reviews = [json.loads(row[0]) for row in db.execute('SELECT record FROM reviews ORDER BY review_id')]
    with _database(primary / 'paper_publications.sqlite', check=check) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
        publications = [] if not exists else [(json.loads(row[0]), json.loads(row[1]))
            for row in db.execute('SELECT request,receipt FROM publications ORDER BY review_id')]
    policy = json.loads((primary / 'reflection_owner.json').read_text(encoding='utf-8'))
    result = reconcile_reviews(state, reviews, closed, policy=policy, now=now, check=check)
    reconcile_publications(reviews, publications)
    return result


def capture(supervisor_root, target, *, enabled=False, worker_capture,
            experiment_id, expected_release, through_sequence, max_bytes,
            timeout_seconds, clock=time.monotonic, now=None, fault=None):
    """Hold the actual PC cycle owner through worker export and bundle commit.

    worker_capture(target, max_bytes=..., timeout_seconds=...) returns the exact
    inventory digest of a consistent worker export. Its transport must enforce
    these bounds itself; this coordinator cannot interrupt an arbitrary callback.
    Only original evidence is bundled. Source state is never compacted here.
    """
    if enabled is not True:
        raise ValueError('archive coordinator is disabled')
    if type(max_bytes) is not int or not 0 < max_bytes <= store.MAX_TOTAL_BYTES:
        raise ValueError('cohort snapshot byte budget is invalid')
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 90:
        raise ValueError('cohort capture deadline must be positive and at most ninety seconds')
    started = clock(); deadline = started + timeout_seconds
    def check():
        if clock() >= deadline:
            raise TimeoutError('cohort capture deadline exceeded')
    def allowance():
        check(); return min(30, deadline - clock())
    check()
    supervisor_root = store._root(supervisor_root); target = store._root(target)
    if supervisor_root == target or supervisor_root in target.parents or target in supervisor_root.parents:
        raise ValueError('cohort source and target must be separate')
    if target.exists() or not target.parent.is_dir():
        raise ValueError('cohort capture requires a new target in an existing parent')
    stage = target.with_name('.cohort-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        with ownership(supervisor_root, role='supervisor', enabled=True) as owner:
            worker = stage / 'worker'; controller = stage / 'supervisor'
            worker_result = worker_capture(worker, max_bytes=max_bytes, timeout_seconds=allowance())
            check()
            worker_rows = _verified_inventory(worker, worker_result['manifest_sha256'], check)
            used = sum(row['bytes'] for row in worker_rows) + (worker / INVENTORY).stat().st_size
            if used >= max_bytes:
                raise ValueError('cohort snapshot byte budget exceeded')
            pc_result = snapshot(supervisor_root, controller, role='supervisor', owner_db=owner,
                enabled=True, max_bytes=max_bytes-used, timeout_seconds=allowance(), clock=clock)
            # A completed batch can be newer than invocation start. Observe the
            # captured pair now; an explicit caller cutoff remains authoritative.
            current = _now(now)
            certificate = certify(worker, expected_inventory_sha256=worker_result['manifest_sha256'],
                experiment_id=experiment_id, expected_release=expected_release, through_sequence=through_sequence,
                supervisor_root=controller, expected_supervisor_inventory_sha256=pc_result['manifest_sha256'],
                now=current, check=check)
            review_history = _review_history(worker, controller, now=current, check=check)
            metadata = {'schema_version': 1, 'purpose': 'paired_coordinated_capture_v1',
                'certificate': certificate, 'review_reconciliation': review_history,
                'worker_inventory_sha256': worker_result['manifest_sha256'],
                'supervisor_inventory_sha256': pc_result['manifest_sha256']}
            raw = (_json(metadata) + '\n').encode()
            if len(raw) > store.MAX_MANIFEST_BYTES:
                raise ValueError('cohort metadata exceeds byte budget')
            (stage / 'cohort_capture.json').write_bytes(raw)
            inventory = _inventory(stage, check); records = []; total = 0
            for name in sorted(inventory):
                check(); size, digest = _hash_file(stage / name, check); total += size
                if total > max_bytes:
                    raise ValueError('cohort snapshot byte budget exceeded')
                store.sync_file(stage / name)
                records.append({'path': name, 'bytes': size, 'sha256': digest})
            raw = (_json({'schema_version': 1, 'purpose': 'paired_consistent_snapshot_v1', 'files': records})+'\n').encode()
            total += len(raw)
            if total > max_bytes or len(raw) > store.MAX_MANIFEST_BYTES:
                raise ValueError('cohort inventory exceeds byte budget')
            (stage / INVENTORY).write_bytes(raw); store.sync_file(stage / INVENTORY)
            digest = hashlib.sha256(raw).hexdigest()
            for path in sorted((path for path in stage.rglob('*') if path.is_dir()),
                               key=lambda path:len(path.parts), reverse=True):
                check(); store._sync_directory(path)
            store._sync_directory(stage)
            if fault:
                fault('before_publish')
            _verified_inventory(stage, digest, check)
            check()
            if target.exists():
                raise ValueError('cohort capture target appeared')
            stage.rename(target); store._sync_directory(target.parent)
        return {'manifest_sha256': digest, 'snapshot_bytes': total, 'certificate': certificate,
            'elapsed_seconds': clock()-started, 'local_directory_fsync_supported': store.directory_fsync_supported(),
            'independent_backup_verified': False, 'retirement_authorized': False}
    except BaseException:
        # Only this invocation's exact newly allocated stage may be removed.
        if stage.exists():
            resolved = store._root(stage).resolve()
            if resolved != stage or resolved.parent != target.parent.resolve() or not stage.name.startswith('.cohort-'):
                raise ValueError('cohort capture cleanup target escaped')
            shutil.rmtree(resolved)
        raise
