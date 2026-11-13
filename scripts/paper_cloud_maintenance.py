"""Condense retained legacy evidence, then serve the existing paper experiment."""
import asyncio,json,os,signal,traceback,hashlib
from pathlib import Path

from scripts.paper_cloud_service import install_default,serve_cloud
from scripts.paper_cloud_install import _same_or_create
from scripts.paper_compact_history import compact_history
from scripts.paper_storage_recovery import recover_storage,recovery_status
from scripts.paper_archive_repack import repack_archives
from hermes_trading.archive_snapshot import ownership,_safe_root
from hermes_trading.paired_storage_admission import inspect_storage

MARKER={'schema_version':1,'purpose':'paper_cloud_storage_maintenance_v1','mode':'paper'}
RUNTIME='082042439d5dadd8247b788fe6b29de5c7449f165f339a2e155761bf55c525de'
# Runtime of the reviewed Discord format release
# (scripts/discord_format_release_plan.json, new runtime). The second storage
# pause recovery is bound to it and is never replayed under another runtime.
PREVIOUS_RUNTIME='33f6c6329f1bc5383d12d5cafce8e13255eb68a02a2bf2bba9ec60ccb370114f'
PREVIOUS_RECOVERY_TAG='after-2026-11-13-reserve'
PREVIOUS_RECOVERY_APPROVAL='user-2026-11-13-storage-maintenance-option-2'
# Installed runtime after the chained paper pilot release
# (scripts/pilot_release_plan.json, new runtime).
CURRENT_RUNTIME='42cf3f3efe1b931f4f4cec942749b086af2bdb5d82aa4865d4c244c85ba6df7e'
RECOVERY_TAG='after-2026-11-13-pilot'
RECOVERY_APPROVAL='user-2026-11-13-lat-116-chained-transition'


def marker(root,*,enabled):
    path=_safe_root(root/'watcher_maintenance.json')
    if enabled:
        if not path.exists():_same_or_create(path,(json.dumps(MARKER,sort_keys=True)+'\n').encode())
    elif path.exists() and path.stat().st_size<4096 and json.loads(path.read_bytes())==MARKER:
        path.unlink()
        from hermes_trading.paper_publication import _sync_dir
        _sync_dir(root)


def maintain_storage(project):
    """Second storage pause (2026-11-13 02:45 UTC, filesystem reserve).

    The legacy database archive and the first two recoveries are already
    complete and pinned to the predecessor runtime; they are not replayed.
    This phase only condenses one more obsolete root backup losslessly,
    re-verifies the cutover archives, then resolves the second pause once:
    if its recovery already completed under the Discord format runtime, that
    evidence is verified read-only and nothing is recovered; otherwise the
    same approved pause is recovered under a new, separately named audit bound
    to the pilot runtime. A later, different pause is never recovered here.
    It never resets a halt unless the native recovery preconditions all hold.
    """
    root=project/'state'
    install_default(project)
    print(json.dumps(compact_history(root,enabled=True,retire_originals=True)),flush=True)
    print(json.dumps(repack_archives(root/'.paper-cloud-cutover')),flush=True)
    storage=inspect_storage(root,json.loads((root/'storage_limits.json').read_bytes()))
    print(json.dumps({'kind':'paper_cloud_storage_probe','status':storage['status'],
        'reason':storage['reason'],'state_bytes':storage['state_bytes'],'free_bytes':storage['free_bytes']}),flush=True)
    previous=recovery_status(root,approval_ref=PREVIOUS_RECOVERY_APPROVAL,
        source_runtime=PREVIOUS_RUNTIME,recovery_tag=PREVIOUS_RECOVERY_TAG)
    if previous=='completed':
        if (root/('.paper-storage-recovery-'+RECOVERY_TAG)).exists():raise ValueError('second pause recovered twice')
        print(json.dumps({'kind':'paper_storage_recovery','status':'already_recovered_by_predecessor'}),flush=True)
    else:
        print(json.dumps(recover_storage(root,approval_ref=RECOVERY_APPROVAL,
            expected_runtime=CURRENT_RUNTIME,recovery_tag=RECOVERY_TAG)),flush=True)
    with ownership(root,role='worker',enabled=True) as db:
        state=json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        storage=inspect_storage(root,json.loads((root/'storage_limits.json').read_bytes()))
        if state.get('storage_stop') or storage.get('status')!='ready':raise ValueError('current storage admission remains blocked')
    marker(root,enabled=False)
    return {'kind':'paper_cloud_storage_maintenance','status':'completed'}


async def run(project):
    project=Path(project).resolve()
    try:result=maintain_storage(project)
    except Exception as error:
        # Native supervisor maintenance does not consume failed cycles while
        # the protective worker continues to report its actual admission.
        marker(project/'state',enabled=True)
        frames=[{'file':Path(f.f_code.co_filename).name,'function':f.f_code.co_name,'line':line}
            for f,line in traceback.walk_tb(error.__traceback__)]
        result={'kind':'paper_cloud_storage_maintenance','status':'blocked','error_type':type(error).__name__,
            'reason_sha256':hashlib.sha256(str(error).encode()).hexdigest(),'frames':frames[-8:]}
    print(json.dumps(result),flush=True)
    await serve_cloud(project)


def main():
    async def serve():
        if os.name!='nt':asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,asyncio.current_task().cancel)
        await run(Path(__file__).resolve().parents[1])
    try:asyncio.run(serve())
    except (asyncio.CancelledError,KeyboardInterrupt):return 0
    except Exception as error:
        frames=[{'file':Path(f.f_code.co_filename).name,'function':f.f_code.co_name,'line':line}
            for f,line in traceback.walk_tb(error.__traceback__)]
        print(json.dumps({'kind':'paper_cloud_failure_diagnostic','error_type':type(error).__name__,
            'reason_sha256':hashlib.sha256(str(error).encode()).hexdigest(),'frames':frames[-10:]}),flush=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
