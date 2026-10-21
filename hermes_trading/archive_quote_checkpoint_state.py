"""Bounded checkpoint identities and retained financial replay authority."""
import hashlib

from hermes_trading.reflection_supervision import _hash, _json
from hermes_trading.archive_quote_state import timestamp
from hermes_trading.archive_journal import _digest

PURPOSE = 'paired_quote_control_checkpoint_v1'
INCIDENT = 'archive:quote-checkpoint'
MAX_CHECKPOINT_BYTES = 65536
MAX_TAIL = 32
MAX_CONTROL_BYTES = 4*1024**2
MAX_DETAILED_BYTES = 3*1024**2
MAX_POSITIONS = 1000000


def ordinal(value):
    if type(value) is not int or not 1 <= value <= 2**63-1:
        raise ValueError('quote-checkpoint ordinal differs')
    return value


def operation_id(cohort_sha256, sequence):
    _digest(cohort_sha256)
    ordinal(sequence)
    return _hash(dict(purpose='paired_quote_request_v1', cohort_sha256=cohort_sha256, ordinal=sequence))[:32]


def next_ordinal(db, checkpoint):
    high = 0 if checkpoint is None else ordinal(checkpoint['through_ordinal'])
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_quote_retention'").fetchone():
        high = max(high, db.execute('SELECT COALESCE(MAX(rowid),0) FROM paired_quote_retention').fetchone()[0])
    return ordinal(high+1)


def _summary_types(value):
    if (type(value) is not dict or set(value) != {'positions_highwater', 'nonterminal_positions',
            'closed_positions_count', 'closed_positions_sha256'} or
            type(value['positions_highwater']) is not int or not 0 <= value['positions_highwater'] <= 2**63-1 or
            type(value['closed_positions_count']) is not int or not 0 <= value['closed_positions_count'] <= MAX_POSITIONS or
            type(value['nonterminal_positions']) is not list or len(value['nonterminal_positions']) > 3):
        raise ValueError('quote-checkpoint terminal summary differs')
    _digest(value['closed_positions_sha256'])
    seen = set()
    for row in value['nonterminal_positions']:
        if (type(row) is not list or len(row) != 4 or type(row[0]) is not int or
                not 1 <= row[0] <= value['positions_highwater'] or
                any(not isinstance(item, str) or not 1 <= len(item) <= 2048 for item in row[1:]) or
                row[3] == 'closed' or row[1] in seen):
            raise ValueError('quote-checkpoint captured nonterminal identity differs')
        seen.add(row[1])
    return value


def _closed_prefix(db, highwater, excluded, check):
    digest = hashlib.sha256()
    count = 0
    rows = db.execute("SELECT rowid,* FROM positions WHERE rowid<=? AND status='closed' ORDER BY rowid", (highwater,))
    identity = [column[0] for column in rows.description].index('position_id')
    for row in rows:
        check()
        if row[identity] in excluded:
            continue
        count += 1
        raw = _json(list(row)).encode()
        if count > MAX_POSITIONS or len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError('quote-checkpoint retained finance bound exceeded')
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)
    return count, digest.hexdigest()


def terminal_summary(db, assets, check=lambda: None):
    """Capture actual terminal membership, independent of timestamp equality."""
    check()
    high = db.execute('SELECT COALESCE(MAX(rowid),0) FROM positions').fetchone()[0]
    rows = [list(row) for row in db.execute('''SELECT rowid,position_id,asset,status FROM positions
        WHERE status!='closed' ORDER BY rowid LIMIT ?''', (len(assets)+1,))]
    if len(rows) > len(assets) or any(row[2] not in assets for row in rows):
        raise ValueError('quote-checkpoint captured active positions exceed asset bound')
    count, digest = _closed_prefix(db, high, {row[1] for row in rows}, check)
    return _summary_types(dict(positions_highwater=high, nonterminal_positions=rows,
                               closed_positions_count=count, closed_positions_sha256=digest))


def assert_terminal_summary(db, value, check=lambda: None):
    _summary_types(value)
    count, digest = _closed_prefix(db, value['positions_highwater'],
                                  {row[1] for row in value['nonterminal_positions']}, check)
    if (count, digest) != (value['closed_positions_count'], value['closed_positions_sha256']):
        raise ValueError('quote-checkpoint captured closed financial prefix changed')


