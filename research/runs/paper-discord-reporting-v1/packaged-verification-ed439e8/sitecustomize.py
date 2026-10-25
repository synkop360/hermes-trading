"""Offline verification guard inherited by native child interpreters."""
import atexit
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

_started = time.monotonic()
_export = Path(os.environ['HERMES_PACKAGED_VERIFY_EXPORT']).resolve()
_output = Path(os.environ['HERMES_PACKAGED_VERIFY_OUTPUT']).resolve()
_tests = _export / 'tests'
sys.dont_write_bytecode = True
sys.path[:] = [str(_tests), str(_export)] + [
    entry for entry in sys.path if entry not in (str(_tests), str(_export))
]

import httpx

_denials = []

def deny_sync(self, request):
    _denials.append('HTTPTransport.handle_request')
    raise AssertionError('packaged verification: raw HTTP forbidden')

async def deny_async(self, request):
    _denials.append('AsyncHTTPTransport.handle_async_request')
    raise AssertionError('packaged verification: raw HTTP forbidden')

httpx.HTTPTransport.handle_request = deny_sync
httpx.AsyncHTTPTransport.handle_async_request = deny_async

def module_receipt():
    imported = {}
    violations = {}
    for name, module in list(sys.modules.items()):
        if name != 'hermes_trading' and not name.startswith('hermes_trading.') and name != 'scripts' and not name.startswith('scripts.') and not name.startswith('test_') and name != 'release_transition_fixtures':
            continue
        source = getattr(module, '__file__', None)
        if source:
            source = str(Path(source).resolve())
            imported[name] = source
            if not Path(source).is_relative_to(_export):
                violations[name] = source
        else:
            for location in getattr(module, '__path__', []):
                if not Path(location).resolve().is_relative_to(_export):
                    violations[name] = str(location)
    return imported, violations

def write_receipt():
    imported, violations = module_receipt()
    value = dict(
        observed_at=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
        argv=sys.argv, cwd=os.getcwd(), executable=sys.executable,
        elapsed_seconds=time.monotonic()-_started,
        sys_path_first_two=sys.path[:2], export=str(_export),
        raw_http_denials=_denials, imported_modules=imported,
        import_provenance_violations=violations,
        raw_transport_guards_active=(httpx.HTTPTransport.handle_request is deny_sync and
            httpx.AsyncHTTPTransport.handle_async_request is deny_async),
    )
    (_output / ('process-' + str(os.getpid()) + '.json')).write_text(
        json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')

atexit.register(write_receipt)
