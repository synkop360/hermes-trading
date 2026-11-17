"""Resume the audited storage limit and source transition before paper startup."""
import asyncio,json,os,signal,traceback,hashlib
from pathlib import Path

from scripts.paper_cloud_service import install_default,serve_cloud
from scripts.paper_cloud_install import _same_or_create
from scripts.paper_compact_history import compact_history
from scripts.paper_storage_recovery import recover_storage,recovery_status
from scripts.paper_storage_limit_transition import transition_limit, AUDIT, APPROVAL as TRANSITION_APPROVAL
from scripts.paper_archive_repack import repack_archives
from hermes_trading.archive_snapshot import ownership,_safe_root
from hermes_trading.paired_storage_admission import inspect_storage

MARKER={'schema_version':1,'purpose':'paper_cloud_storage_maintenance_v1','mode':'paper'}
RUNTIME='082042439d5dadd8247b788fe6b29de5c7449f165f339a2e155761bf55c525de'
# Installed runtime after the reviewed Discord format release
# (scripts/discord_format_release_plan.json, new runtime).
CURRENT_RUNTIME='33f6c6329f1bc5383d12d5cafce8e13255eb68a02a2bf2bba9ec60ccb370114f'
STORAGE_RUNTIME='924b045fb5bc8b08ae7313037f7cce347494b9c84daf20679b5f30b3cb0e0263'
NEW_RUNTIME='3b7eb68929b48c502a3b041768069274d05e7dd1508cf97e56366737b7b82258'
RECOVERY_TAG='after-2026-11-13-reserve'
RECOVERY_APPROVAL='user-2026-11-13-storage-maintenance-option-2'
NEW_RECOVERY_TAG='after-2026-11-17-storage-limit-1gb'
NEW_RECOVERY_APPROVAL='user-2026-11-17-storage-limit-1gb-recovery'


def marker(root,*,enabled):
    path=_safe_root(root/'watcher_maintenance.json')
    if enabled:
        if not path.exists():_same_or_create(path,(json.dumps(MARKER,sort_keys=True)+'\n').encode())
    elif path.exists() and path.stat().st_size<4096 and json.loads(path.read_bytes())==MARKER:
        path.unlink()
        from hermes_trading.paper_publication import _sync_dir
        _sync_dir(root)


def maintain_storage(project):
    """Verify the old recovery, migrate both 1 GB bounds, then recover storage."""
    root=project/'state'
    print(json.dumps(compact_history(root,enabled=True,retire_originals=True)),flush=True)
    print(json.dumps(repack_archives(root/'.paper-cloud-cutover')),flush=True)
    storage=inspect_storage(root,json.loads((root/'storage_limits.json').read_bytes()))
    print(json.dumps({'kind':'paper_cloud_storage_probe','status':storage['status'],
        'reason':storage['reason'],'state_bytes':storage['state_bytes'],'free_bytes':storage['free_bytes']}),flush=True)
    # The 13 November receipt belongs to the old configuration. Verify it
    # before the first migration; subsequent starts verify the durable intent.
    if not (root/AUDIT/'intent.json').exists():
        previous=recovery_status(root,approval_ref=RECOVERY_APPROVAL,
            source_runtime=CURRENT_RUNTIME,recovery_tag=RECOVERY_TAG)
        if previous.get('status')!='already_recovered':
            raise ValueError('previous storage recovery is not complete')
        print(json.dumps(previous),flush=True)
    print(json.dumps(transition_limit(root,approval_ref=TRANSITION_APPROVAL)),flush=True)
    install_default(project)
    new_recovery=root/('.paper-storage-recovery-'+NEW_RECOVERY_TAG)
    if (new_recovery/'receipt.json').exists():
        source=json.loads((new_recovery/'intent.json').read_bytes()).get('source')
        if source not in {STORAGE_RUNTIME,NEW_RUNTIME}:
            raise ValueError('storage recovery source differs')
        recovered=recovery_status(root,approval_ref=NEW_RECOVERY_APPROVAL,
            source_runtime=source,recovery_tag=NEW_RECOVERY_TAG)
    else:
        recovered=recover_storage(root,approval_ref=NEW_RECOVERY_APPROVAL,
            expected_runtime=NEW_RUNTIME,recovery_tag=NEW_RECOVERY_TAG)
    print(json.dumps(recovered),flush=True)
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
