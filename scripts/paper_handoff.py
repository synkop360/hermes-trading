"""Quiesce the installed 37e1be8 worker after one settled native batch."""
import asyncio
import json
import os
from pathlib import Path
import sys

EXPECTED_RUNTIME='19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed'


class SettledHandoff(BaseException):
    """Leave the worker through its finally blocks without latching a stop."""


def runtime_hash():
    from hermes_trading.paper_publication import _release_hashes
    from hermes_trading.reflection_supervision import _hash
    return _hash(_release_hashes())


def closed_storage_boundary(root):
    """A stopped storage collector is quiescent only under its actual lease."""
    root=Path(root)
    if not (root/'worker_runtime.sqlite').is_file():return False
    from hermes_trading.archive_snapshot import ownership
    from hermes_trading.paired_worker import _stats,_profiles,_active_storage_positions
    from hermes_trading.paired_primary import resolve_primary
    with ownership(root,role='worker',enabled=True) as db:
        state=json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        if state.get('storage_stop') is not True:return False
        manifest=json.loads((root/'manifest.json').read_bytes());profiles=_profiles(root,manifest)
        stats=_stats(profiles[('adaptive',resolve_primary(manifest).wallet_eur)][0])
        if (stats['pending'] or stats.get('latest') is None or
                any(count!=0 for count in _active_storage_positions(profiles).values())):
            raise ValueError('storage handoff has unresolved financial work')
        return True


def sqlite_footprint(root):
    """Read page-allocation counters only; never row contents or private paths."""
    import sqlite3,shutil
    from contextlib import closing
    root=Path(root).resolve();rows=[]
    paths=[('legacy',root/'trading.db'),('worker',root/'worker_runtime.sqlite'),
           ('shared',root/'adaptive_200/paper_publications.sqlite')]
    paths += [(f'{arm}_{wallet}',root/f'{arm}_{wallet}'/'trading.db')
        for arm in ('adaptive','control') for wallet in (50,100,150,200)]
    for label,path in paths:
        if not path.is_file():continue
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=0)) as db:
            page=db.execute('PRAGMA page_size').fetchone()[0]
            rows.append(dict(bucket=label,bytes=path.stat().st_size,
                free_page_bytes=page*db.execute('PRAGMA freelist_count').fetchone()[0]))
    return dict(kind='paper_sqlite_footprint',databases=rows,filesystem_free_bytes=shutil.disk_usage(root).free)


async def quiesce(root, *, boot=None, stream=None):
    if runtime_hash()!=EXPECTED_RUNTIME:raise ValueError('handoff source differs from installed predecessor')
    stream=stream if stream is not None else sys.stdout
    if closed_storage_boundary(root):
        print(json.dumps({'kind':'paper_handoff','status':'quiescent','basis':'closed_storage_boundary'}),file=stream,flush=True)
        return 'quiescent'
    if boot is None:
        from hermes_trading import paper_boot as boot
    original=boot.run_worker
    async def settle(root,**options):
        previous=options.get('observer')
        def observer(row):
            if previous is not None:
                try:previous(row)
                except Exception:pass
            if isinstance(row,dict) and row.get('kind')=='settled':raise SettledHandoff()
        return await original(root,**dict(options,observer=observer))
    boot.run_worker=settle
    try:
        try:await asyncio.wait_for(boot.run_paper(Path(root)),timeout=180)
        except SettledHandoff:
            print(json.dumps({'kind':'paper_handoff','status':'quiescent'}),file=stream,flush=True)
            return 'quiescent'
        raise ValueError('worker returned without a settled handoff')
    finally:boot.run_worker=original


def main():
    os.environ['HERMES_TRADING_MODE']='paper'
    os.environ['HERMES_TRADING_ALLOW_REAL_ORDERS']='false'
    try:asyncio.run(quiesce(Path('/app/state')))
    except BaseException:
        print(json.dumps({'kind':'paper_handoff','status':'not_quiescent'}),flush=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
