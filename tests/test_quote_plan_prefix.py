"""New-plan metadata prefix selection against the native envelope contract."""
from copy import deepcopy
import importlib
import importlib.util
import inspect
import unittest
from unittest.mock import patch

from hermes_trading import archive_journal as journal, archive_quote_state as state
from hermes_trading.reflection_supervision import _hash

NAMES = ('adaptive_50', 'adaptive_100', 'adaptive_150', 'adaptive_200',
         'control_50', 'control_100', 'control_150', 'control_200')


def make_plan(counts, *, unicode=False, order=NAMES):
    accounts = {}
    ordinal = 0
    for name, count in zip(order, counts):
        items = []
        for index in range(count):
            items.append(dict(decision_id=f'{ordinal+index:064x}',
                asset='ÉTH/€' if unicode else 'ETH/EUR',
                timeframe='paper-position:'+('東京-' if unicode else '')+'e'*32,
                at='2026-10-19T00:00:00+00:00', bundle_sha256='f'*64))
        ordinal += count
        accounts[name] = dict(directory=name, items=items)
    return dict(schema_version=1, purpose=state.PURPOSE, operation_id='a'*32,
        archive_operation_id='b'*32, archive_sha256='c'*64, cohort_sha256='d'*64,
        cut_at='2026-10-19T00:00:00+00:00', accounts=accounts)


def native_reservation(plan):
    """Independent format oracle; no dependency on the prospective helper."""
    progress = dict(status='pending', accounts={name: dict(done=False, stage=None, orphans=[])
                                               for name in journal.ACCOUNTS})
    added = len(state.envelope(plan).encode())+len(state.envelope(progress).encode())
    plan_hash = _hash(plan)
    for name, account in plan['accounts'].items():
        added += len(state.envelope(state.receipt(plan, name)).encode())+512
        for item in account['items']:
            floor = dict(schema_version=1, purpose=state.PURPOSE,
                asset=item['asset'], timeframe=item['timeframe'], through=item['at'],
                operation_id=plan['operation_id'], plan_sha256=plan_hash)
            added += len(state.envelope(floor).encode())+512
    return added


def original_prefix(plan, count):
    value = deepcopy(plan)
    left = count
    for account in value['accounts'].values():
        source = account['items']
        account['items'] = source[:left]
        left = max(0, left-len(source))
    if left:
        raise AssertionError('oracle prefix exceeds eligible input')
    return value


def linear_oracle(plan, *, existing_bytes, max_bytes, max_items):
    count = min(sum(len(account['items']) for account in plan['accounts'].values()), max_items)
    fitting = None
    for size in range(1, count+1):
        candidate = original_prefix(plan, size)
        if existing_bytes+native_reservation(candidate) <= max_bytes:
            fitting = candidate
    return fitting


