"""Install once, then serve the normal paper worker beside finite supervision."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import threading

from scripts.paper_cloud_install import install
from scripts.paper_local_transport import _run_bounded, TransportError
from urllib.parse import urlsplit


class Relay:
    """Forward only known technical events and the explicit device-login link."""
    def __init__(self,stream):self.stream=stream;self.pending=bytearray()
    def __call__(self,chunk):
        self.pending.extend(chunk)
        if len(self.pending)>16384:raise ValueError('child event exceeds bound')
        while b'\n' in self.pending:
            raw,_,rest=self.pending.partition(b'\n');self.pending=bytearray(rest)
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError('child event differs')
            kind,status=body.get('kind'),body.get('status')
            if not isinstance(kind,str) or not isinstance(status,str):raise ValueError('child event tags differ')
            if kind=='paper_cloud_auth' and status=='verification_required':
                url,code=body.get('url'),body.get('code')
                if (set(body)!={'kind','status','url','code'} or not isinstance(url,str) or not isinstance(code,str) or
                        len(url)>4096 or not 1<=len(code)<=64 or urlsplit(url).scheme!='https' or
                        urlsplit(url).netloc!='portal.nousresearch.com'):
                    raise ValueError('child verification event differs')
                record=body
            elif (kind,status) in {
                    ('paper_cloud_auth','connected'),('paper_cloud_auth','already_connected'),('paper_cloud_auth','connection_failed'),
                    ('paper_supervisor_cycle','auth_pending'),('paper_supervisor_cycle','completed'),
                    ('paper_supervisor_cycle','child_failed'),('paper_supervisor_cycle','maintenance'),
                    ('paper_supervisor_cycle','child_outcome_uncertain'),('paper_supervisor_cycle','not_activated'),
                    ('paper_supervisor_cycle','expired'),('paper_supervisor_stopped','stopped'),
                    ('paper_supervisor_stopped','expired'),('paper_supervisor_stopped','disabled'),
                    ('paper_supervisor_stopped','not_activated'),('paper_supervisor_stopped','outcome_uncertain'),
                    ('paper_supervisor_stopped','configuration_rejected')}:
                record={'kind':kind,'status':status}
            else:raise ValueError('unexpected child event')
            print(json.dumps(record,sort_keys=True),file=self.stream,flush=True)


def install_default(project):
    scripts=project/'scripts'
    plan=json.loads((scripts/'cloud_release_plan.json').read_bytes())
    from scripts.hermes_watch import _supervisor_release
    release=_supervisor_release()
    if release!=plan['new']:
        successor=json.loads((scripts/'discord_format_release_plan.json').read_bytes())
        if successor['old']!=plan['new'] or successor['new']!=release:
            raise ValueError('notification correction source map differs')
        from scripts.paper_cloud_update import update_installed
        print(json.dumps(update_installed(project,successor)),flush=True)
        plan=successor
    return install(project,plan,
        entry_plan=json.loads((scripts/'entry_history_release_plan.json').read_bytes()),
        observer=lambda phase:print(json.dumps({'kind':'paper_cloud_install_phase','phase':phase}),flush=True))


async def serve_cloud(project, *, installer=None, worker=None, spawn=None, stream=None):
    project=Path(project);root=project/'state'
    stream=stream if stream is not None else sys.stdout
    installed=(installer or install_default)(project)
    # The private checkpoint is durable now; do not pass its transport payload
    # into authentication, model or native receiver processes.
    for name in list(os.environ):
        if name=='HERMES_CLOUD_CHECKPOINT_PARTS' or (name.startswith('HERMES_CLOUD_CHECKPOINT_') and name[-2:].isdigit()):
            os.environ.pop(name,None)
    print(json.dumps({'kind':'paper_cloud_cutover','status':installed['status']}),file=stream,flush=True)
    stop=threading.Event();tasks=[];children=[]
    async def background(command,timeout):
        try:
            await asyncio.to_thread(_run_bounded,command,cwd=project,env=os.environ.copy(),
                timeout=timeout,max_bytes=512*1024*1024,stop_event=stop,retain_stdout=False,stdout_observer=Relay(stream))
        except (TransportError,OSError,ValueError):
            print(json.dumps({'kind':'paper_cloud_child_stopped'}),file=stream,flush=True)
    def launch(command,timeout):
        if spawn is not None:children.append(spawn(command,cwd=project))
        else:tasks.append(asyncio.create_task(background(command,timeout)))
    try:
        launch_path=Path(installed['launch_file'])
        if spawn is None:
            config=json.loads(launch_path.read_bytes())
            expires=datetime.fromisoformat(config['expires_at'].replace('Z','+00:00'))
            remaining=(expires-datetime.now(timezone.utc)).total_seconds()
            if expires.tzinfo is None or remaining>180*86400:raise ValueError('finite cloud lifetime required')
        else:remaining=240
        if remaining>0:
            launch([sys.executable,'-m','scripts.paper_supervisor_service','--launch-file',str(launch_path)],remaining)
        else:
            print(json.dumps({'kind':'paper_supervisor_stopped','status':'expired'}),file=stream,flush=True)
        if remaining>0 and not (root/'cloud-auth/auth.json').exists() and os.environ.get('HERMES_NOUS_AUTH')!='environment':
            launch([sys.executable,'-m','scripts.paper_cloud_auth'],min(900,remaining))
        if worker is None:
            from scripts.paper_service import serve
            worker=serve
        await worker(root)
    finally:
        stop.set()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=3)
                except Exception:child.kill();child.wait()


def main():
    async def run():
        loop=asyncio.get_running_loop();task=asyncio.current_task()
        if os.name!='nt':loop.add_signal_handler(signal.SIGTERM,task.cancel)
        await serve_cloud(Path(__file__).resolve().parents[1])
    try:asyncio.run(run())
    except (asyncio.CancelledError,KeyboardInterrupt):return 0
    except Exception:
        print(json.dumps({'kind':'paper_cloud_start_failed'}),flush=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
