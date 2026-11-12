"""Original closed-history union and read-only controller reconciliation.

No watermark is synthesized, no review is dispatched, and no strategy changes.
Callers must authenticate/verify the snapshots containing these original rows.
"""
import json
from hermes_trading.operational_exits import RECOVERY_EXIT_REASONS

from hermes_trading.global_reflection import _recent_trades
from hermes_trading.persistence.artifacts import _trade_payload
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _json, _hash, _time, _now


def _identity(row):
    if not isinstance(row, dict) or row.get('status', 'closed') != 'closed':
        raise ValueError('only closed-trade evidence can be merged')
    identity = row.get('position_id') or row.get('trade_id') or row.get('id')
    asset = row.get('asset')
    if not isinstance(identity, str) or not identity or not isinstance(asset, str) or not asset:
        raise ValueError('closed trade identity is missing')
    return _json([asset, identity])


def _closed(row):
    return _time(row.get('closed_at') or row.get('timestamp'))


def _recovery(row):
    return (row.get('evidence_classification') in {'operational_recovery', 'operational_risk_stop'} or
            row.get('exit_reason') in RECOVERY_EXIT_REASONS)


def merge_closed_rows(archived, active):
    """Keep every original ID and recovery flag; exact duplicates are harmless."""
    found = {}
    for rows in (archived, active):
        for row in rows:
            key = _identity(row); closed = _closed(row); canonical = _json(row)
            if key in found and found[key][1] != canonical:
                raise ValueError('conflicting original closed trade evidence')
            found[key] = (closed, canonical)
    return [json.loads(value[1]) for _, value in sorted(found.items(), key=lambda item: (item[1][0], item[0]))]


def canonical_closed_rows(db, *, check=lambda: None):
    """Read original positions plus immutable exit provenance, without writes."""
    repository = StateRepository(db); rows = []
    for position in repository.closed_positions():
        check(); decision = repository.closed_position_decision(position)
        if decision is None:
            raise ValueError('original closed position exit provenance is missing')
        rows.append(_trade_payload(position, decision))
    return merge_closed_rows([], rows)


def reconcile_mirror(mirror_rows, canonical_rows):
    """A stale mirror may omit later closes; it may not invent or alter one."""
    original = merge_closed_rows([], canonical_rows)
    if _hash(merge_closed_rows(original, mirror_rows)) != _hash(original):
        raise ValueError('controller mirror contains closes ahead of original worker evidence')


def reconcile_publications(reviews, publications):
    """Link a validated worker timeline to the original controller audit.

    A worker commit with a lost reply may still be pending on the controller;
    its exact original intent must remain available for idempotent completion.
    """
    by_id = {review['review_id']: review for review in reviews}; published = set()
    for request, receipt in publications:
        identity = request['review_id']; review = by_id.get(identity)
        if identity in published or review is None or review.get('outcome') not in {'pending', 'changed'}:
            raise ValueError('worker publication has no original controller review')
        published.add(identity)
        if _hash(review.get('publication_intent')) != _hash(request):
            raise ValueError('worker publication differs from original controller intent')
        original = request['review']
        for key, value in original.items():
            observed = 'pending' if key == 'outcome' else review.get(key)
            if _hash(observed) != _hash(value):
                raise ValueError('worker publication differs from original controller review material')
        if (receipt.get('review_id') != identity or receipt.get('status') != 'published' or
                (review['outcome'] == 'changed' and _hash(review.get('publication')) != _hash(receipt))):
            raise ValueError('worker publication receipt differs from controller completion')
    if any(review.get('outcome') == 'changed' and review['review_id'] not in published for review in reviews):
        raise ValueError('completed controller change is missing its worker publication')


