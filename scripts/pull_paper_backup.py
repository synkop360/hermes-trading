"""Pull one completed daily SQLite snapshot; never capture or modify live state."""
import argparse
from datetime import date,datetime,timezone
import hashlib,json,os
from pathlib import Path,PurePosixPath
import re,shlex,shutil,signal,stat,subprocess,time,uuid

from hermes_trading.paper_backup import _metadata,_safe_root,_verified
from hermes_trading.paper_publication import _sync_dir
from hermes_trading.persistence.artifacts import _artifact_lock

MAX_BYTES=500000000
MAX_WIRE_BYTES=550000000
MAX_MEMBERS=4096
MAX_SECONDS=360
REMOTE_COPIES='/tmp/hermes-paper-daily-backups'


def _name(day,identity):
    if not isinstance(identity,str) or re.fullmatch('[0-9a-f]{64}',identity) is None:
        raise ValueError('exact expected snapshot source identity required')
    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:
        raise ValueError('UTC daily snapshot date required')
    return 'state-'+day+'-'+identity[:12]


def _owned_remove(path,parent,prefix):
    # Validate the exact absolute target inside the intended store immediately
    # before recursive removal; never construct a cross-shell deletion.
    path=_safe_root(path);parent=_safe_root(parent)
    if path.parent!=parent or not path.name.startswith(prefix):
        raise ValueError('daily-copy cleanup escaped owned store')
    shutil.rmtree(path)
    _sync_dir(parent)


def _generations(store,identity,check):
    owned=[]
    for member in store.glob('state-*-'+identity[:12]):
        check()
        if len(owned)>=4096:raise ValueError('daily-copy generation count exceeds bound')
        if member.name!=_name(member.name[6:16],identity):raise ValueError('unknown daily-copy generation')
        owned.append(_safe_root(member))
    return sorted(owned)


def _prune(store,identity,check):
    owned=_generations(store,identity,check)
    for old in owned[:-2]:
        check()
        if old.name!=_name(old.name[6:16],identity):raise ValueError('unknown daily-copy generation')
        _verified(old,identity,check,MAX_BYTES)
        _owned_remove(old,store,'state-')


def _extract(wire,stage,name,check):
    import tarfile
    count=0;total=0;seen=set()
    with tarfile.open(wire,'r:') as archive:
        for member in archive:
            check();count+=1
            if count>MAX_MEMBERS:raise ValueError('daily-copy tar member count exceeds bound')
            path=PurePosixPath(member.name)
            if (not path.parts or path.parts[0]!=name or path.is_absolute() or
                    '..' in path.parts or '\\' in member.name or ':' in member.name or
                    len(member.name)>512 or len(path.parts)>16 or member.name in seen):
                raise ValueError('daily-copy member escaped expected snapshot')
            seen.add(member.name)
            target=_safe_root(stage.joinpath(*path.parts))
            if not target.is_relative_to(stage):raise ValueError('daily-copy member escaped staging')
            if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
            if not member.isreg() or member.issparse():raise ValueError('daily-copy requires regular original members')
            if member.size<0 or total+member.size>MAX_BYTES:raise ValueError('daily-copy original byte budget exceeded')
            total+=member.size
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(member) as source,target.open('xb') as output:
                copied=0
                while raw:=source.read(1024*1024):
                    check();copied+=len(raw)
                    if copied>member.size:raise ValueError('daily-copy member length differs')
                    output.write(raw)
                if copied!=member.size:raise ValueError('daily-copy original member is truncated')
                output.flush();os.fsync(output.fileno())


