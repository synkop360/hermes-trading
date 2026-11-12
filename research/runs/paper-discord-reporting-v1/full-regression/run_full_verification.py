"""Frozen-source full correctness discovery; no economic backtest or inference."""
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
import sys,time,unittest,warnings
from unittest.mock import patch
import httpx

PROJECT=Path(__file__).resolve().parents[4]
OUTPUT=Path(__file__).resolve().parent
STEM=sys.argv[1] if len(sys.argv)>1 else 'mvp-full-windows-v2'
if not STEM.replace('-','').isalnum():raise ValueError('simple verification artifact stem required')
sys.path[:0]=[str(PROJECT/'tests'),str(PROJECT)]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def pins():
    files=[path for folder in ('hermes_trading','tests','scripts','config/paper-beta-v1')
        for path in (PROJECT/folder).rglob('*') if path.is_file() and '__pycache__' not in path.parts]
    files=[p for p in files if p.suffix in ('.py','.json','.yaml')]
    files += [PROJECT/name for name in ('Dockerfile','pyproject.toml','uv.lock')]
    return {p.relative_to(PROJECT).as_posix():sha(p) for p in sorted(files)}
def write(name,value):
    (OUTPUT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')


class Result(unittest.TextTestResult):
    def stopTest(self,test):
        super().stopTest(test)
        if self.testsRun%25==0:
            write(STEM+'.progress.json',dict(status='running',tests=self.testsRun,
                failures=len(self.failures),errors=len(self.errors),current=str(test),
                observed_at=datetime.now(timezone.utc).isoformat()))


if __name__=='__main__':
    target=OUTPUT/(STEM+'.verification.json')
    if target.exists():raise SystemExit('preserve completed verification; use a new version')
    before=pins();write(STEM+'.source-sha256.json',before)
    started=datetime.now(timezone.utc).isoformat();tick=time.monotonic()
    log=OUTPUT/(STEM+'.log')
    with log.open('x',encoding='utf-8') as stream,redirect_stdout(stream),redirect_stderr(stream),warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always',ResourceWarning)
        suite=unittest.defaultTestLoader.discover(str(PROJECT/'tests'))
        original_async=httpx.AsyncHTTPTransport.handle_async_request
        original_sync=httpx.HTTPTransport.handle_request
        async def deny(transport,request,*a,**k):
            if request.url.scheme=='http' and request.url.host in {'127.0.0.1','::1'}:
                return await original_async(transport,request,*a,**k)
            raise AssertionError('external HTTP forbidden during synthetic correctness verification')
        def deny_sync(transport,request,*a,**k):
            if request.url.scheme=='http' and request.url.host in {'127.0.0.1','::1'}:
                return original_sync(transport,request,*a,**k)
            raise AssertionError('external HTTP forbidden during synthetic correctness verification')
        with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',deny),patch.object(httpx.HTTPTransport,'handle_request',deny_sync):
            result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
    after=pins();changed=[name for name in sorted(set(before)|set(after)) if before.get(name)!=after.get(name)]
    resource=[str(item.message) for item in captured if issubclass(item.category,ResourceWarning)]
    value=dict(scope='full Windows correctness discovery: disabled paper Discord reporting and native composition',
        started_at=started,observed_at=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-tick,
        tests=result.testsRun,passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
        failures=[(str(test),reason) for test,reason in result.failures],
        errors=[(str(test),reason) for test,reason in result.errors],
        skipped=[(str(test),reason) for test,reason in result.skipped],resource_warnings=resource,
        source_files=len(before),source_pins_unchanged=not changed,changed_sources=changed,
        source_pins=STEM+'.source-sha256.json',log_sha256=sha(log),runner_sha256=sha(Path(__file__)),
        deployed=False,activated=False,paid_calls=0,market_backtest=False,profitability_qualified=False)
    value['status']='pass_disabled_not_launch_ready' if result.wasSuccessful() and not changed and not resource else 'failed'
    write(target.name,value);write(STEM+'.progress.json',dict(status='terminal',tests=result.testsRun,result=value['status']))
    print(json.dumps(value,indent=2))
    raise SystemExit(0 if value['status']=='pass_disabled_not_launch_ready' else 1)
