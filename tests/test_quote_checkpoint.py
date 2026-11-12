"""Native bounded all-eight quote-control checkpoint transitions."""
from contextlib import closing
from copy import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import asyncio
import hashlib
import importlib.util
import json
import secrets
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from hermes_trading import archive_journal as journal, archive_quote_state as state, archive_store as store
from hermes_trading.archive_capacity import CapacityExceeded, StorageDomain
from hermes_trading.archive_coordinator import capture
from hermes_trading.archive_quote_retention import _readonly, retain_quote_outcomes
from hermes_trading.archive_snapshot import snapshot
from hermes_trading.control_auth import sign_command
from hermes_trading.paired_worker import _stats
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash, _json
from venue_fixtures import venue_record
import test_paired_primary_archive as native


class QuoteCheckpointAvailabilityTests(unittest.TestCase):
    def test_checkpoint_coordinator_is_available(self):
        self.assertIsNotNone(importlib.util.find_spec('hermes_trading.archive_quote_checkpoint'),
                             'all-eight quote-control checkpoint coordinator is not implemented')


class QuoteCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('hermes_trading.archive_quote_checkpoint') is None:
            raise unittest.SkipTest('checkpoint API availability is checked separately')
        from hermes_trading.archive_quote_checkpoint import checkpoint_quote_controls
        from hermes_trading.archive_quote_checkpoint_state import operation_id

        cls.fixtures = unittest.TestCase()
        cls.addClassCleanup(cls.fixtures.doCleanups)

        async def build():
            f, selected = native.fresh_selected(cls.fixtures, 200, fees=True)
            # Configure the complete experiment before the first lifecycle is created.
            f.limits['max_cycles'] = 12
            (f.root / 'worker_limits.json').write_text(json.dumps(f.limits))
            f.storage['protective_stop_on_pressure'] = True
            (f.root / 'storage_limits.json').write_text(json.dumps(f.storage))

            async def collect(assets):
                quote = replace(f.snapshot, price=dict(f.snapshot.price, received_at=f.at.isoformat()),
                                venue_rules=venue_record(f.snapshot.asset, f.at))
                return {quote.asset: quote}

            f.market.collect.side_effect = collect

            async def advance_to(count):
                async def pause(seconds):
                    await f.sleep(seconds)
                    if _stats(f.primary)['complete'] == count:
                        raise RuntimeError('invented checkpoint completed-cut pause')
                with cls.fixtures.assertRaisesRegex(RuntimeError, 'completed-cut pause'):
                    await f.invoke(sleep=pause)

            await advance_to(5)
            pc = native.controller_for(cls.fixtures, f, selected)
            temporary = tempfile.TemporaryDirectory()
            cls.fixtures.addCleanup(temporary.cleanup)
            base = Path(temporary.name)
            archive = base / 'archive'
            domain = StorageDomain(base, domain_id='native-quote-checkpoint-fixture',
                                   limit_bytes=512 * 1024**2, reserve_bytes=1024**2,
                                   extra_roots=(f.root,), enabled=True)
            domain.initialize()

            def archived(label, through, first_batch, parent=None):
                cohort = base / label
                result = capture(pc, cohort, enabled=True,
                    worker_capture=lambda target, **limits: snapshot(f.root, target, enabled=True, **limits),
                    experiment_id='paired-test', expected_release=_release_hashes(), through_sequence=through,
                    max_bytes=64 * 1024**2, timeout_seconds=90, now=f.at)
                inventory = json.loads((cohort / 'snapshot_inventory.json').read_text())
                names = [row['path'] for row in inventory['files']] + ['snapshot_inventory.json']
                digest = store.seal(cohort, archive, names, experiment='paired-test',
                                    first_batch=first_batch, last_batch=result['certificate']['observed_complete'],
                                    parent=parent)
                certificate = result['certificate']
                shared = f.primary / 'paper_publications.sqlite'
                request = journal.prepare(shared, enabled=True, experiment_id='paired-test',
                    release_sha256=certificate['release_sha256'], archive_sha256=digest,
                    account_snapshots=certificate['account_snapshots'], copies={'pc': 'e'*64, 'backup': 'f'*64},
                    through_sequence=through)
                keys = {name: secrets.token_hex(32) for name in ('pc', 'backup')}
                body = _json(request)
                receipts = {name: dict(body=body, nonce=request['operation_id'], timestamp=f.at.isoformat(),
                    signature=sign_command(key, request['operation_id'], f.at, body)) for name, key in keys.items()}
                journal.acknowledge(shared, request['operation_id'], receipts, keys, now=f.at, enabled=True)
                journal.retire(shared, request['operation_id'], enabled=True)
                return request

            def retain(request, *, operation_id=None):
                with domain.ownership() as owner, patch.object(state, 'MAX_ITEMS', 2):
                    return retain_quote_outcomes(f.root, archive_root=archive,
                        archive_operation_id=request['operation_id'], operation_id=operation_id,
                        storage_owner=owner, cache_root=base, enabled=True,
                        max_bytes=64 * 1024**2, timeout_seconds=120, now=f.at)

            def checkpoint(request, through):
                with domain.ownership() as owner:
                    return checkpoint_quote_controls(f.root, archive_root=archive,
                        archive_operation_id=request['operation_id'], through_ordinal=through,
                        storage_owner=owner, cache_root=base, enabled=True,
                        max_bytes=64 * 1024**2, timeout_seconds=120, now=f.at)

            a = archived('cohort-a', 4, 1)
            q1 = retain(a)
            await advance_to(7)
            b = archived('cohort-b', 6, 6, a['archive_sha256'])
            q2 = retain(a, operation_id=secrets.token_hex(16))
            before_first = base / 'before-checkpoint-one'
            shutil.copytree(f.root, before_first)
            first_now = f.at
            first_finances = cls.finance_rows(f.root, f.manifest)
            cp1 = checkpoint(b, 1)
            cls.fixtures.assertEqual(first_finances, cls.finance_rows(f.root, f.manifest))
            cls.fixtures.assertEqual([row[0] for row in cls.control_rows(f.root)], [2])
            first_worker = base / 'after-checkpoint-one'
            shutil.copytree(f.root, first_worker)
            await advance_to(9)
            c = archived('cohort-c', 8, 8, b['archive_sha256'])
            before_third = base / 'before-third-retention'
            shutil.copytree(f.root, before_third)
            q3 = retain(a, operation_id=operation_id(_hash(f.manifest), 3))
            cls.fixtures.assertEqual([row[0] for row in cls.control_rows(f.root)], [2, 3])
            before_second = base / 'before-checkpoint-two'
            shutil.copytree(f.root, before_second)
            second_finances = cls.finance_rows(f.root, f.manifest)
            cp2 = checkpoint(c, 2)
            cls.fixtures.assertEqual(second_finances, cls.finance_rows(f.root, f.manifest))
            cls.fixtures.assertEqual([row[0] for row in cls.control_rows(f.root)], [3])
            cls.case = dict(worker=before_first, second_worker=before_second,
                first_worker=first_worker, final_worker=f.root, before_q3=before_third,
                archive=archive, manifest=f.manifest, a=a, b=b, c=c, q1=q1, q2=q2, q3=q3,
                cp1=cp1, cp2=cp2, first_now=first_now, now=f.at,
                first_finances=first_finances, second_finances=second_finances,
                fixture=f, supervisor=pc, original_a=base/'cohort-a/worker')

        asyncio.run(build())

    @staticmethod
    def control_rows(root):
        with closing(sqlite3.connect(root / 'adaptive_200/paper_publications.sqlite')) as db:
            return list(db.execute('SELECT rowid,operation_id,plan,progress FROM paired_quote_retention ORDER BY rowid'))

    @staticmethod
    def finance_rows(root, manifest):
        result = {}
        for profile in manifest['profiles']:
            with closing(sqlite3.connect(root / profile['directory'] / 'trading.db')) as db:
                for table in ('positions', 'order_intents', 'orders', 'order_fills'):
                    result[profile['directory'], table] = list(db.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid'))
        return result

    @staticmethod
    def inventory(root):
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob('*') if path.is_file()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.worker = self.root / 'worker'
        self.archive = self.root / 'archive'
        shutil.copytree(self.case['worker'], self.worker)
        shutil.copytree(self.case['archive'], self.archive)
        # Ordinary validation may leave empty WAL sidecars in the source fixture.
        # Start each coordinator case from the required clean completed cut.
        self.finance_rows(self.worker, self.case['manifest'])
        self.domain = StorageDomain(self.root, domain_id='native-quote-checkpoint',
                                   limit_bytes=512 * 1024**2, reserve_bytes=1024**2, enabled=True)
        self.domain.initialize()
        self.owner = self.enterContext(self.domain.ownership())

    def checkpoint(self, *, worker=None, request=None, through_ordinal=1, **changes):
        from hermes_trading.archive_quote_checkpoint import checkpoint_quote_controls
        options = dict(archive_root=self.archive,
            archive_operation_id=(request or self.case['b'])['operation_id'], through_ordinal=through_ordinal,
            storage_owner=self.owner, cache_root=self.root, enabled=True,
            max_bytes=64 * 1024**2, timeout_seconds=120, now=self.case['now'])
        options.update(changes)
        return checkpoint_quote_controls(worker or self.worker, **options)

    def protective_worker_exit(self, worker, at):
        fixture = copy(self.case['fixture'])
        fixture.root, fixture.primary, fixture.at = worker, worker/'adaptive_200', at
        fixture.market = AsyncMock()
        fixture.market.__aenter__.return_value = fixture.market

        async def collect(assets):
            quote = replace(fixture.snapshot,
                price=dict(fixture.snapshot.price, bid='77', ask='78', received_at=fixture.at.isoformat()),
                venue_rules=venue_record(fixture.snapshot.asset, fixture.at))
            return {quote.asset: quote}

        fixture.market.collect.side_effect = collect
        with closing(sqlite3.connect(worker/'worker_runtime.sqlite')) as db:
            before = json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
        result = asyncio.run(fixture.invoke(storage_probe=lambda root, limits:
            {'status': 'blocked', 'reason': 'state_budget'}))
        self.assertEqual(result['completed_batches'], before['completed_batches']+1)
        self.assertEqual(result['deadline'], before['deadline'])
        self.assertEqual(result['limits'], before['limits'])
        self.assertEqual(result['stop_reason'], 'storage_stop')
        self.assertEqual(result['storage_protection']['status'], 'flat')
        fixture.market.collect.assert_awaited_once()
        packet = _stats(fixture.primary)['latest']['packet']
        self.assertEqual(packet['operational_stop_reason'], 'paired_storage_stop')
        self.assertTrue(packet['operational_entries_blocked'])
        for profile in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(worker/profile['directory']/'trading.db')) as db:
                self.assertFalse(db.execute("SELECT 1 FROM positions WHERE status!='closed'").fetchone())
        return fixture, result

    def recorded_archive(self, fixture, label, through, first_batch, parent):
        cohort = self.root/label
        result = capture(self.case['supervisor'], cohort, enabled=True,
            worker_capture=lambda target, **limits: snapshot(fixture.root, target, enabled=True, **limits),
            experiment_id='paired-test', expected_release=_release_hashes(), through_sequence=through,
            max_bytes=64 * 1024**2, timeout_seconds=90, now=fixture.at)
        inventory = json.loads((cohort/'snapshot_inventory.json').read_text())
        names = [row['path'] for row in inventory['files']] + ['snapshot_inventory.json']
        digest = store.seal(cohort, self.archive, names, experiment='paired-test',
            first_batch=first_batch, last_batch=result['certificate']['observed_complete'], parent=parent)
        certificate = result['certificate']
        shared = fixture.primary/'paper_publications.sqlite'
        request = journal.prepare(shared, enabled=True, experiment_id='paired-test',
            release_sha256=certificate['release_sha256'], archive_sha256=digest,
            account_snapshots=certificate['account_snapshots'], copies={'pc': 'e'*64, 'backup': 'f'*64},
            through_sequence=through)
        keys = {name: secrets.token_hex(32) for name in ('pc', 'backup')}
        body = _json(request)
        receipts = {name: dict(body=body, nonce=request['operation_id'], timestamp=fixture.at.isoformat(),
            signature=sign_command(key, request['operation_id'], fixture.at, body)) for name, key in keys.items()}
        journal.acknowledge(shared, request['operation_id'], receipts, keys, now=fixture.at, enabled=True)
        journal.retire(shared, request['operation_id'], enabled=True)
        return request

    def assert_checkpoint_installed(self, worker, result, through, removed):
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['through_ordinal'], through)
        self.assertEqual(set(result['accounts']), journal.ACCOUNTS)
        self.assertFalse(result['activated'])
        with closing(sqlite3.connect(worker / 'adaptive_200/paper_publications.sqlite')) as db:
            rows = list(db.execute('SELECT id,state,sha256 FROM paired_quote_control_checkpoint'))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 1)
            self.assertEqual(rows[0][2], result['checkpoint_sha256'])
            checkpoint = json.loads(rows[0][1])
            self.assertEqual(_hash(checkpoint), result['checkpoint_sha256'])
            self.assertEqual(checkpoint['through_ordinal'], through)
            pending = db.execute('SELECT COUNT(*) FROM paired_quote_control_pending').fetchone()[0]
            self.assertEqual(pending, 0)
        for profile in self.case['manifest']['profiles']:
            with self.subTest(account=profile['directory']):
                with closing(sqlite3.connect(worker / profile['directory'] / 'trading.db')) as db:
                    row = db.execute("SELECT status,reason FROM incidents WHERE incident_id='archive:quote-checkpoint'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row[0], 'resolved')
                    self.assertEqual(state.unpack(row[1])['checkpoint_sha256'], result['checkpoint_sha256'])
                    for operation_id in removed:
                        self.assertIsNone(db.execute('SELECT 1 FROM incidents WHERE incident_id=?',
                                                    (state.RECEIPT+operation_id,)).fetchone())
        self.assertFalse(state.assert_quote_cohort(worker, manifest=self.case['manifest']))
        return checkpoint

    def test_two_native_rollovers_preserve_finances_and_original_ordinals(self):
        case = self.case
        first = self.assert_checkpoint_installed(case['first_worker'], case['cp1'], 1,
                                                 [case['q1']['operation_id']])
        second = self.assert_checkpoint_installed(case['final_worker'], case['cp2'], 2,
                                                  [case['q1']['operation_id'], case['q2']['operation_id']])
        self.assertEqual(first['from_ordinal'], 1)
        self.assertEqual(first['cumulative_outcomes'], 2)
        self.assertEqual(second['from_ordinal'], 2)
        self.assertEqual(second['cumulative_outcomes'], 4)
        self.assertEqual(second['previous_checkpoint_sha256'], case['cp1']['checkpoint_sha256'])
        self.assertEqual(self.finance_rows(case['first_worker'], case['manifest']), case['first_finances'])
        self.assertEqual(self.finance_rows(case['final_worker'], case['manifest']), case['second_finances'])
        self.assertEqual([row[0] for row in self.control_rows(case['first_worker'])], [2])
        self.assertEqual([row[0] for row in self.control_rows(case['final_worker'])], [3])
        self.assertNotEqual(case['cp1']['checkpoint_sha256'], case['cp2']['checkpoint_sha256'])
        for key in ('q1', 'q2', 'q3'):
            self.assertEqual(case[key]['retired_outcomes'], 2)

    def test_empty_detailed_tail_does_not_reuse_compacted_original_ordinal(self):
        from hermes_trading.archive_quote_checkpoint_state import operation_id
        worker = self.root / 'empty-tail-worker'
        shutil.copytree(self.case['before_q3'], worker)
        result = self.checkpoint(worker=worker, request=self.case['c'], through_ordinal=2)
        self.assertEqual(self.control_rows(worker), [])
        self.assert_checkpoint_installed(worker, result, 2,
            [self.case['q1']['operation_id'], self.case['q2']['operation_id']])
        before = self.finance_rows(worker, self.case['manifest'])
        with patch.object(state, 'MAX_ITEMS', 2):
            retained = retain_quote_outcomes(worker, archive_root=self.archive,
                archive_operation_id=self.case['a']['operation_id'],
                operation_id=operation_id(_hash(self.case['manifest']), 3),
                storage_owner=self.owner, cache_root=self.root, enabled=True,
                max_bytes=64 * 1024**2, timeout_seconds=120, now=self.case['now'])
        self.assertEqual(retained['retired_outcomes'], 2)
        self.assertEqual([row[0] for row in self.control_rows(worker)], [3])
        self.assertEqual(before, self.finance_rows(worker, self.case['manifest']))
        self.assertFalse(state.assert_quote_cohort(worker, manifest=self.case['manifest']))

    def test_interrupted_account_commits_and_acknowledgements_resume_all_eight(self):
        for phase in ('after_plan_commit', 'after_account_commit', 'after_account_ack', 'after_cohort_complete'):
            with self.subTest(phase=phase):
                worker = self.root / phase
                shutil.copytree(self.case['worker'], worker)
                finances = self.finance_rows(worker, self.case['manifest'])
                seen = []

                def interrupt(point, name):
                    if point == phase and not seen:
                        seen.append(name)
                        raise RuntimeError('invented checkpoint interruption')

                with self.assertRaisesRegex(RuntimeError, 'checkpoint interruption'):
                    self.checkpoint(worker=worker, fault=interrupt)
                self.assertEqual(len(seen), 1)
                if phase == 'after_cohort_complete':
                    self.assertFalse(state.assert_quote_cohort(worker, manifest=self.case['manifest']))
                else:
                    with self.assertRaises(ValueError):
                        state.assert_quote_cohort(worker, manifest=self.case['manifest'])
                    self.assertTrue(state.assert_quote_cohort(worker, manifest=self.case['manifest'], protective=True))
                self.assertEqual(finances, self.finance_rows(worker, self.case['manifest']))
                result = self.checkpoint(worker=worker)
                self.assert_checkpoint_installed(worker, result, 1, [self.case['q1']['operation_id']])
                self.assertEqual([row[0] for row in self.control_rows(worker)], [2])
                self.assertEqual(finances, self.finance_rows(worker, self.case['manifest']))

    def test_compacted_checkpoint_and_original_retention_retries_never_create_new_controls(self):
        worker = self.root / 'compacted-retries'
        shutil.copytree(self.case['final_worker'], worker)
        before = self.inventory(worker), self.inventory(self.archive)
        try:
            result = self.checkpoint(worker=worker)
        except ValueError:
            pass
        else:
            self.assertEqual(result['checkpoint_sha256'], self.case['cp2']['checkpoint_sha256'])
        self.assertEqual(before, (self.inventory(worker), self.inventory(self.archive)))
        try:
            result = retain_quote_outcomes(worker, archive_root=self.archive,
                archive_operation_id=self.case['a']['operation_id'], operation_id=self.case['q1']['operation_id'],
                storage_owner=self.owner, cache_root=self.root, enabled=True,
                max_bytes=64 * 1024**2, timeout_seconds=120, now=self.case['now'])
        except ValueError:
            pass
        else:
            self.assertEqual(result['checkpoint_sha256'], self.case['cp2']['checkpoint_sha256'])
        self.assertEqual(before, (self.inventory(worker), self.inventory(self.archive)))
        self.assertEqual([row[0] for row in self.control_rows(worker)], [3])

    def test_disabled_and_unavailable_archive_cannot_mutate_controls_or_finances(self):
        for changes in ({'enabled': False}, {'archive_root': self.root/'missing-archive'}):
            with self.subTest(changes=changes):
                before = self.inventory(self.worker), self.inventory(self.archive)
                with self.assertRaises((ValueError, OSError, sqlite3.Error)):
                    self.checkpoint(**changes)
                self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_empty_tail_rejects_missing_accounts_control_tables_and_active_floors(self):
        from hermes_trading.persistence.repository import StateRepository
        empty = self.root / 'empty-authority-base'
        shutil.copytree(self.case['before_q3'], empty)
        self.checkpoint(worker=empty, request=self.case['c'], through_ordinal=2)
        self.assertEqual(self.control_rows(empty), [])
        for variant in ('missing_database', 'missing_incidents', 'missing_active_floor'):
            worker = self.root / variant
            shutil.copytree(empty, worker)
            if variant == 'missing_database':
                (worker / 'control_200/trading.db').unlink()
            elif variant == 'missing_incidents':
                with closing(sqlite3.connect(worker / 'control_200/trading.db')) as db:
                    db.execute('DROP TABLE incidents')
                    db.commit()
            else:
                selected = None
                for profile in self.case['manifest']['profiles']:
                    path = worker / profile['directory'] / 'trading.db'
                    with closing(sqlite3.connect(path)) as db:
                        row = db.execute("SELECT incident_id,reason FROM incidents WHERE incident_id LIKE 'archive:quote-floor:%' LIMIT 1").fetchone()
                        if row is not None:
                            selected = path, state.unpack(row[1])
                            db.execute('DELETE FROM incidents WHERE incident_id=?', (row[0],))
                            db.commit()
                            break
                self.assertIsNotNone(selected, 'the native cut contains a captured nonterminal replay floor')
            with self.subTest(variant=variant, contract='cohort_guard'):
                with self.assertRaises(ValueError):
                    state.assert_quote_cohort(worker, manifest=self.case['manifest'])
            if variant == 'missing_active_floor':
                path, floor = selected
                with self.subTest(variant=variant, contract='retained_local_replay_authority'):
                    repo = StateRepository.open(path)
                    try:
                        self.assertTrue(repo.is_candle_processed(floor['asset'], floor['timeframe'], floor['through']))
                    finally:
                        repo.close()

    def test_pending_resume_rejects_changed_original_raw_receipts_and_predecessor(self):
        def stop_after_plan(point, name):
            if point == 'after_plan_commit':
                raise RuntimeError('invented pending checkpoint pause')

        pending_first = self.root / 'pending-first-base'
        shutil.copytree(self.case['worker'], pending_first)
        self.finance_rows(pending_first, self.case['manifest'])
        with self.assertRaisesRegex(RuntimeError, 'pending checkpoint pause'):
            self.checkpoint(worker=pending_first, fault=stop_after_plan)
        pending_second = self.root / 'pending-second-base'
        shutil.copytree(self.case['second_worker'], pending_second)
        self.finance_rows(pending_second, self.case['manifest'])
        with self.assertRaisesRegex(RuntimeError, 'pending checkpoint pause'):
            self.checkpoint(worker=pending_second, request=self.case['c'], through_ordinal=2, fault=stop_after_plan)

        for variant in ('selected_quote_receipt', 'previous_checkpoint_receipt', 'previous_shared_checkpoint'):
            with self.subTest(variant=variant):
                worker = self.root / variant
                first = variant == 'selected_quote_receipt'
                shutil.copytree(pending_first if first else pending_second, worker)
                if variant == 'previous_shared_checkpoint':
                    with closing(sqlite3.connect(worker / 'adaptive_200/paper_publications.sqlite')) as db:
                        raw = db.execute('SELECT state FROM paired_quote_control_checkpoint WHERE id=1').fetchone()[0]
                        changed = json.dumps(json.loads(raw), indent=2)
                        self.assertNotEqual(raw, changed)
                        db.execute('UPDATE paired_quote_control_checkpoint SET state=? WHERE id=1', (changed,))
                        db.commit()
                else:
                    key = state.RECEIPT+self.case['q1']['operation_id'] if first else 'archive:quote-checkpoint'
                    with closing(sqlite3.connect(worker / 'control_200/trading.db')) as db:
                        raw = db.execute('SELECT reason FROM incidents WHERE incident_id=?', (key,)).fetchone()[0]
                        changed = json.dumps(json.loads(raw), indent=2)
                        self.assertNotEqual(raw, changed)
                        db.execute('UPDATE incidents SET reason=? WHERE incident_id=?', (changed, key))
                        db.commit()
                self.finance_rows(worker, self.case['manifest'])
                before = self.inventory(worker), self.inventory(self.archive)
                with self.assertRaises(ValueError):
                    self.checkpoint(worker=worker, request=self.case['b'] if first else self.case['c'],
                                    through_ordinal=1 if first else 2)
                self.assertEqual(before, (self.inventory(worker), self.inventory(self.archive)))

    def test_checkpoint_reserves_metadata_headroom_before_pending_mutation(self):
        from hermes_trading.archive_quote_segment import _control_footprint
        from hermes_trading.archive_shared_resolver import _Budget
        budget = _Budget(64 * 1024**2, 120, self.domain.clock)
        with closing(sqlite3.connect(self.worker / 'adaptive_200/paper_publications.sqlite')) as db:
            used = _control_footprint(self.worker, self.case['manifest'], budget, db=db, sealed=False)
            padding = 4 * 1024**2 - 1024 - used
            self.assertGreater(padding, 0)
            raw = db.execute('SELECT progress FROM paired_quote_retention WHERE rowid=2').fetchone()[0]
            db.execute('UPDATE paired_quote_retention SET progress=? WHERE rowid=2', (raw+' '*padding,))
            db.commit()
        self.finance_rows(self.worker, self.case['manifest'])
        before = self.inventory(self.worker), self.inventory(self.archive)
        forbidden_commits = []

        def stop_if_pending_committed(point, name):
            if point == 'after_plan_commit':
                forbidden_commits.append(point)
                # Stop before account mutations while observing a forbidden first commit.
                raise ValueError('invented stop after unadmitted pending metadata commit')

        with self.assertRaises(ValueError):
            self.checkpoint(fault=stop_if_pending_committed)
        with self.subTest(contract='metadata_admitted_before_pending_commit'):
            self.assertEqual(forbidden_commits, [])
        with self.subTest(contract='rejected_transition_preserves_sources'):
            self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

    def test_complete_cohort_rejects_a_gap_in_original_retention_ordinals(self):
        with closing(sqlite3.connect(self.worker / 'adaptive_200/paper_publications.sqlite')) as db:
            db.execute('UPDATE paired_quote_retention SET rowid=3 WHERE rowid=2')
            db.commit()
        with self.assertRaises(ValueError):
            state.assert_quote_cohort(self.worker, manifest=self.case['manifest'])

    def test_native_protective_exit_after_account_commit_survives_checkpoint_resume(self):
        self.finance_rows(self.worker, self.case['manifest'])
        before = self.finance_rows(self.worker, self.case['manifest'])
        original_limits = (self.worker/'worker_limits.json').read_bytes()

        def stop_after_account(point, name):
            if point == 'after_account_commit':
                raise RuntimeError('invented checkpoint account-commit pause')

        with self.assertRaisesRegex(RuntimeError, 'account-commit pause'):
            self.checkpoint(fault=stop_after_account)
        fixture, stopped = self.protective_worker_exit(self.worker, self.case['first_now']+timedelta(seconds=1))
        after_exit = self.finance_rows(self.worker, self.case['manifest'])
        self.assertNotEqual(before, after_exit)
        result = self.checkpoint(now=fixture.at)
        self.assert_checkpoint_installed(self.worker, result, 1, [self.case['q1']['operation_id']])
        self.assertEqual(after_exit, self.finance_rows(self.worker, self.case['manifest']))
        self.assertEqual(original_limits, (self.worker/'worker_limits.json').read_bytes())
        with closing(sqlite3.connect(self.worker/'worker_runtime.sqlite')) as db:
            self.assertEqual(json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0]), stopped)
        with closing(sqlite3.connect(self.worker/'adaptive_200/paper_publications.sqlite')) as db:
            checkpoint = json.loads(db.execute('SELECT state FROM paired_quote_control_checkpoint WHERE id=1').fetchone()[0])
        retained = sum(len(account['active_floors']) for account in checkpoint['accounts'].values())
        self.assertGreater(retained, 0, 'lineages closed after B must retain their captured nonterminal floors')

    def test_native_terminal_capture_purges_closed_floors_and_preserves_replay_protection(self):
        from decimal import Decimal
        from hermes_trading.domain import Decision
        from hermes_trading.persistence.repository import StateRepository
        worker = self.root/'terminal-worker'
        shutil.copytree(self.case['final_worker'], worker)
        fixture, _ = self.protective_worker_exit(worker, self.case['now']+timedelta(seconds=1))
        finances = self.finance_rows(worker, self.case['manifest'])
        request = self.recorded_archive(fixture, 'cohort-d', 9, 10, self.case['c']['archive_sha256'])
        result = self.checkpoint(worker=worker, request=request, through_ordinal=3, now=fixture.at)
        checkpoint = self.assert_checkpoint_installed(worker, result, 3,
            [self.case[name]['operation_id'] for name in ('q1', 'q2', 'q3')])
        self.assertEqual(checkpoint['cumulative_outcomes'], 6)
        self.assertEqual(self.control_rows(worker), [])
        for profile in self.case['manifest']['profiles']:
            name = f"{profile['arm']}_{profile['wallet_eur']}"
            self.assertEqual(checkpoint['accounts'][name]['active_floors'], {})
            self.assertGreater(checkpoint['accounts'][name]['terminal']['closed_positions_count'], 0)
            with closing(sqlite3.connect(worker/profile['directory']/'trading.db')) as db:
                self.assertIsNone(db.execute("SELECT 1 FROM incidents WHERE incident_id LIKE 'archive:quote-floor:%'").fetchone())
        plans = {identity: state.unpack(raw_plan)
                 for root in (self.case['worker'], self.case['second_worker'])
                 for _, identity, raw_plan, _ in self.control_rows(root)}
        for operation in ('q1', 'q2', 'q3'):
            plan = plans[self.case[operation]['operation_id']]
            for account in plan['accounts'].values():
                directory = account['directory']
                for item in account['items']:
                    with self.subTest(operation=operation, decision_id=item['decision_id']):
                        with closing(_readonly(self.case['original_a']/directory/'trading.db')) as original:
                            row = original.execute('SELECT * FROM decisions WHERE decision_id=?', (item['decision_id'],)).fetchone()
                        self.assertIsNotNone(row)
                        decision = Decision(decision_id=row['decision_id'], asset=row['asset'], action=row['action'],
                            reason=row['reason'], candle_id=row['candle_id'], strategy_version=row['strategy_version'],
                            reference_price=Decimal(row['reference_price']), decided_at=row['decided_at'])
                        repo = StateRepository.open(worker/directory/'trading.db')
                        try:
                            self.assertTrue(repo.is_candle_processed(item['asset'], item['timeframe'], item['at']))
                            repo.mark_candle_processed(item['asset'], item['timeframe'], item['at'])
                            self.assertFalse(repo.persist_candle_outcome(item['asset'], item['timeframe'], item['at'], decision))
                            self.assertIsNone(repo._connection.execute('SELECT 1 FROM decisions WHERE decision_id=?',
                                                                      (item['decision_id'],)).fetchone())
                        finally:
                            repo.close()
        self.assertEqual(finances, self.finance_rows(worker, self.case['manifest']))

    def test_terminal_floor_deletion_requires_raw_originals_before_initial_and_resumed_transition(self):
        from hermes_trading.archive_quote_checkpoint_state import operation_id
        cut = self.root/'floor-original-cut'
        shutil.copytree(self.case['final_worker'], cut)
        fixture, _ = self.protective_worker_exit(cut, self.case['now']+timedelta(seconds=1))
        request = self.recorded_archive(fixture, 'floor-cohort-d', 9, 10, self.case['c']['archive_sha256'])
        finances = self.finance_rows(cut, self.case['manifest'])
        selected = None
        for profile in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(cut/profile['directory']/'trading.db')) as db:
                row = db.execute("SELECT incident_id FROM incidents WHERE incident_id LIKE 'archive:quote-floor:%' LIMIT 1").fetchone()
                if row is not None:
                    selected = profile['directory'], row[0]
                    break
        self.assertIsNotNone(selected)

        def stop_after_plan(point, name):
            if point == 'after_plan_commit':
                raise RuntimeError('invented floor-proof pending pause')

        pending = self.root/'floor-original-pending'
        shutil.copytree(cut, pending)
        with self.assertRaisesRegex(RuntimeError, 'floor-proof pending pause'):
            self.checkpoint(worker=pending, request=request, through_ordinal=3, now=fixture.at, fault=stop_after_plan)
        for phase in ('initial', 'resume'):
            for column in ('reason', 'revision'):
                with self.subTest(phase=phase, column=column):
                    worker = self.root/('changed-floor-'+phase+'-'+column)
                    shutil.copytree(cut if phase == 'initial' else pending, worker)
                    directory, key = selected
                    with closing(sqlite3.connect(worker/directory/'trading.db')) as db:
                        if column == 'reason':
                            raw = db.execute('SELECT reason FROM incidents WHERE incident_id=?', (key,)).fetchone()[0]
                            changed = json.dumps(json.loads(raw), indent=2)
                            self.assertNotEqual(raw, changed)
                            db.execute('UPDATE incidents SET reason=? WHERE incident_id=?', (changed, key))
                        else:
                            db.execute('UPDATE incidents SET revision=revision+1 WHERE incident_id=?', (key,))
                        db.commit()
                    self.finance_rows(worker, self.case['manifest'])
                    before = self.inventory(worker), self.inventory(self.archive)
                    with self.assertRaises(ValueError):
                        self.checkpoint(worker=worker, request=request, through_ordinal=3, now=fixture.at)
                    self.assertEqual(before, (self.inventory(worker), self.inventory(self.archive)))

        # A later detailed operation creates a closed-lineage floor absent from D.
        # It must remain outside the deleted subset, including lost-ack recovery.
        worker = self.root/'later-closed-tail'
        shutil.copytree(cut, worker)
        with patch.object(state, 'MAX_ITEMS', 2):
            q4 = retain_quote_outcomes(worker, archive_root=self.archive,
                archive_operation_id=self.case['a']['operation_id'],
                operation_id=operation_id(_hash(self.case['manifest']), 4),
                storage_owner=self.owner, cache_root=self.root, enabled=True,
                max_bytes=64 * 1024**2, timeout_seconds=120, now=fixture.at)
        self.assertEqual(q4['retired_outcomes'], 2)
        self.finance_rows(worker, self.case['manifest'])
        committed = []

        def stop_after_account(point, name):
            if point == 'after_account_commit' and not committed:
                committed.append(name)
                raise RuntimeError('invented already-applied floor purge pause')

        with self.assertRaisesRegex(RuntimeError, 'already-applied floor purge pause'):
            self.checkpoint(worker=worker, request=request, through_ordinal=3, now=fixture.at, fault=stop_after_account)
        self.assertEqual(len(committed), 1)
        result = self.checkpoint(worker=worker, request=request, through_ordinal=3, now=fixture.at)
        self.assert_checkpoint_installed(worker, result, 3, [self.case['q3']['operation_id']])
        self.assertEqual([row[0] for row in self.control_rows(worker)], [4])
        plan = state.unpack(self.control_rows(worker)[0][2])
        for account in plan['accounts'].values():
            with closing(sqlite3.connect(worker/account['directory']/'trading.db')) as db:
                for item in account['items']:
                    self.assertIsNotNone(db.execute('SELECT 1 FROM incidents WHERE incident_id=?',
                        (state.floor_key(item['asset'], item['timeframe']),)).fetchone())
        self.assertEqual(finances, self.finance_rows(worker, self.case['manifest']))

    def test_coordinator_capacity_deadline_and_missing_original_bundle_reject_before_mutation(self):
        from hermes_trading import archive_quote_checkpoint as coordinator
        before = self.inventory(self.worker), self.inventory(self.archive)
        with patch.object(coordinator, '_exclusive', side_effect=AssertionError('SQLite lease preceded capacity admission')):
            with self.assertRaises(CapacityExceeded):
                self.checkpoint(max_bytes=self.domain.limit_bytes)
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

        elapsed = [0]
        head = self.archive/'manifests'/(self.case['b']['archive_sha256']+'.json')
        original_read = store._read

        def expire_after_manifest(path, limit):
            raw = original_read(path, limit)
            if path == head:
                elapsed[0] = 121
            return raw

        with patch.object(self.domain, 'clock', lambda: time.monotonic()+elapsed[0]), \
             patch.object(store, '_read', side_effect=expire_after_manifest):
            with self.assertRaisesRegex(TimeoutError, 'absolute deadline exceeded'):
                self.checkpoint()
        self.assertEqual(elapsed[0], 121)
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))

        a = json.loads((self.archive/'manifests'/(self.case['a']['archive_sha256']+'.json')).read_text())
        b = json.loads(head.read_text())
        later_objects = {obj for member in b['files'] for obj in member['objects']}
        # Q1's target account differs in B because its original quotes are gone.
        member = next(member for member in a['files'] if member['path'] == 'worker/adaptive_50/trading.db')
        only_original = [obj for obj in member['objects'] if obj not in later_objects]
        self.assertTrue(only_original, 'remove an A object that B does not reference')
        (self.archive/'objects'/only_original[0]).unlink()
        before = self.inventory(self.worker), self.inventory(self.archive)
        verified = []
        original_verify = coordinator._verify_archive

        def observe_verified(*args, **kwargs):
            result = original_verify(*args, **kwargs)
            verified.append(args[3])
            return result

        with patch.object(coordinator, '_verify_archive', side_effect=observe_verified):
            with self.assertRaises((ValueError, OSError, sqlite3.Error)):
                self.checkpoint()
        self.assertEqual(verified, [self.case['b']['operation_id']],
                         'later B validates before missing original A evidence is encountered')
        self.assertEqual(before, (self.inventory(self.worker), self.inventory(self.archive)))


if __name__ == '__main__':
    unittest.main()