def pull_completed(destination,*,source_identity,day,download,clock=time.monotonic):
    """Download one immutable completed copy, verify all bytes, then retain two.

    ``download(name, wire_path)`` must return only after a successful bounded
    transport. It never reads active SQLite or authorizes source retirement.
    """
    name=_name(day,source_identity)
    store=_safe_root(Path(destination).absolute())
    deadline=clock()+MAX_SECONDS
    def check():
        if clock()>=deadline:raise TimeoutError('daily-copy absolute deadline exceeded')
    store.mkdir(parents=True,exist_ok=True)
    check()
    lock=_safe_root(store/'daily_pull.lock')
    if lock.exists():
        info=lock.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('daily-copy lock must be an unlinked regular file')
    with _artifact_lock(store/'daily_pull'):
        check();target=_safe_root(store/name)
        retained=sorted({p.name for p in _generations(store,source_identity,check)}|{name})[-2:]
        if name not in retained:raise ValueError('requested day is outside the daily-copy retention window')
        if target.exists():
            receipt=_verified(target,source_identity,check,MAX_BYTES)
            _prune(store,source_identity,check)
            return dict(receipt,status='already_completed',path=str(target),off_host_verified=True)
        # Unknown prior partials require inspection rather than unlimited new
        # attempts, silent adoption or broad deletion of another process's work.
        if any(store.glob('.daily-pull-*')):raise ValueError('previous daily-copy partial requires inspection')
        stage=store/('.daily-pull-'+uuid.uuid4().hex);stage.mkdir()
        try:
            wire=stage/'wire.tar'
            download(name,wire);check()
            wire=_safe_root(wire)
            if not wire.is_file() or not 0<wire.stat().st_size<=MAX_WIRE_BYTES:
                raise ValueError('daily-copy transport size exceeds bound')
            _extract(wire,stage,name,check)
            receipt=_verified(stage/name,source_identity,check,MAX_BYTES)
            for directory in sorted((p for p in (stage/name).rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
                check();_sync_dir(directory)
            _sync_dir(stage/name);check()
            if target.exists():raise ValueError('daily-copy target appeared during transfer')
            (stage/name).rename(target);_sync_dir(store)
            _prune(store,source_identity,check)
            return dict(receipt,status='completed',path=str(target),off_host_verified=True)
        finally:
            if stage.exists():_owned_remove(stage,store,'.daily-pull-')


def _stop_transport(process):
    if process.poll() is not None:return
    if os.name=='nt':
        # This is the exact owned Popen process and its children, never a name
        # search or a worker/service process. Keep the original handle alive.
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,creationflags=0x08000000)
    else:os.killpg(process.pid,signal.SIGKILL)
    process.wait(timeout=10)


def railway_download(name,target,*,railway,clock=time.monotonic):
    if re.fullmatch(r'state-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{12}',name) is None:
        raise ValueError('bounded daily snapshot name required')
    # Source is an already completed immutable snapshot. Tar follows no links;
    # local extraction independently rejects links, special files and extras.
    command='timeout 300 tar -C '+shlex.quote(REMOTE_COPIES)+' -cf - '+shlex.quote(name)
    started=clock()
    with Path(target).open('xb') as output:
        process=subprocess.Popen([str(railway),'ssh','--',command],stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.DEVNULL,
            **({'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True}))
        try:
            while process.poll() is None:
                if clock()-started>=310:raise TimeoutError('daily-copy transport deadline exceeded')
                if Path(target).stat().st_size>MAX_WIRE_BYTES:raise ValueError('daily-copy transport size exceeds bound')
                time.sleep(.1)
            if process.returncode!=0:raise RuntimeError('daily-copy transport failed')
            output.flush();os.fsync(output.fileno())
            if Path(target).stat().st_size>MAX_WIRE_BYTES:raise ValueError('daily-copy transport size exceeds bound')
        finally:
            if process.poll() is None:_stop_transport(process)
            process.wait(timeout=10)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Copy a completed daily paper snapshot to this host')
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--railway-executable',required=True)
    parser.add_argument('--day',default=datetime.now(timezone.utc).date().isoformat())
    args=parser.parse_args(argv)
    try:
        raw=_metadata(Path(args.manifest),4*1024**2,lambda:None)
        manifest=json.loads(raw)
        if manifest.get('status')!='paper_ready' or manifest.get('execution_enabled') is not True:
            raise ValueError('final active paper manifest is required')
        identity=hashlib.sha256(b'/app/state\0'+raw).hexdigest()
        result=pull_completed(args.destination,source_identity=identity,day=args.day,
            download=lambda name,target:railway_download(name,target,railway=args.railway_executable))
    except Exception as error:
        print(json.dumps(dict(status='failed',error_kind=type(error).__name__,off_host_verified=False)))
        return 1
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
