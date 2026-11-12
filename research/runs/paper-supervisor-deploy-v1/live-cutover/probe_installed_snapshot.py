"""Remote read-only typed observation of the installed snapshot retry path."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash
from hermes_trading.supervisor_sync import make_snapshot_with_retry

runtime = _release_hashes()
assert _hash(runtime) == 'ffe95446f146d79441862a3eae14228865fd7bf460fc7c7311f5ead3510834eb'

def classify(error):
    rows, seen = [], set()
    for _ in range(8):
        if error is None or id(error) in seen:
            break
        seen.add(id(error))
        kind = type(error)
        permitted = (ValueError, RuntimeError, OSError, FileNotFoundError, PermissionError,
                     TimeoutError, sqlite3.OperationalError, sqlite3.DatabaseError)
        row = {'type': kind.__name__ if kind in permitted else 'unclassified', 'frames': []}
        code = getattr(error, 'sqlite_errorcode', None)
        if isinstance(error, sqlite3.Error) and type(code) is int:
            row['sqlite_code'] = code
        tb = error.__traceback__
        while tb:
            filename = tb.tb_frame.f_code.co_filename
            prefix = '/app/hermes_trading/'
            if filename.startswith(prefix) and filename[len(prefix):] in runtime:
                row['frames'].append({'source': 'hermes_trading/' + filename[len(prefix):], 'line': tb.tb_lineno})
            tb = tb.tb_next
        row['frames'] = row['frames'][-8:]
        rows.append(row)
        error = error.__cause__ or error.__context__
    return rows

observations = []
start = time.monotonic()
for i in range(1):
    if i:
        if time.monotonic() - start >= 25:
            break
        time.sleep(2)
    at, tick = datetime.now(timezone.utc).isoformat(), time.monotonic()
    try:
        snapshot = make_snapshot_with_retry(Path('/app/state/adaptive_200'))
        row = {'at': at, 'status': 'validated', 'schema': snapshot['schema_version']}
    except Exception as error:
        row = {'at': at, 'status': 'rejected', 'chain': classify(error)}
    row['elapsed_seconds'] = time.monotonic() - tick
    observations.append(row)
    if row['status'] == 'rejected':
        break
print(json.dumps({'source_map_verified': True, 'runtime_files': len(runtime), 'observations': observations,
                  'health_receipts_published': False, 'model_invoked': False,
                  'raw_messages_retained': False, 'elapsed_seconds': time.monotonic() - start}))