def reconcile_reviews(state, reviews, closed_rows, *, policy, now=None, check=lambda: None):
    """Reject missing/ahead/conflicting worker evidence without changing state.

    The all-history watermark and unresolved IDs remain independent of the
    latest25 prompt context. Failed/deferred attempts never consume trade IDs.
    """
    current = _now(now)
    if (not isinstance(state, dict) or type(state.get('schema_version')) is not int or
            state['schema_version'] != 1 or state.get('experiment_id') != policy.get('experiment_id') or
            state.get('policy_hash') != _hash(policy)):
        raise ValueError('controller review policy/state binding differs')
    activated = _time(state['activated_at'])
    if activated > current:
        raise ValueError('controller activation is future-dated')
    rows = merge_closed_rows([], closed_rows)
    original = {_identity(row): row for row in rows}
    for row in rows:
        check()
        if _closed(row) > current:
            raise ValueError('original closed history exceeds snapshot time')
    consumed = set(); seen = set(); pending = []; pending_ids = set()
    if not isinstance(reviews, list):
        raise ValueError('controller review audit must be complete')
    for review in reviews:
        check()
        if not isinstance(review, dict) or not isinstance(review.get('review_id'), str) or not review['review_id']:
            raise ValueError('controller review identity missing')
        if review['review_id'] in seen:
            raise ValueError('duplicate controller review identity')
        seen.add(review['review_id'])
        outcome = review.get('outcome')
        if outcome not in {'pending', 'changed', 'no_change', 'rejected', 'failed', 'deferred'}:
            raise ValueError('controller review outcome differs')
        started = _time(review['started_at'])
        if not activated <= started <= current:
            raise ValueError('controller review time differs from experiment')
        snapshot = review.get('trade_snapshot'); identities = review.get('trade_ids')
        if (not isinstance(snapshot, list) or not isinstance(identities, list) or
                len(snapshot) < policy['cadence_closed_trades'] or
                _hash(snapshot) != review.get('snapshot_hash')):
            raise ValueError('controller review original snapshot hash differs')
        normalized = merge_closed_rows([], snapshot)
        keys = [_identity(row) for row in normalized]
        if _hash(normalized) != _hash(snapshot) or keys != identities:
            raise ValueError('controller review original snapshot identities differ')
        for row, key in zip(normalized, keys):
            check()
            if key not in original or _hash(row) != _hash(original[key]):
                raise ValueError('controller review original closed evidence missing or changed')
            if not activated < _closed(row) <= started or _recovery(row):
                raise ValueError('controller review original close time/recovery classification differs')
        if outcome == 'pending':
            pending.append(review); pending_ids.update(keys)
        else:
            completed = _time(review['completed_at'])
            if not started <= completed <= current:
                raise ValueError('controller review completion time differs')
            if outcome in {'changed', 'no_change', 'rejected'}:
                if (review.get('provider'), review.get('model')) != (policy['model']['provider'], policy['model']['name']):
                    raise ValueError('completed controller review model differs')
                if consumed.intersection(keys):
                    raise ValueError('controller review trade IDs were consumed more than once')
                consumed.update(keys)
    pointer = state.get('pending_review')
    if _hash(pending) != _hash([] if pointer is None else [pointer]):
        raise ValueError('controller pending review audit differs')
    watermark = state.get('completed_trade_ids')
    if not isinstance(watermark, list) or _hash(watermark) != _hash(sorted(consumed)):
        raise ValueError('controller consumed watermark differs from complete review audit')
    if consumed.intersection(pending_ids):
        raise ValueError('pending controller review includes consumed trade IDs')
    unseen = [row for row in rows if activated < _closed(row) <= current and _identity(row) not in consumed]
    return {'schema_version': 1, 'purpose': 'paired_review_history_reconciliation_v1',
        'closed_history_sha256': _hash(rows), 'supervisor_state_sha256': _hash(state),
        'review_audit_sha256': _hash(reviews), 'consumed_trade_ids': sorted(consumed),
        'pending_review_id': None if pointer is None else pointer['review_id'],
        'unreviewed_trade_ids': [_identity(row) for row in unseen if not _recovery(row)],
        'recovery_trade_ids': [_identity(row) for row in unseen if _recovery(row)],
        'recent_trades': _recent_trades(rows, current)}
