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


async def quiesce(root, *, boot=None, stream=None):
    if runtime_hash()!=EXPECTED_RUNTIME:raise ValueError('handoff source differs from installed predecessor')
    if boot is None:
        from hermes_trading import paper_boot as boot
    stream=stream if stream is not None else sys.stdout
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
