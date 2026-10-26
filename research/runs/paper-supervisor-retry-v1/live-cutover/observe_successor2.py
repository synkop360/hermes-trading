import sys;sys.dont_write_bytecode=True
"""Bounded, read-only native reporting acceptance facts; stdout is sanitized JSON.

No environment/credential reads, network, sender construction, DB initialization,
migration, financial replay or state writes. Run only as an operator observation.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import zlib

ACCOUNTS = tuple(f'{arm}_{size}' for arm in ('adaptive', 'control') for size in (50, 100, 150, 200))
COUNTERS = ('coalesced', 'dropped', 'invalid', 'oversized', 'delivery_unknown', 'gaps',
            'repeated_states', 'omitted_events', 'restarts')
EXPERIMENT = 'paper-beta-v1-20261021-6c9d520'
OPENING_HASH = '50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349'
OPENING_AT = '2026-10-23T20:13:56.622959+00:00'
MANIFEST_RAW_HASH = '43ec91c55d05f6f04f7a227dd9adc23a2283dd0d788a2255903cb63ee60cbb64'
MAX_RECORD = 512 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def bounded_json(path, maximum):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('bounded_metadata_exceeded')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('metadata_object_required')
    return value, raw


def code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) else None


def number(value):
    return value if type(value) is int and 0 <= value <= 10**15 else None


def stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.isoformat() if result.tzinfo is not None else None
    except ValueError:
        return None


def money(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        return value if Decimal(value).is_finite() else None
    except InvalidOperation:
        return None


def hash_value(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) else None


def batch_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f-]{36}', value) else None


def record(payload):
    """Read store_record's bounded inline format; never reconstruct shared packets."""
    if isinstance(payload, str):
        if len(payload.encode()) > MAX_RECORD:
            raise ValueError('record_bound')
        value = json.loads(payload)
    else:
        if not isinstance(payload, bytes) or not payload.startswith(b'PCP1') or len(payload) > MAX_RECORD:
            raise ValueError('record_format')
        decoder = zlib.decompressobj()
        raw = decoder.decompress(payload[4:], MAX_RECORD + 1)
        if len(raw) > MAX_RECORD or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('record_inflate_bound')
        envelope = json.loads(raw)
        if set(envelope) != {'schema_version', 'inline', 'refs', 'input_hash'} or envelope['schema_version'] != 1 or envelope['refs']:
            raise ValueError('inline_record_required')
        value = envelope['inline']
        if canonical(value) != envelope['input_hash']:
            raise ValueError('record_hash')
    if not isinstance(value, dict):
        raise ValueError('record_object_required')
    return value


def journal(root):
    path = root / 'adaptive_200/paper_publications.sqlite'
    # Exactly one bounded connection. Never immutable=1 against running state.
    with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
        deadline = time.monotonic() + .2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        counts = dict(db.execute('SELECT status,COUNT(*) FROM paired_batches GROUP BY status'))
        opening_row = db.execute('SELECT CASE WHEN length(payload)<=? THEN payload END,receipt_hash FROM paired_inception WHERE id=1', (MAX_RECORD,)).fetchone()
        opening = record(opening_row[0])
        if canonical(opening) != opening_row[1] or opening_row[1] != OPENING_HASH or opening.get('at') != OPENING_AT:
            raise ValueError('opening_hash_mismatch')
        latest = db.execute("SELECT batch_id,input_hash,CASE WHEN length(completed)<=? THEN completed END FROM paired_batches WHERE status='complete' ORDER BY rowid DESC LIMIT 1", (MAX_RECORD,)).fetchone()
        if latest is None:
            raise ValueError('no_complete_batch')
        completed = record(latest[2])
        settlement = db.execute('SELECT CASE WHEN length(payload)<=? THEN payload END FROM paired_settlements WHERE batch_id=?', (MAX_RECORD, latest[0])).fetchone()
        measurements = record(settlement[0])
        return dict(status='observed', completed_count=number(counts.get('complete', 0)),
                    pending_count=number(counts.get('pending', 0)), batch_id=batch_id(latest[0]),
                    input_hash=hash_value(latest[1]), opening_hash=opening_row[1],
                    opening_at=stamp(opening.get('at')), finalized_all_eight=set(completed) == set(ACCOUNTS) and all(completed[n].get('finalized') is True for n in ACCOUNTS)), opening, measurements


