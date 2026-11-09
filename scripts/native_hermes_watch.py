"""Stdlib-only no-agent cron launcher; install beside its reviewed JSON config."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,subprocess,time


LOCAL_TRANSPORT_SHA256 = '96e05ec04f5c36866ce885673d00bc78ead2a1d2849dde489c38e700790a8b12'


LOCAL_SERVICE_SHA256 = 'cf5581afc57aaeaa5d375f9fa452a1854684fef1e2fb167a680e0e0ba8ae01ee'


def validate_local_transport(project):
    if (os.environ.get('HERMES_TRADING_MODE')!='paper' or
            os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false'):
        raise ValueError('local supervision requires explicit paper mode')
    path=Path(project)/'scripts/paper_local_transport.py'
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=LOCAL_TRANSPORT_SHA256:
        raise ValueError('local supervisor transport release differs')
    service=Path(project)/'scripts/paper_supervisor_service.py'
    if service.is_symlink() or hashlib.sha256(service.read_bytes()).hexdigest()!=LOCAL_SERVICE_SHA256:
        raise ValueError('local supervisor service release differs')


def run_launch(path,*,now=None,command_run=None):
    path=Path(path).resolve()
    if path.stat().st_size>65536:raise ValueError('launch config exceeds bound')
    config=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config,dict):raise ValueError('launch config must be an object')
    def quiet(status,exit_code=0):return {'status':status,'wakeAgent':False,'exit_code':exit_code}
    if config.get('status')!='activated' or config.get('enabled') is not True:return quiet('not_activated')
    if config.get('wrapper_sha256')!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('launcher release differs')
    project=Path(config['project_dir']);state=Path(config['local_state_dir'])
    if not project.is_absolute() or not state.is_absolute():raise ValueError('absolute launcher paths required')
    if (project/'state/watcher_maintenance.json').exists() or (state/'watcher_maintenance.json').exists():return quiet('maintenance')
    expires=datetime.fromisoformat(config['expires_at'].replace('Z','+00:00'))
    current=now or datetime.now(timezone.utc)
    if expires.tzinfo is None or current.tzinfo is None:raise ValueError('aware launcher times required')
    remaining=(expires-current).total_seconds()
    if remaining<=0:return quiet('expired')
    maximum=config.get('max_script_seconds')
    if type(maximum) is not int or not 1<=maximum<=240:raise ValueError('finite launcher wait required')
    mode=config.get('transport','ssh')
    if mode not in {'ssh','local'}:raise ValueError('unsupported supervisor transport')
    if mode=='local':validate_local_transport(project)
    python=project/('.venv/bin/python' if mode=='local' and os.name!='nt' else '.venv/Scripts/python.exe')
    if not python.is_file():raise ValueError('project Python is unavailable')
    command=[str(python),'-m','scripts.hermes_watch','--launch-file',str(path)]
    environment=os.environ.copy()
    # Hermes may overlay its own runtime on Windows. Use the project's venv.
    for name in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'):environment.pop(name,None)
    if mode=='local':
        environment['HERMES_PROPOSAL_PYTHON']=str(python)
        # A separate cloud session must rotate solely on this persistent volume.
        environment['HERMES_HOME']=str(project/'state/cloud-auth')
        environment['HERMES_SHARED_AUTH_DIR']=str(project/'state/cloud-auth/shared')
    # A same-host monotonic deadline includes child import/startup. Leave five
    # seconds inside the native hard timeout for receipts/local cleanup.
    environment['HERMES_TRADING_INVOCATION_DEADLINE']=str(time.monotonic()+min(maximum,remaining)-5)
    try:
        result=(command_run or subprocess.run)(command,cwd=project,capture_output=True,text=True,
            encoding='utf-8',errors='replace',timeout=min(maximum,remaining),env=environment,
            **({'creationflags':0x08000000} if os.name=='nt' else {}))
    except subprocess.TimeoutExpired:return quiet('child_outcome_uncertain',1)
    return quiet('completed' if result.returncode==0 else 'child_failed',0 if result.returncode==0 else 1)


def main():
    try:result=run_launch(Path(__file__).with_suffix('.json'))
    except Exception as error:result={'status':'launcher_failed','error_class':type(error).__name__,'wakeAgent':False,'exit_code':1}
    print(json.dumps(result,sort_keys=True))
    return result['exit_code']


if __name__=='__main__':raise SystemExit(main())
