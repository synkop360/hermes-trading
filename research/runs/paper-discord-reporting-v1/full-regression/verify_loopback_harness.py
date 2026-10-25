"""Reproduce an overbroad test harness guard; do not change product code."""
import ast
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import httpx

ROOT=Path.cwd().resolve();sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]
OUTPUT=Path(__file__).resolve().parent
values=[]
for name in ('run_full_verification_external_deny_v1.py','run_full_verification.py'):
    tree=ast.parse((OUTPUT/name).read_text())
    definitions=[node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
                 and node.name in ('deny','deny_sync')]
    assert len(definitions)==2
    namespace=dict(httpx=httpx,original_async=httpx.AsyncHTTPTransport.handle_async_request,
                   original_sync=httpx.HTTPTransport.handle_request)
    exec(compile(ast.Module(body=definitions,type_ignores=[]),name,'exec'),namespace)
    suite=unittest.TestLoader().loadTestsFromName('test_colocated.ColocatedEntrypointTests.test_colocated_control_api_serves_status_from_background_thread')
    log=OUTPUT/(name.removesuffix('.py')+'.loopback-check.txt')
    with log.open('x',encoding='utf-8') as stream,patch.object(httpx.AsyncHTTPTransport,'handle_async_request',namespace['deny']),patch.object(httpx.HTTPTransport,'handle_request',namespace['deny_sync']):
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    values.append(dict(runner=name,tests=result.testsRun,failures=[(str(t),e) for t,e in result.failures],
                       errors=[(str(t),e) for t,e in result.errors],passed=result.wasSuccessful(),log=log.name))
assert not values[0]['passed'] and values[1]['passed']
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),runs=values,
           conclusion='Only the harness changed: explicit HTTP loopback test servers permitted; external HTTP remains denied',
           discord_requests=0,product_changes=False)
(OUTPUT/'loopback-harness-verification.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps(value,indent=2))
