"""Bounded read-only facts, including persisted Discord delivery markers."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

# The caller prepends the previously reviewed inspector definitions without its CLI.
root = Path('/app/state')
try:
    from hermes_trading.paper_publication import _release_hashes
    from hermes_trading.reflection_supervision import _hash
    from hermes_trading.paper_safety import load_policy, inspect_volume
    runtime = _release_hashes()
    if _hash(runtime) != '0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c':
        raise ValueError('unexpected_runtime')
    result = inspect(root, 1741, 5)
    heartbeat, _ = bounded_json(root/'adaptive_200/heartbeat.json', MAX_RECORD)
    result['runtime'] = dict(files=len(runtime), expected_deployed_map=True,
        loaded_release_matches_disk=(heartbeat.get('worker_release') or {}).get('hashes')==runtime)
    policy = load_policy(root, EXPERIMENT)
    result['volume'] = inspect_volume(policy) if policy is not None else {'status':'policy_missing'}
    trading = {}
    for name in ACCOUNTS:
        try:
            with closing(sqlite3.connect((root/name/'trading.db').as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
                deadline = time.monotonic()+.2
                db.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                trading[name] = dict(status='observed',orders=number(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]),
                    positions={code(key):number(count) for key,count in db.execute('SELECT status,COUNT(*) FROM positions GROUP BY status') if code(key)},
                    decisions={code(key):number(count) for key,count in db.execute('SELECT action,COUNT(*) FROM decisions GROUP BY action') if code(key)})
        except (OSError,ValueError,sqlite3.Error) as error:
            trading[name] = dict(status='unavailable',error_type=type(error).__name__)
    result['trading'] = trading
    result['limitations'].append('Trading reads and volume scan follow the cohort read; they are not one cross-database transaction.')
except Exception as error:
    result = dict(kind='read_only_current_paper_observation',status='unavailable',error_type=type(error).__name__)
print(json.dumps(result,sort_keys=True,allow_nan=False))
