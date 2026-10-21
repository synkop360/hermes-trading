"""Disabled-by-default archive operation protocol for the shared paper journal.

No endpoint, transport, collector, account compaction or installation is provided.
Copy proofs are authenticated publisher claims; independent recovery must be
verified separately before a caller enables this API. Source account evidence
is never removed here. All shared retirement state commits in one transaction.
"""
from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from hermes_trading.control_auth import verify_command
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.reflection_supervision import _hash, _json, _now

ACCOUNTS = {f'{arm}_{wallet}' for arm in ('adaptive', 'control') for wallet in (50, 100, 150, 200)}
MAX_BATCHES = 259200
MAX_RECEIPT_BYTES = 8192
PURPOSE = 'paired_archive_retirement_v1'
REQUEST_FIELDS = {'schema_version', 'purpose', 'operation_id', 'experiment_id',
    'cohort_sha256', 'release_sha256', 'archive_sha256', 'account_snapshots', 'copies',
    'from_sequence', 'through_sequence', 'observed_complete', 'prefix_sha256',
    'previous_checkpoint_sha256', 'next_input_hash', 'revision'}


def _enabled(value):
    if value is not True:
        raise ValueError('archive operations are disabled')


def _digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('invalid archive digest')
    return value


def _ordinal(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_BATCHES:
        raise ValueError('invalid archive ordinal')
    return value


def _validate_request(request):
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError('archive request fields differ')
    if type(request['schema_version']) is not int or request['schema_version'] != 1 or request['purpose'] != PURPOSE:
        raise ValueError('archive purpose/schema differs')
    if not isinstance(request['operation_id'], str) or re.fullmatch('[0-9a-f]{32}', request['operation_id']) is None:
        raise ValueError('archive operation identity differs')
    if not isinstance(request['experiment_id'], str) or not 1 <= len(request['experiment_id']) <= 128:
        raise ValueError('archive experiment identity differs')
    for field in ('cohort_sha256', 'release_sha256', 'archive_sha256', 'prefix_sha256',
                  'previous_checkpoint_sha256', 'next_input_hash'):
        _digest(request[field])
    for field in ('from_sequence', 'through_sequence', 'observed_complete', 'revision'):
        _ordinal(request[field])
    if not request['from_sequence'] <= request['through_sequence'] < request['observed_complete']:
        raise ValueError('archive retirement must preserve a complete tail')
    accounts = request['account_snapshots']
    if not isinstance(accounts, dict) or set(accounts) != ACCOUNTS:
        raise ValueError('archive must bind all eight account snapshots')
    for value in accounts.values():
        _digest(value)
    copies = request['copies']
    if (not isinstance(copies, dict) or len(copies) != 2 or
            any(not isinstance(key, str) or re.fullmatch('[A-Za-z0-9_-]{1,64}', key) is None for key in copies)):
        raise ValueError('two named copy proofs are required')
    for value in copies.values():
        _digest(value)
    if len(set(copies.values())) != 2:
        raise ValueError('copy proofs must be distinct')
    if len(_json(request).encode()) > MAX_RECEIPT_BYTES:
        raise ValueError('archive request exceeds receipt bound')


@contextmanager
def _transaction(path):
    # Never create a financial journal accidentally when a path is wrong.
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=0, isolation_level=None)
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS paired_archive_operations (
        operation_id TEXT PRIMARY KEY, request TEXT NOT NULL, request_sha256 TEXT NOT NULL,
        receipt TEXT, receipt_sha256 TEXT, status TEXT NOT NULL, result TEXT, result_sha256 TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS paired_archive_checkpoint (
        id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, sha256 TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS paired_archive_result_digest ON paired_archive_operations(result_sha256)')


def _operation(db, operation_id):
    row = db.execute('SELECT request,request_sha256,receipt,receipt_sha256,status,result,result_sha256 '
                     'FROM paired_archive_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None:
        raise ValueError('archive operation is missing')
    try:
        request = json.loads(row[0]); receipt = None if row[2] is None else json.loads(row[2])
        result = None if row[5] is None else json.loads(row[5])
        _validate_request(request)
        if request['operation_id'] != operation_id or _hash(request) != row[1]:
            raise ValueError('archive operation request checksum differs')
        if row[4] not in {'prepared', 'acknowledged', 'retired'}:
            raise ValueError('archive operation status differs')
        if ((receipt is None) != (row[4] == 'prepared') or
                (receipt is not None and _hash(receipt) != row[3]) or
                (result is None) != (row[4] != 'retired') or
                (result is not None and _hash(result) != row[6])):
            raise ValueError('archive operation evidence checksum differs')
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError('archive operation evidence is malformed') from error
    return {'request': request, 'receipt': receipt, 'status': row[4], 'result': result}


def _checkpoint(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_archive_checkpoint'").fetchone() is None:
        if (db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_archive_operations'").fetchone() is not None and
                db.execute("SELECT 1 FROM paired_archive_operations WHERE status='retired' LIMIT 1").fetchone()):
            raise ValueError('archive checkpoint table is missing after retirement')
        return None
    row = db.execute('SELECT state,sha256 FROM paired_archive_checkpoint WHERE id=1').fetchone()
    if row is None:
        # Missing checkpoint after committed retirement is not a new experiment.
        if db.execute("SELECT 1 FROM paired_archive_operations WHERE status='retired' LIMIT 1").fetchone():
            raise ValueError('archive checkpoint is missing')
        return None
    try:
        state = json.loads(row[0])
        if not isinstance(state, dict) or _hash(state) != row[1]:
            raise ValueError('archive checkpoint checksum differs')
        operation = _operation(db, state['operation_id'])
        current = state; seen = set()
        while True:
            request = operation['request']
            if request['operation_id'] in seen or len(seen) >= MAX_BATCHES:
                raise ValueError('archive checkpoint ancestry exceeds bound or cycles')
            seen.add(request['operation_id'])
            if operation['status'] != 'retired' or operation['result'] != current or current != _result(request):
                raise ValueError('archive checkpoint operation differs')
            if request['revision'] == 1:
                if request['previous_checkpoint_sha256'] != _hash(None) or request['from_sequence'] != 1:
                    raise ValueError('archive checkpoint origin differs')
                break
            prior = db.execute('SELECT operation_id FROM paired_archive_operations WHERE result_sha256=? AND status=\'retired\'',
                               (request['previous_checkpoint_sha256'],)).fetchmany(2)
            if len(prior) != 1:
                raise ValueError('archive checkpoint predecessor missing or ambiguous')
            predecessor = _operation(db, prior[0][0])
            previous = predecessor['result']
            if (not isinstance(previous, dict) or _hash(previous) != request['previous_checkpoint_sha256'] or
                    previous['revision'] + 1 != current['revision'] or
                    previous['retired_complete'] + 1 != request['from_sequence'] or
                    previous['experiment_id'] != current['experiment_id'] or
                    previous['cohort_sha256'] != current['cohort_sha256'] or
                    previous['observed_complete'] > current['observed_complete']):
                raise ValueError('archive checkpoint predecessor differs')
            current = previous; operation = predecessor
    except (TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError('archive checkpoint is malformed') from error
    return state


def _blob_hash(value):
    if isinstance(value, str):
        value = value.encode()
    if not isinstance(value, bytes):
        raise ValueError('archive journal payload type differs')
    return hashlib.sha256(value).hexdigest()


def _read_journal(db, checkpoint, check=lambda:None):
    check()
    identity = db.execute('SELECT manifest_hash FROM paired_identity WHERE id=1').fetchone()
    if identity is None:
        raise ValueError('archive cohort identity is missing')
    _digest(identity[0])
    if checkpoint is not None and checkpoint['cohort_sha256'] != identity[0]:
        raise ValueError('archive cohort identity differs')
    retired = 0 if checkpoint is None else checkpoint['retired_complete']
    rows = db.execute('SELECT rowid,batch_id,status,payload,input_hash,completed FROM paired_batches ORDER BY rowid').fetchall()
    result = []; pending = False
    for ordinal, row in enumerate(rows, retired + 1):
        check()
        sequence, batch, status, payload, input_hash, completed = row
        if sequence != ordinal or status not in {'pending', 'complete'} or pending:
            raise ValueError('archive journal sequence/status differs')
        if _hash(load_packet(db, payload)) != input_hash:
            raise ValueError('archive original packet input hash differs')
        accounts = load_packet(db, completed)
        settlement = db.execute('SELECT payload FROM paired_settlements WHERE batch_id=?', (batch,)).fetchone()
        if status == 'complete':
            if (set(accounts) != ACCOUNTS or settlement is None or set(load_packet(db, settlement[0])) != ACCOUNTS):
                raise ValueError('archive complete batch lacks all eight settlements')
        else:
            pending = True
        result.append({'sequence': sequence, 'batch_id': batch, 'status': status, 'input_hash': input_hash,
            'payload_sha256': _blob_hash(payload), 'completed_sha256': _blob_hash(completed),
            'settlement_sha256': None if settlement is None else _blob_hash(settlement[0])})
    complete = retired + len(result) - int(pending)
    if complete > MAX_BATCHES or (checkpoint is not None and complete < checkpoint['observed_complete']):
        raise ValueError('archive lifetime count regressed or exceeds bound')
    return {'rows': result, 'complete': complete, 'pending': pending, 'cohort_sha256': identity[0]}


def _prefix(checkpoint, rows, through):
    value = _hash([]) if checkpoint is None else checkpoint['prefix_sha256']
    for row in rows:
        if row['sequence'] > through:
            break
        value = _hash({'previous': value, 'record': row})
    return value


def _result(request):
    return {'schema_version': 1, 'operation_id': request['operation_id'],
        'experiment_id': request['experiment_id'], 'cohort_sha256': request['cohort_sha256'],
        'retired_complete': request['through_sequence'], 'prefix_sha256': request['prefix_sha256'],
        'observed_complete': request['observed_complete'], 'revision': request['revision']}


def prepare(path, *, enabled=False, experiment_id, release_sha256, archive_sha256,
            account_snapshots, copies, through_sequence, operation_id=None):
    """Pin a bounded request from an exact completed journal prefix.

    Snapshot/copy digests must come from a verified export, not directory names
    or assumed backup success. This primitive does not attest those exports.
    """
    _enabled(enabled)
    _ordinal(through_sequence)
    if operation_id is not None and (not isinstance(operation_id, str) or re.fullmatch('[0-9a-f]{32}', operation_id) is None):
        raise ValueError('archive operation identity differs')
    with _transaction(path) as db:
        _schema(db)
        checkpoint = _checkpoint(db)
        if operation_id is not None and db.execute('SELECT 1 FROM paired_archive_operations WHERE operation_id=?', (operation_id,)).fetchone():
            frozen = _operation(db, operation_id)['request']
            supplied = {'experiment_id': experiment_id, 'release_sha256': release_sha256,
                'archive_sha256': archive_sha256, 'account_snapshots': account_snapshots,
                'copies': copies, 'through_sequence': through_sequence}
            if any(frozen[key] != value for key, value in supplied.items()):
                raise ValueError('archive operation request differs')
            return frozen
        journal = _read_journal(db, checkpoint)
        if journal['pending']:
            raise ValueError('pending batch forbids archive preparation')
        _ordinal(through_sequence)
        retired = 0 if checkpoint is None else checkpoint['retired_complete']
        if not retired < through_sequence < journal['complete']:
            raise ValueError('archive retirement must preserve a complete tail')
        if checkpoint is not None and checkpoint['experiment_id'] != experiment_id:
            raise ValueError('archive experiment identity differs')
        request = {'schema_version': 1, 'purpose': PURPOSE, 'operation_id': operation_id or uuid.uuid4().hex,
            'experiment_id': experiment_id, 'cohort_sha256': journal['cohort_sha256'],
            'release_sha256': release_sha256, 'archive_sha256': archive_sha256,
            'account_snapshots': account_snapshots, 'copies': copies, 'from_sequence': retired + 1,
            'through_sequence': through_sequence, 'observed_complete': journal['complete'],
            'prefix_sha256': _prefix(checkpoint, journal['rows'], through_sequence),
            'previous_checkpoint_sha256': _hash(checkpoint),
            'next_input_hash': next(row['input_hash'] for row in journal['rows'] if row['sequence'] == through_sequence + 1),
            'revision': 1 if checkpoint is None else checkpoint['revision'] + 1}
        _validate_request(request)
        if db.execute("SELECT 1 FROM paired_archive_operations WHERE status!='retired' LIMIT 1").fetchone():
            raise ValueError('another archive operation is unresolved')
        db.execute('INSERT INTO paired_archive_operations VALUES(?,?,?,NULL,NULL,\'prepared\',NULL,NULL)',
                   (request['operation_id'], _json(request), _hash(request)))
    return request


def acknowledge(path, operation_id, receipts, keys, *, now, enabled=False, fault=None):
    """Authenticate both publisher claims and persist the exact receipt once.

    Retrying a previously accepted identical receipt returns its durable state
    even after expiry. A new signature/timestamp cannot reset the operation.
    """
    _enabled(enabled)
    current = _now(now)
    with _transaction(path) as db:
        operation = _operation(db, operation_id); request = operation['request']
        if not isinstance(receipts, dict) or len(_json(receipts).encode()) > 2 * MAX_RECEIPT_BYTES:
            raise ValueError('archive receipts exceed bound')
        if operation['status'] != 'prepared':
            if operation['receipt'] != receipts:
                raise ValueError('archive receipt differs from accepted evidence')
            return {'operation_id': operation_id, 'status': operation['status']}
        if (not isinstance(keys, dict) or set(keys) != set(request['copies']) or
                set(receipts) != set(keys) or any(not isinstance(key, str) or len(key) < 32 for key in keys.values()) or
                len(set(keys.values())) != 2):
            raise ValueError('two distinct authenticated copy publishers required')
        for name, receipt in receipts.items():
            if (not isinstance(receipt, dict) or set(receipt) != {'body', 'nonce', 'timestamp', 'signature'} or
                    receipt['body'] != _json(request) or receipt['nonce'] != operation_id):
                raise ValueError('archive receipt binding differs')
            try:
                at = datetime.fromisoformat(receipt['timestamp'])
                if at.tzinfo is None or not 0 <= (current - at).total_seconds() < 300:
                    raise ValueError('archive receipt validity differs')
            except (TypeError, AttributeError) as error:
                raise ValueError('archive receipt validity differs') from error
            signature = _digest(receipt['signature'])
            if not verify_command(keys[name], operation_id, at, receipt['body'], signature, set(), now=current):
                raise ValueError('archive receipt authentication failed')
        db.execute('UPDATE paired_archive_operations SET receipt=?,receipt_sha256=?,status=\'acknowledged\' WHERE operation_id=?',
                   (_json(receipts), _hash(receipts), operation_id))
        if fault:
            fault('before_ack_commit')
    if fault:
        fault('after_ack_commit')
    return {'operation_id': operation_id, 'status': 'acknowledged'}


def retire(path, operation_id, *, enabled=False, fault=None):
    """Commit shared prefix retirement and its lifetime checkpoint atomically.

    Leaves at least one completed tail batch and every account financial row.
    The caller owns the cohort writer boundary and verified copy policy.
    """
    _enabled(enabled)
    with _transaction(path) as db:
        operation = _operation(db, operation_id); request = operation['request']
        checkpoint = _checkpoint(db)
        if operation['status'] == 'retired':
            if checkpoint is None or checkpoint['revision'] < operation['result']['revision']:
                raise ValueError('archive checkpoint regressed')
            return operation['result']
        if operation['status'] != 'acknowledged':
            raise ValueError('archive operation has not been acknowledged')
        journal = _read_journal(db, checkpoint)
        if journal['pending']:
            raise ValueError('pending batch forbids archive retirement')
        cut = request['through_sequence']
        tail = next((row for row in journal['rows'] if row['sequence'] == cut + 1), None)
        if (request['previous_checkpoint_sha256'] != _hash(checkpoint) or
                journal['cohort_sha256'] != request['cohort_sha256'] or
                journal['complete'] < request['observed_complete'] or tail is None or
                tail['input_hash'] != request['next_input_hash'] or
                _prefix(checkpoint, journal['rows'], cut) != request['prefix_sha256']):
            raise ValueError('archive prefix/checkpoint binding differs')
        result = _result(request)
        if fault:
            fault('before_delete')
        db.execute('DELETE FROM paired_settlements WHERE batch_id IN (SELECT batch_id FROM paired_batches WHERE rowid<=?)', (cut,))
        db.execute('DELETE FROM paired_batches WHERE rowid<=?', (cut,))
        if fault:
            fault('after_delete')
        db.execute('UPDATE paired_archive_operations SET status=\'retired\',result=?,result_sha256=? WHERE operation_id=?',
                   (_json(result), _hash(result), operation_id))
        db.execute('INSERT OR REPLACE INTO paired_archive_checkpoint VALUES(1,?,?)', (_json(result), _hash(result)))
        _read_journal(db, _checkpoint(db))
        if fault:
            fault('after_checkpoint')
    if fault:
        fault('after_commit')
    return result


def lifetime(path, *, minimum_complete=0):
    """Read checkpoint plus retained original ordinals; reject missing history."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        return read_lifetime(db, minimum_complete=minimum_complete)


def read_lifetime(db, *, minimum_complete=0):
    """Use the caller's existing read snapshot for coherent worker statistics."""
    _ordinal(minimum_complete, zero=True)
    checkpoint = _checkpoint(db); journal = _read_journal(db, checkpoint)
    if journal['complete'] < minimum_complete:
        raise ValueError('archive lifetime count regressed below trusted witness')
    retired = 0 if checkpoint is None else checkpoint['retired_complete']
    return {'complete': journal['complete'], 'active_complete': journal['complete'] - retired,
        'retired_complete': retired, 'pending': journal['pending'],
        'revision': 0 if checkpoint is None else checkpoint['revision']}
