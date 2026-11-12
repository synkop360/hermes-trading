"""Condense retained legacy evidence, then serve the existing paper experiment."""
import asyncio,json,os,signal,traceback,hashlib
from pathlib import Path

from scripts.paper_cloud_service import install_default,serve_cloud
from scripts.paper_cloud_install import _same_or_create
from scripts.paper_compact_history import compact_history
from scripts.paper_storage_recovery import recover_storage
from hermes_trading.archive_snapshot import ownership,_safe_root
from hermes_trading.paired_storage_admission import inspect_storage

MARKER={'schema_version':1,'purpose':'paper_cloud_storage_maintenance_v1','mode':'paper'}
RUNTIME='082042439d5dadd8247b788fe6b29de5c7449f165f339a2e155761bf55c525de'


def marker(root,*,enabled):
    path=_safe_root(root/'watcher_maintenance.json')
    if enabled:
        if not path.exists():_same_or_create(path,(json.dumps(MARKER,sort_keys=True)+'\n').encode())
    elif path.exists() and path.stat().st_size<4096 and json.loads(path.read_bytes())==MARKER:
        path.unlink()
        from hermes_trading.paper_publication import _sync_dir
        _sync_dir(root)


def maintain_storage(project):
    root=project/'state'
    install_default(project)
    result=compact_history(root,enabled=True,retire_originals=True,legacy_database_only=True)
    print(json.dumps(result),flush=True)
    print(json.dumps(recover_storage(root,approval_ref='user-2026-11-12-lossless-historical-condensation',
        expected_runtime=RUNTIME,recovery_tag='after-legacy-archive')),flush=True)
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
        result={'kind':'paper_cloud_storage_maintenance','status':'blocked','error_type':type(error).__name__}
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
