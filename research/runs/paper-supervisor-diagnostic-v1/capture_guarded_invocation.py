"""One operator-observed invocation of the unchanged native guarded launcher.

Only sanitized types and trusted source-frame locations are retained. Native
argv, kwargs, deadlines, return value and child process behavior are unchanged.
Default execution is a privacy self-check; live invocation requires --execute.
"""
import argparse
import ast
import builtins
from functools import lru_cache
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[3]/'hermes-trading'
HOME=Path('C:/Users/thoma/AppData/Local/hermes')
LAUNCH=HOME/'scripts/hermes_trading_paper_6c9d520_native.json'
NATIVE=LAUNCH.with_suffix('.py')
JOB_ID='2f6c5088c6ff'
RELEASE='fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844'

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()



@lru_cache(maxsize=1)
def trusted_sources():
    release=json.loads((HERE.parent/'paper-discord-reporting-v1/proposed-release-plan.json').read_text())['new']
    paths=[(MAIN/'hermes_trading'/name,'hermes_trading/'+name,expected) for name,expected in release['runtime'].items()]
    paths.extend([(MAIN/'scripts/hermes_watch.py','scripts/hermes_watch.py',release['watcher']),
                  (NATIVE,'native_hermes_watch.py',release['native_launcher'])])
    registry={}
    types={name for name,value in vars(builtins).items() if isinstance(value,type) and issubclass(value,BaseException)}
    types.update({'JSONDecodeError','CancelledError','TimeoutExpired','CalledProcessError','OperationalError',
                  'IntegrityError','ProgrammingError','HTTPError','HTTPStatusError','RequestError','ConnectError',
                  'ReadError','WriteError','ProtocolError','RemoteProtocolError','ReadTimeout','ConnectTimeout',
                  'PoolTimeout','SSLError','URLError','ValidationError'})
    for path,label,expected in paths:
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:
            continue
        tree=ast.parse(raw.decode('utf-8-sig'))
        functions=[(n.name,n.lineno,n.end_lineno) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
        types.update(n.name for n in ast.walk(tree) if isinstance(n,ast.ClassDef))
        registry[str(path.resolve()).casefold()]=dict(source=label,functions=functions)
    return registry,frozenset(types)

def sanitized(stderr):
    registry,allowed=trusted_sources()
    frames=[]
    text=stderr[-65536:]
    for filename,line,function in re.findall(r'File "([^"\n]+)", line ([0-9]+), in ([A-Za-z_][A-Za-z0-9_.]*)',text):
        try:known=registry.get(str(Path(filename).resolve()).casefold())
        except (ValueError,OSError):known=None
        number=int(line)
        match=next((name for name,start,end in known['functions'] if name==function and start<=number<=end),None) if known else None
        if match is None:
            frames.append(dict(scope='unclassified_python_frame'))
        else:
            frames.append(dict(scope='pinned_project_source',source=known['source'],line=number,function=match))
    types=[]
    for token in re.findall(r'^([A-Za-z_][A-Za-z0-9_.]{0,200})(?::|$)',text,re.M):
        name=token.rsplit('.',1)[-1]
        if name in allowed:types.append(name)
        elif name.endswith(('Error','Exception','Timeout','Unavailable','Exit','Interrupt')):
            types.append('UnclassifiedException')
    return dict(exception_types=list(dict.fromkeys(types))[-8:],frames=frames[-16:],
                stderr_bytes=len(stderr.encode('utf-8',errors='replace')),raw_stderr_retained=False)

def self_check():
    registry,_=trusted_sources()
    source=registry[str((MAIN/'scripts/hermes_watch.py').resolve()).casefold()]
    name,line,_=next(row for row in source['functions'] if row[0]=='_remote_json')
    fake='Traceback (most recent call last):\n  File "'+str(MAIN/'scripts/hermes_watch.py')+'", line '+str(line)+', in '+name+'\nValueError: FAKE_SECRET_TOKEN https://example.invalid/private?token=FAKE_SECRET_TOKEN\n'
    fake+='  File "external.py", line 1, in FAKE_SECRET_TOKEN\n'
    fake+='  File "'+str(MAIN/'hermes_trading/FAKE_SECRET_TOKEN.py')+'", line 1, in FAKE_SECRET_TOKEN\n'
    fake+='FAKE_SECRET_TOKENError: private payload\n'
    fake+='FAKE_SECRET_TOKEN.RuntimeError: private payload\n'
    result=sanitized(fake);encoded=json.dumps(result)
    assert 'FAKE_SECRET_TOKEN' not in encoded and 'example.invalid' not in encoded
    assert result['exception_types']==['ValueError','UnclassifiedException','RuntimeError']
    assert result['frames'][0]['source']=='scripts/hermes_watch.py'
    assert all(x==dict(scope='unclassified_python_frame') for x in result['frames'][1:])
    return dict(status='sanitizer_self_check_passed',privacy_cases=5,native_invocations=0)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--output',default='observed-native-invocation.json')
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps(self_check()));return
    output=(HERE/args.output).resolve()
    assert output.is_relative_to(HERE) and not output.exists()
    config=json.loads(LAUNCH.read_text(encoding='utf-8'))
    assert Path(config['project_dir']).resolve()==MAIN.resolve() and config['release_hash']==RELEASE
    assert config['wrapper_sha256']==digest(NATIVE)
    assert config['enabled'] is True and config['max_script_seconds']==240
    jobs=json.loads((HOME/'cron/jobs.json').read_text(encoding='utf-8'))['jobs']
    job=next(j for j in jobs if j['id']==JOB_ID)
    assert job['enabled'] is False and job['state']=='paused'
    with sqlite3.connect((HOME/'cron/executions.db').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
        assert not db.execute("SELECT 1 FROM executions WHERE job_id=? AND status IN ('claimed','running','unknown') LIMIT 1",(JOB_ID,)).fetchone()
    state=Path(config['local_state_dir'])
    with sqlite3.connect((state/'watcher_cycle.sqlite').as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON');db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
    before=json.loads((state/'supervisor_lifecycle.json').read_text(encoding='utf-8'))
    fixed_names=('strategy.yaml','reflection_owner.json','supervisor_limits.json','wallet_account.json')
    fixed_before={name:digest(state/name) for name in fixed_names}
    launch_before=digest(LAUNCH)
    assert before['model_attempts_reserved']==0
    pre=json.loads(json.loads((HERE/'preflight_actual_paper_final.native-result.json').read_text())['stdout'])
    preflight_age=(datetime.now(timezone.utc)-datetime.fromisoformat(pre['observed_at'])).total_seconds()
    assert 0<=preflight_age<=120,'fresh observed preflight required'
    assert pre['supervisor_safety']['supervisor']['entry_halt'] is True
    assert all(row['orders']==0 and row['positions']=={} for row in pre['accounts'].values())
    runtime=json.loads((HERE.parent/'paper-discord-reporting-v1/proposed-release-plan.json').read_text())['new']
    assert all(digest(MAIN/'hermes_trading'/name)==value for name,value in runtime['runtime'].items())
    assert digest(MAIN/'scripts/hermes_watch.py')==runtime['watcher'] and digest(NATIVE)==runtime['native_launcher']
    os.environ['HERMES_PROPOSAL_PYTHON']=str(HOME/'hermes-agent/venv/Scripts/python.exe')
    spec=importlib.util.spec_from_file_location('actual_guarded_native_launcher',NATIVE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    observed=[]
    def command_run(command,**kwargs):
        assert len(command)==5 and Path(command[0]).resolve()==(MAIN/'.venv/Scripts/python.exe').resolve()
        assert command[1:4]==['-m','scripts.hermes_watch','--launch-file'] and Path(command[4]).resolve()==LAUNCH.resolve()
        assert Path(kwargs['cwd']).resolve()==MAIN.resolve()
        assert kwargs['capture_output'] is True and kwargs['text'] is True
        assert 0<kwargs['timeout']<=240
        child_env=kwargs['env']
        assert all(name not in child_env for name in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV'))
        assert 0<float(child_env['HERMES_TRADING_INVOCATION_DEADLINE'])-time.monotonic()<=235.1
        started=time.monotonic()
        try:
            result=subprocess.run(command,**kwargs)
        except subprocess.TimeoutExpired as error:
            observed.append(dict(status='native_child_timeout',elapsed_seconds=time.monotonic()-started,
                                 native_timeout_unchanged=True,raw_output_retained=False))
            raise
        observed.append(dict(status='native_child_terminal',exit_code=result.returncode,
                             elapsed_seconds=time.monotonic()-started,stdout_bytes=len(result.stdout.encode('utf-8')),
                             diagnostic=sanitized(result.stderr),native_argv_and_kwargs_unchanged=True))
        return result
    started_at=datetime.now(timezone.utc).isoformat()
    quiet=module.run_launch(LAUNCH,command_run=command_run)
    after=json.loads((state/'supervisor_lifecycle.json').read_text(encoding='utf-8'))
    fixed_after={name:digest(state/name) for name in fixed_names}
    runtime_after=all(digest(MAIN/'hermes_trading'/name)==value for name,value in runtime['runtime'].items())
    guards_after=runtime_after and digest(MAIN/'scripts/hermes_watch.py')==runtime['watcher'] and digest(NATIVE)==runtime['native_launcher']
    result=dict(started_at=started_at,finished_at=datetime.now(timezone.utc).isoformat(),native_result=quiet,
                observed_children=observed,source_sha256=dict(native=digest(NATIVE),watcher=digest(MAIN/'scripts/hermes_watch.py')),
                before={key:before[key] for key in ('cycles_started','model_attempts_reserved','deadline')},
                after={key:after[key] for key in ('cycles_started','model_attempts_reserved','deadline')},
                unchanged_runtime_guards=guards_after,strategy_and_limits_unchanged=fixed_before==fixed_after,
                fixed_before=fixed_before,fixed_after=fixed_after,native_launch_file_unchanged=digest(LAUNCH)==launch_before,
                limitations=['Operator process environment with the original job proposal interpreter; not a cron-daemon reproduction.',
                             'No raw stdout/stderr, exception message, environment values or credentials retained.'])
    result['operator_script_sha256']=digest(Path(__file__))
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    assert after['model_attempts_reserved']==before['model_attempts_reserved']==0
    assert after['deadline']==before['deadline']
    assert guards_after and fixed_before==fixed_after and digest(LAUNCH)==launch_before
if __name__=='__main__':main()
