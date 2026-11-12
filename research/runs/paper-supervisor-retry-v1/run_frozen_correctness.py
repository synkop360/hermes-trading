"""Source-pinned offline correctness checks; allows loopback HTTP test servers."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
import warnings
from unittest.mock import patch

import httpx

PROJECT = Path(sys.argv[3]).resolve()  # explicit frozen local package, never a live state directory
OUTPUT = Path(__file__).resolve().parent/'verification'
OUTPUT.mkdir(exist_ok=True)
SCOPE = sys.argv[1]
STEM = sys.argv[2]
if SCOPE not in {'affected','full'} or not STEM.replace('-','').isalnum():
    raise ValueError('explicit scope and simple new artifact name required')
sys.path[:0] = [str(PROJECT/'tests'),str(PROJECT)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins():
    files = [p for folder in ('hermes_trading','tests','scripts','config/paper-beta-v1')
             for p in (PROJECT/folder).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.json','.yaml'}]
    files += [PROJECT/name for name in ('Dockerfile','pyproject.toml','uv.lock')]
    return {p.relative_to(PROJECT).as_posix():sha(p) for p in sorted(files)}


def save(suffix,value):
    (OUTPUT/(STEM+suffix)).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def cases(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from cases(item)
        else:yield item.id()


class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.outcomes = {}
        self.started_ids = []

    def startTest(self,test):
        self.started_ids.append(test.id())
        super().startTest(test)

    def addSuccess(self,test):
        super().addSuccess(test)
        self.outcomes[test.id()] = 'passed'

    def addFailure(self,test,error):
        super().addFailure(test,error)
        self.outcomes[test.id()] = 'failed'

    def addError(self,test,error):
        super().addError(test,error)
        self.outcomes[test.id()] = 'error'

    def addSkip(self,test,reason):
        super().addSkip(test,reason)
        self.outcomes[test.id()] = 'skipped'

    def addSubTest(self,test,subtest,error):
        super().addSubTest(test,subtest,error)
        if error is not None:
            self.outcomes[test.id()] = 'failed' if issubclass(error[0],test.failureException) else 'error'

    def addExpectedFailure(self,test,error):
        super().addExpectedFailure(test,error)
        self.outcomes[test.id()] = 'expected_failure'

    def addUnexpectedSuccess(self,test):
        super().addUnexpectedSuccess(test)
        self.outcomes[test.id()] = 'unexpected_success'

    def stopTest(self,test):
        super().stopTest(test)
        if self.testsRun%25==0:
            save('.progress.json',dict(status='running',tests=self.testsRun,current=test.id(),
                failures=len(self.failures),errors=len(self.errors),observed_at=datetime.now(timezone.utc).isoformat()))


if __name__=='__main__':
    target = OUTPUT/(STEM+'.result.json')
    if target.exists():raise SystemExit('preserve terminal evidence; use a new artifact name')
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    before = pins()
    save('.source-sha256.json',before)
    log = OUTPUT/(STEM+'.log')
    with log.open('x',encoding='utf-8') as stream, redirect_stdout(stream), redirect_stderr(stream), warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always',ResourceWarning)
        if SCOPE=='full':
            suite = unittest.defaultTestLoader.discover(str(PROJECT/'tests'))
        else:
            modules = ['test_supervisor_snapshot_retry','test_supervisor_sync','test_paired_primary_supervision',
                'test_paper_snapshot_release_transition','test_paper_discord_transition','test_paper_release_transition',
                'test_paper_publication','test_paper_safety','test_supervisor_launch','test_native_supervisor_launcher',
                'test_paired_worker','test_paper_safety_worker']
            suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
        catalog = list(cases(suite))
        save('.catalog.json',catalog)
        original_async = httpx.AsyncHTTPTransport.handle_async_request
        original_sync = httpx.HTTPTransport.handle_request
        async def guarded_async(transport,request,*args,**kwargs):
            if request.url.scheme=='http' and request.url.host in {'127.0.0.1','::1'}:
                return await original_async(transport,request,*args,**kwargs)
            raise AssertionError('external HTTP forbidden during local correctness checks')
        def guarded_sync(transport,request,*args,**kwargs):
            if request.url.scheme=='http' and request.url.host in {'127.0.0.1','::1'}:
                return original_sync(transport,request,*args,**kwargs)
            raise AssertionError('external HTTP forbidden during local correctness checks')
        with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',guarded_async), \
                patch.object(httpx.HTTPTransport,'handle_request',guarded_sync):
            result = unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
    after = pins()
    changed = [key for key in sorted(set(before)|set(after)) if before.get(key)!=after.get(key)]
    resources = [str(item.message) for item in captured if issubclass(item.category,ResourceWarning)]
    unrun = sorted(set(catalog)-set(result.started_ids))
    value = dict(scope=SCOPE,started_at=started,finished_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=time.monotonic()-tick,catalog_cases=len(catalog),tests_run=result.testsRun,
        passed=sum(result.outcomes.get(key)=='passed' for key in catalog),
        expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses),
        skipped=[(str(test),reason) for test,reason in result.skipped],
        failures=[(str(test),reason) for test,reason in result.failures],
        errors=[(str(test),reason) for test,reason in result.errors],unrun_cases=unrun,
        resource_warnings=resources,source_files=len(before),changed_sources=changed,
        log_sha256=sha(log),runner_sha256=sha(Path(__file__)),deployed=False,activated=False,
        market_backtest=False,training=False,paid_calls=0)
    value['status'] = 'passed_local_only' if result.wasSuccessful() and not changed and not resources and not unrun else 'failed'
    save('.outcomes.json',result.outcomes)
    save('.result.json',value)
    save('.progress.json',dict(status='terminal',tests=result.testsRun,result=value['status']))
    print(json.dumps({k:v for k,v in value.items() if k not in {'failures','errors','unrun_cases','skipped'}},indent=2))
    raise SystemExit(0 if value['status']=='passed_local_only' else 1)
