"""Finite same-container supervisor loop, using the existing native launcher.

Run beside the paper worker on its persistent volume. Never boot a new journal,
change a policy, clear a stop, extend a deadline or print private child output.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

from scripts import native_hermes_watch
from scripts.paper_local_transport import _run_bounded, _pairs, _constant, _float, TransportError


def _read(path):
    path=Path(path)
    try:
        if path.is_symlink():raise ValueError('linked supervisor metadata')
        with path.open('rb') as source:raw=source.read(65537)
        if len(raw)>65536:raise ValueError('supervisor metadata exceeds bound')
        body=json.loads(raw,object_pairs_hook=_pairs,parse_constant=_constant,parse_float=_float)
        if not isinstance(body,dict):raise ValueError('supervisor metadata must be an object')
        return body
    except (OSError,UnicodeError,RecursionError) as error:
        raise ValueError('existing supervisor metadata required') from None


def supervise(launch_file, *, stop=None, launch_run=None, wait=None, clock=None, stream=None):
    path=Path(launch_file).resolve(strict=True)
    stop=stop if stop is not None else threading.Event()
    wait=wait or stop.wait
    clock=clock or (lambda:datetime.now(timezone.utc))
    stream=stream if stream is not None else sys.stdout
    launch_run=launch_run or native_hermes_watch.run_launch
    statuses={'completed','child_failed','child_outcome_uncertain','not_activated','maintenance','expired'}
    while not stop.is_set():
        config=_read(path)
        if config.get('transport')!='local':raise ValueError('explicit local supervisor transport required')
        if config.get('enabled') is not True or config.get('status')!='activated':return 'disabled'
        try:
            expires=datetime.fromisoformat(config['expires_at'].replace('Z','+00:00'))
            now=clock()
            if expires.tzinfo is None or now.tzinfo is None:raise ValueError('aware supervisor deadline required')
            state=Path(config['local_state_dir'])
            if not state.is_absolute() or not state.is_dir():raise ValueError('existing supervisor journal required')
        except (KeyError,TypeError,AttributeError):raise ValueError('supervisor launch metadata differs') from None
        if now>=expires:return 'expired'
        limits=_read(state/'supervisor_limits.json');lifecycle=_read(state/'supervisor_lifecycle.json')
        interval=limits.get('interval_seconds')
        if type(interval) is not int or not 60<=interval<=240:raise ValueError('finite supervisor interval required')
        if lifecycle.get('status')=='stopped':return 'stopped'
        if lifecycle.get('status')!='running':raise ValueError('existing running supervisor lifecycle required')
        def command_run(command, **kwargs):
            return _run_bounded(command,max_bytes=65536,stop_event=stop,**kwargs)
        try:result=launch_run(path,command_run=command_run)
        except TransportError:return 'outcome_uncertain'
        status=result.get('status') if isinstance(result,dict) else None
        if status not in statuses:raise ValueError('native supervisor result differs')
        print(json.dumps({'kind':'paper_supervisor_cycle','status':status}),file=stream,flush=True)
        if status=='child_outcome_uncertain':return 'outcome_uncertain'
        if status in {'expired','not_activated'}:return status
        remaining=(expires-clock()).total_seconds()
        if remaining<=0:return 'expired'
        wait(min(interval,remaining))
    return 'stopped'


def main(argv=None):
    parser=argparse.ArgumentParser(description='Run existing finite paper supervision locally')
    parser.add_argument('--launch-file',required=True)
    args=parser.parse_args(argv)
    stop=threading.Event()
    for name in ('SIGTERM','SIGINT'):
        if hasattr(signal,name):signal.signal(getattr(signal,name),lambda *_:stop.set())
    try:status=supervise(args.launch_file,stop=stop)
    except Exception:
        print(json.dumps({'kind':'paper_supervisor_stopped','status':'configuration_rejected'}),flush=True)
        return 1
    print(json.dumps({'kind':'paper_supervisor_stopped','status':status}),flush=True)
    return 1 if status=='outcome_uncertain' else 0


if __name__=='__main__':raise SystemExit(main())