def terminal_quote_retired(db, asset, timeframe, close_timestamp, summary):
    if not timeframe.startswith('paper-position:'):
        return False
    _summary_types(summary)
    identity = timeframe[len('paper-position:'):]
    if identity in {row[1] for row in summary['nonterminal_positions']}:
        return False
    row = db.execute('SELECT rowid,asset,status,closed_at FROM positions WHERE position_id=?', (identity,)).fetchone()
    return (row is not None and row[0] <= summary['positions_highwater'] and row[1] == asset and
            row[2] == 'closed' and row[3] is not None and timestamp(close_timestamp) <= timestamp(row[3]))


def validate_checkpoint(value):
    from hermes_trading.archive_journal import ACCOUNTS
    from hermes_trading import archive_quote_state as state
    from pathlib import Path
    fields = {'schema_version', 'purpose', 'experiment_id', 'cohort_sha256', 'release_sha256',
              'from_ordinal', 'through_ordinal', 'cumulative_outcomes', 'previous_checkpoint_sha256',
              'segment_pin', 'segment_pin_sha256', 'accounts', 'legacy_operation_highwater'}
    if (type(value) is not dict or set(value) != fields or type(value['schema_version']) is not int or
            value['schema_version'] != 1 or value['purpose'] != PURPOSE or
            not isinstance(value['experiment_id'], str) or not value['experiment_id'] or
            type(value['cumulative_outcomes']) is not int or value['cumulative_outcomes'] < 0 or
            type(value['legacy_operation_highwater']) is not int or not 0 <= value['legacy_operation_highwater'] <= 2**63-1 or
            type(value['accounts']) is not dict or set(value['accounts']) != ACCOUNTS):
        raise ValueError('quote-checkpoint scope differs')
    ordinal(value['from_ordinal']); ordinal(value['through_ordinal'])
    if not 0 <= value['through_ordinal']-value['from_ordinal'] < MAX_TAIL:
        raise ValueError('quote-checkpoint prefix bound differs')
    for key in ('cohort_sha256', 'release_sha256', 'previous_checkpoint_sha256', 'segment_pin_sha256'):
        _digest(value[key])
    pin = value['segment_pin']
    if (type(pin) is not dict or pin.get('purpose') != 'paired_quote_control_prefix_v1' or
            _hash(pin) != value['segment_pin_sha256'] or
            any(pin.get(key) != value[key] for key in ('experiment_id', 'cohort_sha256', 'release_sha256',
                                                      'from_ordinal', 'through_ordinal'))):
        raise ValueError('quote-checkpoint original segment binding differs')
    for account in value['accounts'].values():
        validate_account(account)
        if Path(account['directory']).name != account['directory']:
            raise ValueError('quote-checkpoint account directory differs')
        if len(account['active_floors']) > 3:
            raise ValueError('quote-checkpoint retained active floor bound exceeded')
        for key, floor in account['active_floors'].items():
            if (type(floor) is not dict or set(floor) != {'schema_version', 'purpose', 'asset', 'timeframe',
                    'through', 'operation_id', 'plan_sha256'} or floor['schema_version'] != 1 or
                    floor['purpose'] != state.PURPOSE or key != state.floor_key(floor['asset'], floor['timeframe'])):
                raise ValueError('quote-checkpoint retained floor differs')
            timestamp(floor['through']); _digest(floor['plan_sha256'])
    if len(_json(value).encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError('quote-checkpoint exceeds byte bound')
    return value


def validate_account(value):
    if (type(value) is not dict or set(value) != {'directory', 'terminal', 'active_floors',
            'decision_highwater', 'projection_highwater'} or not isinstance(value['directory'], str) or
            type(value['active_floors']) is not dict or any(type(value[key]) is not int or
            not 0 <= value[key] <= 2**63-1 for key in ('decision_highwater', 'projection_highwater'))):
        raise ValueError('quote-checkpoint account authority differs')
    _summary_types(value['terminal'])
    from hermes_trading import archive_quote_state as state
    if len(value['active_floors']) > len(value['terminal']['nonterminal_positions']):
        raise ValueError('quote-checkpoint active floor membership exceeds captured positions')
    captured = {(row[2], row[1]) for row in value['terminal']['nonterminal_positions']}
    for key, floor in value['active_floors'].items():
        if (type(floor) is not dict or set(floor) != {'schema_version', 'purpose', 'asset', 'timeframe',
                'through', 'operation_id', 'plan_sha256'} or type(floor['schema_version']) is not int or
                floor['schema_version'] != 1 or floor['purpose'] != state.PURPOSE or
                not isinstance(floor['asset'], str) or not isinstance(floor['timeframe'], str) or
                not floor['timeframe'].startswith('paper-position:') or
                (floor['asset'], floor['timeframe'][len('paper-position:'):]) not in captured or
                key != state.floor_key(floor['asset'], floor['timeframe']) or
                not isinstance(floor['operation_id'], str) or len(floor['operation_id']) != 32 or
                any(c not in '0123456789abcdef' for c in floor['operation_id'])):
            raise ValueError('quote-checkpoint retained active floor differs')
        timestamp(floor['through']); _digest(floor['plan_sha256'])
    return value


def read_checkpoint(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_quote_control_checkpoint'").fetchone():
        return None
    row = db.execute('SELECT state,sha256 FROM paired_quote_control_checkpoint WHERE id=1').fetchone()
    if row is None:
        return None
    import json
    if not isinstance(row[0], str) or len(row[0].encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError('quote-checkpoint stored byte bound differs')
    try:
        value = validate_checkpoint(json.loads(row[0]))
        if _hash(value) != row[1]:
            raise ValueError('quote-checkpoint stored checksum differs')
        return value
    except (TypeError, KeyError, RecursionError) as error:
        raise ValueError('quote-checkpoint stored record differs') from error


def context(db):
    from hermes_trading import archive_quote_state as state
    from hermes_trading.archive_journal import ACCOUNTS
    current = read_checkpoint(db)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_quote_control_pending'").fetchone():
        return current, None, None
    row = db.execute('SELECT plan,progress FROM paired_quote_control_pending WHERE id=1').fetchone()
    if row is None:
        return current, None, None
    pending, progress = validate_checkpoint(state.unpack(row[0])), state.unpack(row[1])
    if (pending['previous_checkpoint_sha256'] != _hash(current) or
            pending['from_ordinal'] != (1 if current is None else current['through_ordinal']+1) or
            set(progress) != {'accounts'} or type(progress['accounts']) is not dict or
            set(progress['accounts']) != ACCOUNTS or any(type(done) is not bool for done in progress['accounts'].values())):
        raise ValueError('quote-checkpoint pending predecessor/progress differs')
    return current, pending, progress


def local_receipt(checkpoint, name):
    account = checkpoint['accounts'][name]
    return dict(schema_version=1, purpose=PURPOSE, checkpoint_sha256=_hash(checkpoint),
                account=name, account_sha256=_hash(account), payload=account,
                through_ordinal=checkpoint['through_ordinal'])


def read_local(db):
    from hermes_trading import archive_quote_state as state
    row = db.execute('SELECT status,reason FROM incidents WHERE incident_id=?', (INCIDENT,)).fetchone()
    if row is None:
        return None
    if not isinstance(row[1], str) or len(row[1].encode()) > MAX_CHECKPOINT_BYTES:
        raise ValueError('quote-checkpoint local byte bound differs')
    value = state.unpack(row[1])
    if (row[0] != 'resolved' or set(value) != {'schema_version', 'purpose', 'checkpoint_sha256',
            'account', 'account_sha256', 'payload', 'through_ordinal'} or
            type(value['schema_version']) is not int or value['schema_version'] != 1 or value['purpose'] != PURPOSE or
            _hash(validate_account(value['payload'])) != value['account_sha256']):
        raise ValueError('quote-checkpoint local receipt differs')
    _digest(value['checkpoint_sha256']); ordinal(value['through_ordinal'])
    return value


def account_authority(db, name, current, pending, progress, check=lambda: None):
    local = read_local(db)
    for candidate in (pending, current):
        if candidate is not None and local == local_receipt(candidate, name):
            if pending is not None and candidate is current and progress['accounts'][name]:
                raise ValueError('quote-checkpoint acknowledged account has old authority')
            assert_terminal_summary(db, candidate['accounts'][name]['terminal'], check)
            return candidate
    if local is None and current is None and (pending is None or not progress['accounts'][name]):
        return None
    raise ValueError('quote-checkpoint account has no matching shared authority')