class QuotePlanPrefixTests(unittest.TestCase):
    def module(self):
        name = 'hermes_trading.archive_quote_plan'
        self.assertIsNotNone(importlib.util.find_spec(name), 'production quote-plan byte selector is not implemented')
        module = importlib.import_module(name)
        self.assertTrue(callable(getattr(module, 'reservation_bytes', None)))
        self.assertTrue(callable(getattr(module, 'select_prefix', None)))
        return module

    def test_helper_requires_explicit_count_allowance(self):
        module = self.module()
        signature = inspect.signature(module.select_prefix)
        self.assertEqual(signature.parameters['max_items'].default, inspect.Parameter.empty)
        self.assertEqual(signature.parameters['max_items'].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_reservation_matches_native_envelopes_including_unicode_and_empty_accounts(self):
        module = self.module()
        for counts, unicode in (((0,)*8, False), ((1, 0, 2, 0, 0, 1, 0, 0), False),
                                ((0, 1, 0, 0, 2, 0, 1, 0), True)):
            with self.subTest(counts=counts, unicode=unicode):
                plan = make_plan(counts, unicode=unicode)
                original = deepcopy(plan)
                self.assertEqual(module.reservation_bytes(plan), native_reservation(plan))
                self.assertEqual(plan, original)

    def test_largest_fitting_prefix_matches_independent_linear_oracle(self):
        module = self.module()
        cases = (((3, 0, 2, 0, 1, 0, 0, 1), False, NAMES),
                 ((0, 2, 0, 1, 0, 0, 3, 0), True, NAMES[3:]+NAMES[:3]),
                 ((0, 0, 0, 0, 0, 0, 0, 4), False, NAMES))
        for counts, unicode, order in cases:
            plan = make_plan(counts, unicode=unicode, order=order)
            original = deepcopy(plan)
            total = sum(counts)
            for size in range(1, total+1):
                boundary = native_reservation(original_prefix(plan, size))
                for adjustment in (-1, 0, 1):
                    for count_limit in (1, 3, state.MAX_ITEMS):
                        with self.subTest(counts=counts, unicode=unicode, size=size,
                                          adjustment=adjustment, count_limit=count_limit):
                            options = dict(existing_bytes=173, max_bytes=173+boundary+adjustment,
                                           max_items=count_limit)
                            expected = linear_oracle(plan, **options)
                            if expected is None:
                                with self.assertRaises(ValueError):
                                    module.select_prefix(plan, **options)
                            else:
                                actual = module.select_prefix(plan, **options)
                                self.assertEqual(actual, expected)
                                self.assertEqual(list(actual['accounts']), list(plan['accounts']))
                                self.assertEqual(set(actual['accounts']), journal.ACCOUNTS)
                            self.assertEqual(plan, original)

    def test_fitting_plan_keeps_identity_and_output_does_not_alias_input(self):
        module = self.module()
        plan = make_plan((1, 0, 2, 0, 0, 0, 0, 1))
        original = deepcopy(plan)
        result = module.select_prefix(plan, existing_bytes=0,
            max_bytes=native_reservation(plan), max_items=state.MAX_ITEMS)
        self.assertEqual(result, original)
        result['accounts']['adaptive_50']['items'][0]['asset'] = 'changed'
        result['accounts']['control_200']['directory'] = 'changed'
        self.assertEqual(plan, original)

    def test_zero_items_is_nondurable_and_exhausted_tail_rejects_without_mutation(self):
        module = self.module()
        empty = make_plan((0,)*8)
        original_empty = deepcopy(empty)
        self.assertIsNone(module.select_prefix(empty, existing_bytes=7, max_bytes=7, max_items=state.MAX_ITEMS))
        self.assertEqual(empty, original_empty)
        plan = make_plan((0, 0, 0, 0, 0, 1, 0, 0))
        original = deepcopy(plan)
        reservation = native_reservation(plan)
        with self.assertRaises(ValueError):
            module.select_prefix(plan, existing_bytes=300, max_bytes=300+reservation-1,
                                 max_items=state.MAX_ITEMS)
        self.assertEqual(plan, original)
        self.assertEqual(module.select_prefix(plan, existing_bytes=300,
            max_bytes=300+reservation, max_items=state.MAX_ITEMS), original)

    def test_explicit_dynamic_count_ceiling_applies_before_byte_headroom(self):
        module = self.module()
        plan = make_plan((4, 0, 0, 0, 0, 2, 0, 0))
        original = deepcopy(plan)
        with patch.object(state, 'MAX_ITEMS', 2):
            selected = module.select_prefix(plan, existing_bytes=0,
                max_bytes=native_reservation(plan)+100000, max_items=state.MAX_ITEMS)
        self.assertEqual(selected, original_prefix(original, 2))
        self.assertEqual(plan, original)

    def test_absolute_deadline_checks_cover_loops_and_last_action_before_return(self):
        module = self.module()
        bounded = make_plan((5, 0, 0, 0, 0, 2, 0, 0), unicode=True)
        fitting = make_plan((2, 0, 0, 1, 0, 0, 0, 0))
        empty = make_plan((0,)*8)
        calls = [
            (module.reservation_bytes, bounded, {}),
            (module.select_prefix, bounded, dict(existing_bytes=53,
                max_bytes=53+native_reservation(original_prefix(bounded, 2)), max_items=state.MAX_ITEMS)),
            (module.select_prefix, fitting, dict(existing_bytes=0,
                max_bytes=native_reservation(fitting), max_items=state.MAX_ITEMS)),
            (module.select_prefix, empty, dict(existing_bytes=0, max_bytes=1, max_items=state.MAX_ITEMS)),
        ]
        for function, plan, options in calls:
            checks = []
            function(plan, check=lambda: checks.append(True), **options)
            self.assertGreater(len(checks), 1)
            for expires_at in sorted({1, max(1, len(checks)//2), len(checks)}):
                observed = []
                def deadline():
                    observed.append(True)
                    if len(observed) >= expires_at:
                        raise TimeoutError('invented original absolute deadline expired')
                original = deepcopy(plan)
                with self.subTest(function=function.__name__, expires_at=expires_at), self.assertRaises(TimeoutError):
                    function(plan, check=deadline, **options)
                self.assertEqual(plan, original)

    def test_4096_item_prefix_is_bounded_and_full_plan_hash_is_cached(self):
        module = self.module()
        plan = make_plan((4096, 0, 0, 0, 0, 0, 0, 0))
        original = deepcopy(plan)
        hashes = []
        native_hash = _hash
        def track_hash(value):
            if type(value) is dict and 'accounts' in value and value.get('purpose') == state.PURPOSE:
                hashes.append(sum(len(account['items']) for account in value['accounts'].values()))
            return native_hash(value)
        with patch.object(module, '_hash', side_effect=track_hash, create=True), \
             patch.object(state, '_hash', side_effect=track_hash):
            module.reservation_bytes(plan)
            self.assertEqual(len(hashes), 1, 'one reservation must reuse one complete-plan digest')
            hashes.clear()
            checks = []
            selected = module.select_prefix(plan, existing_bytes=0, max_bytes=3*1024**2,
                max_items=state.MAX_ITEMS, check=lambda: checks.append(True))
        count = sum(len(account['items']) for account in selected['accounts'].values())
        self.assertGreater(count, 0)
        self.assertLess(count, 4096)
        self.assertGreater(len(checks), 4096)
        self.assertLessEqual(len(hashes), 14, 'selection must avoid a full-plan hash per floor item')
        self.assertLessEqual(native_reservation(selected), 3*1024**2)
        self.assertGreater(native_reservation(original_prefix(plan, count+1)), 3*1024**2)
        self.assertEqual(selected, original_prefix(original, count))
        self.assertEqual(plan, original)


if __name__ == '__main__':
    unittest.main()