def inspect(root, baseline_complete, summary_every):
    result = dict(kind='read_only_reporting_acceptance', observed_at=datetime.now(timezone.utc).isoformat(),
                  baseline_complete=baseline_complete, expected_summary_every_new_batches=summary_every,
                  profitability_qualified=False)
    manifest, raw = bounded_json(root / 'manifest.json', 64 * 1024)
    heartbeat, heartbeat_raw = bounded_json(root / 'adaptive_200/heartbeat.json', MAX_RECORD)
    safety, _ = bounded_json(root / 'paper_safety_state.json', 64 * 1024)
    lifecycle = heartbeat.get('worker_lifecycle') or {}
    release = heartbeat.get('worker_release') or {}
    result['heartbeat'] = dict(timestamp=stamp(heartbeat.get('timestamp')), observed_at=stamp(heartbeat.get('observed_at')),
        status=code(heartbeat.get('status')), paper_mode=heartbeat.get('mode') == 'paper',
        simulated=heartbeat.get('execution') == 'simulated', error_present=bool(heartbeat.get('error')),
        batch_id=batch_id(heartbeat.get('batch_id')), input_hash=hash_value(heartbeat.get('input_hash')),
        completed_batches=number(lifecycle.get('completed_batches')), primary_wallet_eur=number(release.get('primary_wallet_eur')),
        account_names_match=set(heartbeat.get('accounts') or {}) == set(ACCOUNTS))
    admission = heartbeat.get('entry_admission') or {}
    result['entry_admission'] = dict(status=code(admission.get('status')), blocked=admission.get('blocked') if type(admission.get('blocked')) is bool else None,
        reasons=[code(x) for x in admission.get('reasons', [])[:32] if code(x)])
    supervisor = safety.get('supervisor') or {}
    result['supervisor_safety'] = dict(entry_halt=supervisor.get('entry_halt') if type(supervisor.get('entry_halt')) is bool else None,
        consecutive_errors=number(supervisor.get('consecutive_errors')), success=supervisor.get('success') if type(supervisor.get('success')) is bool else None,
        last_seen=stamp(supervisor.get('last_seen')), error_kind=code(supervisor.get('error_kind')))
    expected_accounts = {}
    for name in ACCOUNTS:
        account, _ = bounded_json(root / name / 'wallet_account.json', 32 * 1024)
        expected_accounts[name] = canonical(account)
    opening, measurements = None, {}
    try:
        result['journal'], opening, measurements = journal(root)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        result['journal'] = dict(status='unavailable', error_type=type(error).__name__, sqlite_errorname=getattr(error, 'sqlite_errorname', None))
    accounts = {}
    for name in ACCOUNTS:
        try:
            report, _ = bounded_json(root / name / 'wallet_metrics.json', MAX_RECORD)
            obs = report.get('observation') or {}
            provenance = obs.get('cost_provenance') or {}
            quality = {}
            for category in ('hosting', 'ai', 'tools', 'funding'):
                source = provenance.get(category) or {}
                q = source.get('quality')
                quality[category] = 'missing' if source.get('amount') is None else q if q in ('actual', 'estimated', 'missing') else 'unknown'
            settled_obs = (measurements.get(name) or {}).get('observation')
            accounts[name] = dict(status=code(report.get('status')), currency=obs.get('currency') if obs.get('currency') == 'EUR' else None,
                observed_at=stamp(obs.get('at')), account_hash=hash_value(obs.get('account_config_hash')),
                account_hash_matches=obs.get('account_config_hash') == expected_accounts[name],
                common_heartbeat_time=obs.get('at') == heartbeat.get('observed_at'),
                matches_latest_settlement=obs == settled_obs if settled_obs is not None else None,
                net_equity=money(obs.get('net_equity')), closed_net_pnl=money(obs.get('closed_net_pnl')),
                known_accrued_costs=money(obs.get('known_accrued_costs')), cost_quality=quality,
                missing_field_count=len(obs.get('missing', [])) if isinstance(obs.get('missing'), list) else None)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            accounts[name] = dict(status='unavailable', error_type=type(error).__name__)
    result['accounts'] = accounts
    result['comparisons_eur'] = {}
    for size in (50, 100, 150, 200):
        a, c = (accounts[f'{arm}_{size}'].get('net_equity') for arm in ('adaptive', 'control'))
        result['comparisons_eur'][str(size)] = str(Decimal(a) - Decimal(c)) if a is not None and c is not None else None
    expected_identity = dict(experiment_id=EXPERIMENT, manifest_hash=canonical(manifest), opening_hash=OPENING_HASH,
        opening_at=OPENING_AT, primary_wallet_eur=200, account_hashes=expected_accounts)
    result['identity'] = dict(experiment_id=EXPERIMENT, manifest_raw_hash_matches=digest(raw) == MANIFEST_RAW_HASH,
        manifest_hash=expected_identity['manifest_hash'], opening_hash=OPENING_HASH,
        opening_verified=result['journal'].get('opening_hash') == OPENING_HASH)
    try:
        ledger, _ = bounded_json(root / 'paper_discord_reporter.json', 64 * 1024)
        valid = ledger.get('schema_version') == 1 and set(ledger.get('identity') or {}) == set(expected_identity)
        observed_identity = ledger.get('identity') or {}
        valid = valid and all(observed_identity.get(k) == v for k, v in expected_identity.items())
        markers = ledger.get('delivered')
        valid = valid and isinstance(markers, list) and len(markers) <= 256 and all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{24}:[0-3]', x) for x in markers)
        counters = ledger.get('counters') or {}
        valid = valid and set(counters) == set(COUNTERS) and all(number(v) is not None for v in counters.values())
        cadence = ledger.get('cadences') or {}
        cycle = cadence.get('cycle', '0')
        if not isinstance(cycle, str) or not cycle.isdecimal() or len(cycle) > 20:
            raise ValueError('invalid_cycle_cadence')
        if not valid:
            raise ValueError('invalid_reporting_metadata')
        safe_cadences = {}
        for key, value in cadence.items():
            if key in ('cycle', 'heartbeat') and isinstance(value, str) and value.isdecimal() and len(value) <= 20:
                safe_cadences[key] = value
            elif isinstance(key, str) and re.fullmatch(r'(?:operational|financial):[1-9]\d*(?:h|d|w|mo)', key) and stamp(value):
                safe_cadences[key] = stamp(value)
            else:
                raise ValueError('invalid_cadence_metadata')
        groups = {}
        for marker in markers:
            tag, part = marker.split(':'); groups.setdefault(tag, []).append(int(part) + 1)
        new_batches = number(ledger.get('new_batches'))
        last_completed = number(ledger.get('last_completed'))
        pending = ledger.get('pending')
        if pending is not None and (not isinstance(pending, str) or not re.fullmatch(r'[0-9a-f]{24}:[0-3]', pending)):
            raise ValueError('invalid_pending_marker')
        result['reporter'] = dict(status='metadata_observed', schema_version=1, identity_matches=True,
            opening_at=stamp(observed_identity.get('opening_at')), startup_confirmed=ledger.get('startup') is True,
            new_batches=new_batches, last_completed=last_completed, last_observed_at=stamp(ledger.get('last_observed_at')),
            confirmed_part_ids=markers, confirmed_report_parts=groups, pending_part_id=pending,
            abandoned_intent_count=len(ledger.get('abandoned', [])), confirmed_event_marker_count=len(ledger.get('events', [])),
            counters={k: number(counters.get(k)) for k in COUNTERS}, cadences=safe_cadences, confirmed_cycle_windows=int(cycle),
            cycle_summary_confirmation_observed=ledger.get('startup') is True and new_batches is not None and new_batches >= summary_every and int(cycle) >= 1)
    except FileNotFoundError:
        result['reporter'] = dict(status='metadata_absent', startup_confirmed=False, cycle_summary_confirmation_observed=False)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        result['reporter'] = dict(status='metadata_unavailable_or_invalid', error_type=type(error).__name__)
    _, heartbeat_after = bounded_json(root / 'adaptive_200/heartbeat.json', MAX_RECORD)
    current_count = result['heartbeat']['completed_batches']
    result['cohort'] = dict(heartbeat_stable_during_read=heartbeat_after == heartbeat_raw,
        all_eight_account_hashes_match=all(x.get('account_hash_matches') is True for x in accounts.values()),
        all_eight_common_observation_time=all(x.get('common_heartbeat_time') is True for x in accounts.values()),
        all_eight_match_latest_settlement=all(x.get('matches_latest_settlement') is True for x in accounts.values()),
        current_journal_matches_heartbeat=result['journal'].get('batch_id') == result['heartbeat']['batch_id'] and result['journal'].get('input_hash') == result['heartbeat']['input_hash'],
        new_completed_since_closed_baseline=current_count - baseline_complete if current_count is not None else None)
    result['limitations'] = ['One observation, not a consistent backup or continuity proof.',
        'The ledger retains locally confirmed part IDs, not Discord message bodies or remote message IDs.',
        'Startup may precede the first new financial envelope; current wallet rows do not prove a historical message body.',
        'Cycle cadence advances only after confirmed report success; runtime sender status and configured cadence are not persisted.',
        'No retry is made for a locked journal; unavailable/racing source evidence remains unavailable.']
    return result



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
    if _hash(runtime) != '38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2':
        raise ValueError('unexpected_runtime')
    result = inspect(root, 2711, 5)
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
