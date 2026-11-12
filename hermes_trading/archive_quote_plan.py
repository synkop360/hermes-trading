"""Byte admission for a newly eligible quote-retention plan.

Only the new-plan writer uses this selector. Existing or pending plans retain
their original identity and contents. Eligibility and immutable bundle checks
remain the caller's responsibility; this module performs no storage writes.
"""
from copy import deepcopy

from hermes_trading import archive_journal as journal, archive_quote_state as state
from hermes_trading.reflection_supervision import _hash, _json

# The schema ceiling stays separate from the caller's current chunk allowance.
_SCHEMA_MAX_ITEMS = state.MAX_ITEMS


def _count(plan, check):
    check()
    if (type(plan) is not dict or type(plan.get('accounts')) is not dict or
            set(plan['accounts']) != journal.ACCOUNTS):
        raise ValueError('quote retention prefix requires all eight accounts')
    total = 0
    for account in plan['accounts'].values():
        check()
        if (type(account) is not dict or set(account) != {'directory', 'items'} or
                not isinstance(account['directory'], str) or type(account['items']) is not list):
            raise ValueError('quote retention prefix account differs')
        total += len(account['items'])
        if total > _SCHEMA_MAX_ITEMS:
            raise ValueError('quote retention eligible item ceiling exceeded')
        for item in account['items']:
            check()
            if (type(item) is not dict or
                    set(item) != {'decision_id', 'asset', 'timeframe', 'at', 'bundle_sha256'} or
                    any(not isinstance(value, str) or not value for value in item.values())):
                raise ValueError('quote retention prefix requires validated native items')
    return total


def reservation_bytes(plan, *, check=lambda: None):
    """Keep the writer's conservative allowance, with one complete-plan hash."""
    check()
    digest = _hash(plan)
    encoded = _json(dict(payload=plan, sha256=digest))
    check()
    progress = dict(status='pending', accounts={
        name: dict(done=False, stage=None, orphans=[]) for name in journal.ACCOUNTS})
    added = len(encoded.encode())+len(state.envelope(progress).encode())
    for name, account in plan['accounts'].items():
        check()
        receipt = dict(schema_version=1, purpose=state.PURPOSE,
            operation_id=plan['operation_id'], plan_sha256=digest,
            account=name, account_sha256=_hash(account))
        added += len(state.envelope(receipt).encode())+512
        for item in account['items']:
            check()
            floor = dict(schema_version=1, purpose=state.PURPOSE,
                asset=item['asset'], timeframe=item['timeframe'], through=item['at'],
                operation_id=plan['operation_id'], plan_sha256=digest)
            added += len(state.envelope(floor).encode())+512
    check()
    return added


def _prefix(plan, count, check):
    result = deepcopy({key: value for key, value in plan.items() if key != 'accounts'})
    accounts = {}
    left = count
    # _new_plan supplies numeric wallet order. Canonical stored JSON may have
    # another key order, so callers must never reselect an existing plan.
    for name, account in plan['accounts'].items():
        check()
        items = []
        take = min(left, len(account['items']))
        for item in account['items'][:take]:
            check()
            items.append(deepcopy(item))
        accounts[name] = dict(directory=account['directory'], items=items)
        left -= take
    if left:
        raise ValueError('quote retention prefix exceeds eligible input')
    result['accounts'] = accounts
    return result


def select_prefix(plan, *, existing_bytes, max_bytes, max_items, check=lambda: None):
    """Return the largest fitting new prefix, or None when no items qualify.

    Every extra item increases the conservative reservation; all hashes have
    fixed encoded lengths. Binary search therefore preserves maximal progress
    under the unchanged byte and count limits. The callback enforces the
    original absolute deadline, including the last action before each return.
    """
    check()
    if (type(existing_bytes) is not int or existing_bytes < 0 or
            type(max_bytes) is not int or max_bytes < 0 or
            type(max_items) is not int or not 1 <= max_items <= _SCHEMA_MAX_ITEMS):
        raise ValueError('quote retention prefix allowance differs')
    total = _count(plan, check)
    if not total:
        check()
        return None
    upper = min(total, max_items)
    candidate = _prefix(plan, upper, check)
    if existing_bytes+reservation_bytes(candidate, check=check) <= max_bytes:
        check()
        return candidate
    low, high = 1, upper-1
    best = None
    while low <= high:
        check()
        middle = (low+high)//2
        candidate = _prefix(plan, middle, check)
        if existing_bytes+reservation_bytes(candidate, check=check) <= max_bytes:
            best = candidate
            low = middle+1
        else:
            high = middle-1
    check()
    if best is None:
        raise ValueError('quote retention detailed control byte bound requires checkpoint')
    return best
