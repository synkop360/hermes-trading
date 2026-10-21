"""Invented native eight-account retention and interrupted-cohort recovery."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import asyncio,hashlib,importlib.util,json,secrets,shutil,sqlite3,tempfile,unittest

from hermes_trading import archive_journal as journal,archive_store as store
from hermes_trading.archive_coordinator import capture
from hermes_trading.archive_snapshot import snapshot
from hermes_trading.control_auth import sign_command
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _hash,_json
import test_paired_primary_archive as native


from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from hermes_trading import archive_quote_checkpoint_state as checkpoint
from hermes_trading import archive_quote_retention as retention, archive_quote_state as state
from hermes_trading.archive_quote_segment import _control_footprint
from hermes_trading.archive_shared_resolver import _Budget
from hermes_trading.domain import Decision
from hermes_trading.persistence.repository import StateRepository

class QuoteRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.available=importlib.util.find_spec('hermes_trading.archive_quote_retention') is not None
        if not cls.available:return
        cls.fixtures=unittest.TestCase();cls.addClassCleanup(cls.fixtures.doCleanups)
        async def build():
            f,p=native.fresh_selected(cls.fixtures,200,fees=True)
            f.limits['max_cycles']=5;(f.root/'worker_limits.json').write_text(json.dumps(f.limits))
            await f.invoke();pc=native.controller_for(cls.fixtures,f,p)
            tmp=tempfile.TemporaryDirectory();cls.fixtures.addCleanup(tmp.cleanup);base=Path(tmp.name)
            cohort=base/'cohort';archive=base/'archive'
            result=capture(pc,cohort,enabled=True,worker_capture=lambda target,**limits:snapshot(f.root,target,enabled=True,**limits),
                experiment_id='paired-test',expected_release=_release_hashes(),through_sequence=4,
                max_bytes=32*1024**2,timeout_seconds=90,now=f.at)
            names=[r['path'] for r in json.loads((cohort/'snapshot_inventory.json').read_text())['files']]+['snapshot_inventory.json']
            digest=store.seal(cohort,archive,names,experiment='paired-test',first_batch=1,last_batch=5)
            cert=result['certificate'];path=f.primary/'paper_publications.sqlite'
            request=journal.prepare(path,enabled=True,experiment_id='paired-test',release_sha256=cert['release_sha256'],
                archive_sha256=digest,account_snapshots=cert['account_snapshots'],copies={'pc':'e'*64,'backup':'f'*64},through_sequence=4)
            keys={name:secrets.token_hex(32) for name in ('pc','backup')};body=_json(request)
            receipts={name:dict(body=body,nonce=request['operation_id'],timestamp=f.at.isoformat(),
                signature=sign_command(key,request['operation_id'],f.at,body)) for name,key in keys.items()}
            journal.acknowledge(path,request['operation_id'],receipts,keys,now=f.at,enabled=True)
            journal.retire(path,request['operation_id'],enabled=True)
            cls.case=dict(worker=f.root,archive=archive,request=request,now=f.at,manifest=f.manifest,snapshot=f.snapshot)
        asyncio.run(build())

    def setUp(self):
        self.assertTrue(self.available,'coordinated quote retention is not implemented')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.worker=self.root/'worker';self.archive=self.root/'archive'
        shutil.copytree(self.case['worker'],self.worker);shutil.copytree(self.case['archive'],self.archive)
        self.shared=self.worker/'adaptive_200/paper_publications.sqlite'
        from hermes_trading.archive_capacity import StorageDomain
        self.domain=StorageDomain(self.root,domain_id='quote-retention',limit_bytes=256*1024**2,reserve_bytes=1024**2,enabled=True)
        self.domain.initialize();self.owner=self.enterContext(self.domain.ownership())

    def retain(self,**kwargs):
        from hermes_trading.archive_quote_retention import retain_quote_outcomes
        options=dict(archive_root=self.archive,archive_operation_id=self.case['request']['operation_id'],
            storage_owner=self.owner,cache_root=self.root,enabled=True,max_bytes=32*1024**2,timeout_seconds=120,
            now=self.case['now']);options.update(kwargs)
        return retain_quote_outcomes(self.worker,**options)

    def rows(self,profile,table):
        with closing(sqlite3.connect(self.worker/profile/'trading.db')) as db:
            return list(db.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid'))

    def finances(self):
        return {(p['directory'],table):self.rows(p['directory'],table) for p in self.case['manifest']['profiles']
                for table in ('positions','order_intents','orders','order_fills')}

    def test_all_eight_original_finances_and_duplicate_reservations_survive(self):
        from hermes_trading.persistence.repository import StateRepository
        before=self.finances();decisions={p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']}
        result=self.retain()
        self.assertEqual(set(result['accounts']),journal.ACCOUNTS);self.assertEqual(result['status'],'complete')
        self.assertGreater(result['retired_outcomes'],0);self.assertEqual(before,self.finances())
        removed=[]
        for p in self.case['manifest']['profiles']:
            directory=p['directory'];remaining={r[1] for r in self.rows(directory,'decisions')}
            for row in decisions[directory]:
                if row[1] in remaining:continue
                self.assertEqual(row[3:5],('hold','position_open'));removed.append((directory,row))
                candle=row[5];asset=row[2];stamp=row[8];timeframe=candle[len(asset)+1:-(len(stamp)+1)]
                repo=StateRepository.open(self.worker/directory/'trading.db')
                try:
                    repo.migrate();self.assertTrue(repo.is_candle_processed(asset,timeframe,stamp))
                    repo.mark_candle_processed(asset,timeframe,stamp)
                    self.assertFalse(repo._connection.execute('SELECT 1 FROM candles_processed WHERE candle_id=?',(candle,)).fetchone())
                    from decimal import Decimal
                    from hermes_trading.domain import Decision
                    decision=Decision(decision_id=row[1],asset=row[2],action=row[3],reason=row[4],candle_id=row[5],
                        strategy_version=row[6],reference_price=Decimal(row[7]),decided_at=row[8])
                    self.assertFalse(repo.persist_candle_outcome(asset,timeframe,stamp,decision))
                finally:repo.close()
        self.assertTrue(removed);self.assertEqual(self.retain(),result)

    def test_lost_account_ack_blocks_ordinary_consumers_and_resumes(self):
        from hermes_trading.archive_quote_state import assert_quote_retention_complete
        from hermes_trading.paper_publication import assert_no_pending_paired_batch
        before=self.finances()
        def crash(point,account):
            if point=='after_account_commit':raise RuntimeError('invented lost account acknowledgement')
        with self.assertRaisesRegex(RuntimeError,'lost account'):self.retain(fault=crash)
        with closing(sqlite3.connect(self.shared)) as db:
            with self.assertRaisesRegex(ValueError,'quote retention'):assert_quote_retention_complete(db)
        with self.assertRaisesRegex(ValueError,'quote retention'):assert_no_pending_paired_batch(self.worker/'adaptive_200')
        calls=[]
        from unittest.mock import AsyncMock
        from hermes_trading.paired_paper import run_paired_cycle
        market=AsyncMock()
        with self.assertRaisesRegex(ValueError,'quote retention'):
            asyncio.run(run_paired_cycle(self.worker,market,now=self.case['now']+timedelta(seconds=1)))
        market.collect.assert_not_awaited()
        from hermes_trading.paired_performance_report import paired_performance_report
        report=paired_performance_report(self.worker,now=self.case['now'])
        self.assertEqual(report['data_status'],'incomplete');self.assertFalse(report['profitability_qualified'])
        with self.assertRaisesRegex(ValueError,'quote retention'):
            snapshot(self.worker,self.root/'rejected-snapshot',enabled=True,max_bytes=32*1024**2,timeout_seconds=30)
        result=self.retain();self.assertEqual(result['status'],'complete');self.assertEqual(before,self.finances())
        with closing(sqlite3.connect(self.shared)) as db:assert_quote_retention_complete(db)

    def test_archive_outage_rejects_before_canonical_retirement(self):
        before=self.finances();decisions={p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']}
        self.assertTrue(self.archive.resolve().is_relative_to(self.root.resolve()));shutil.rmtree(self.archive)
        with self.assertRaises((ValueError,OSError)):self.retain()
        self.assertEqual(before,self.finances())
        self.assertEqual(decisions,{p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']})

    def test_registered_stage_replacement_cannot_publish_unrelated_bytes(self):
        original={p['directory']:(self.worker/p['directory']/'decisions.jsonl').read_bytes() for p in self.case['manifest']['profiles']}
        def replace_stage(point,name):
            if point=='before_artifact_replace':
                path=next((self.worker/name).glob('.quote-*.tmp'))
                path.unlink();path.write_bytes(b'unrelated replacement\n')
        with self.assertRaisesRegex(ValueError,'stage|artifact'):self.retain(fault=replace_stage)
        self.assertEqual(original,{p['directory']:(self.worker/p['directory']/'decisions.jsonl').read_bytes() for p in self.case['manifest']['profiles']})

    def test_rehashed_future_floor_cannot_suppress_new_quotes(self):
        from hermes_trading.archive_quote_state import assert_quote_cohort,unpack,envelope
        self.retain()
        with closing(sqlite3.connect(self.worker/'adaptive_200/trading.db')) as db:
            key,raw=db.execute("SELECT incident_id,reason FROM incidents WHERE incident_id LIKE 'archive:quote-floor:%' LIMIT 1").fetchone()
            value=unpack(raw);value['through']=(self.case['now']+timedelta(days=365)).isoformat()
            db.execute('UPDATE incidents SET reason=? WHERE incident_id=?',(envelope(value),key));db.commit()
        with self.assertRaisesRegex(ValueError,'floor|quote retention'):assert_quote_cohort(self.worker)

    def test_second_bounded_chunk_keeps_unapplied_old_floors_valid_for_protection(self):
        from unittest.mock import patch
        from hermes_trading import archive_quote_state as state
        before=self.finances();next_id=secrets.token_hex(16)
        with patch.object(state,'MAX_ITEMS',2):
            first=self.retain();self.assertEqual(first['retired_outcomes'],2)
            def crash(point,name):
                if point=='after_plan_commit':raise RuntimeError('invented next chunk pause')
            with self.assertRaisesRegex(RuntimeError,'next chunk'):self.retain(operation_id=next_id,fault=crash)
            self.assertTrue(state.assert_quote_cohort(self.worker,protective=True))
            second=self.retain(operation_id=next_id)
            self.assertLessEqual(second['retired_outcomes'],2);self.assertNotEqual(first['operation_id'],second['operation_id'])
            self.assertEqual(set(second['accounts']),journal.ACCOUNTS)
        self.assertEqual(before,self.finances())

    def test_protective_exit_after_artifact_fault_survives_retention_resume(self):
        from dataclasses import replace
        from unittest.mock import AsyncMock
        from hermes_trading.paired_paper import run_paired_cycle
        before=self.finances()
        def crash(point,name):
            if point=='artifact_chunk':raise RuntimeError('invented artifact write interruption')
        with self.assertRaisesRegex(RuntimeError,'artifact write'):self.retain(fault=crash)
        at=self.case['now']+timedelta(seconds=1);saved=self.case['snapshot']
        # Five worker sleeps exhaust the initial venue record's five-minute
        # lifetime. An executable protection event needs fresh invented facts.
        from venue_fixtures import venue_record
        quote=replace(saved,price=dict(saved.price,bid='77',ask='78',received_at=at.isoformat()),
            venue_rules=venue_record(saved.asset,at))
        market=AsyncMock();market.collect.return_value={quote.asset:quote}
        result=asyncio.run(run_paired_cycle(self.worker,market,now=at,entries_blocked=True,operational_stop='paired_storage_stop'))
        self.assertEqual(result['status'],'complete')
        from hermes_trading.paired_worker import _stats
        packet=_stats(self.worker/'adaptive_200')['latest']['packet']
        self.assertEqual(packet['operational_stop_reason'],'paired_storage_stop')
        self.assertTrue(packet['operational_entries_blocked'])
        after_exit=self.finances();self.assertNotEqual(before,after_exit)
        self.retain();self.assertEqual(after_exit,self.finances())
        for profile in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(self.worker/profile['directory']/'trading.db')) as db:
                self.assertFalse(db.execute("SELECT 1 FROM positions WHERE status!='closed'").fetchone())
                exits=[dict(zip([d[0] for d in db.execute('SELECT * FROM decisions LIMIT 0').description],row))
                       for row in db.execute("SELECT * FROM decisions WHERE action='sell'")]
            lines=[json.loads(line) for line in (self.worker/profile['directory']/'decisions.jsonl').read_text().splitlines()]
            for exit in exits:self.assertIn(exit,lines)

    def test_creation_window_orphan_is_retained_counted_and_not_deleted(self):
        def crash(point,name):
            if point=='after_empty_stage':raise RuntimeError('invented empty stage registration interruption')
        with self.assertRaisesRegex(RuntimeError,'registration'):self.retain(fault=crash)
        orphans=list(self.worker.rglob('.quote-*.tmp'));self.assertEqual(len(orphans),1)
        identity=orphans[0].stat().st_ino;self.assertEqual(orphans[0].read_bytes(),b'')
        self.assertEqual(self.retain()['status'],'complete')
        self.assertEqual(orphans[0].stat().st_ino,identity);self.assertEqual(orphans[0].read_bytes(),b'')
        from hermes_trading.archive_quote_state import operations
        with closing(sqlite3.connect(self.shared)) as db:
            records=operations(db)
        self.assertEqual(sum(len(s['orphans']) for _,p in records for s in p['accounts'].values()),1)

    def test_lost_global_metadata_does_not_reopen_a_partial_cohort(self):
        from hermes_trading.archive_quote_state import assert_quote_cohort
        def crash(point,name):
            if point=='after_account_commit':raise RuntimeError('invented lost global acknowledgement')
        with self.assertRaisesRegex(RuntimeError,'global acknowledgement'):self.retain(fault=crash)
        with closing(sqlite3.connect(self.shared)) as db:db.execute('DROP TABLE paired_quote_retention');db.commit()
        with self.assertRaisesRegex(ValueError,'quote retention'):assert_quote_cohort(self.worker)

    def test_disabled_invalid_bounds_and_small_budget_do_not_retire_accounts(self):
        before={p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']}
        for change in ({'enabled':False},{'max_bytes':4096},{'timeout_seconds':10**1000},
                       {'timeout_seconds':float('nan')},{'timeout_seconds':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.retain(**change)
        self.assertEqual(before,{p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']})

    def test_aggregate_peak_rejection_precedes_plan_and_account_mutation(self):
        from hermes_trading.archive_capacity import StorageDomain,CapacityExceeded
        anchor=self.root/'tight-domain';anchor.mkdir();cache=self.root/'tight-cache';cache.mkdir()
        domain=StorageDomain(anchor,domain_id='tight',extra_roots=[self.worker,cache],limit_bytes=16*1024**2,
            reserve_bytes=1024**2,enabled=True);domain.initialize()
        before={p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']}
        with domain.ownership() as owner:
            with self.assertRaises(CapacityExceeded):self.retain(storage_owner=owner,cache_root=cache)
        self.assertEqual(before,{p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']})
        with closing(sqlite3.connect(self.shared)) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_quote_retention'").fetchone())

    def test_absolute_deadline_after_plan_commit_blocks_mutation_and_can_resume(self):
        from hermes_trading.archive_quote_state import assert_quote_cohort
        original=self.domain.clock;expired=[False]
        before={p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']}
        self.domain.clock=lambda:original()+(1000 if expired[0] else 0)
        def exceed_deadline(point,name):
            if point=='after_plan_commit':expired[0]=True
        try:
            with self.assertRaises(TimeoutError):self.retain(fault=exceed_deadline)
        finally:self.domain.clock=original
        self.assertEqual(before,{p['directory']:self.rows(p['directory'],'decisions') for p in self.case['manifest']['profiles']})
        with self.assertRaisesRegex(ValueError,'quote retention'):assert_quote_cohort(self.worker)
        self.assertEqual(self.retain()['status'],'complete')

    def _retain_worker(self, worker, **changes):
        options = dict(archive_root=self.archive,
            archive_operation_id=self.case['request']['operation_id'],
            storage_owner=self.owner, cache_root=self.root, enabled=True,
            max_bytes=32*1024**2, timeout_seconds=120, now=self.case['now'])
        options.update(changes)
        return retention.retain_quote_outcomes(worker, **options)

    def _ordered_names(self):
        return [f"{p['arm']}_{p['wallet_eur']}" for p in sorted(
            self.case['manifest']['profiles'], key=lambda p: (p['arm'], p['wallet_eur']))]

    def _flat(self, plan):
        return [(name, item) for name in self._ordered_names()
                for item in plan['accounts'][name]['items']]

    def _prefix(self, full, count, identity):
        result = deepcopy(full)
        result['operation_id'] = identity
        for name in self._ordered_names():
            items = result['accounts'][name]['items']
            take = min(count, len(items))
            result['accounts'][name]['items'] = items[:take]
            count -= take
        self.assertEqual(count, 0)
        return result

    @staticmethod
    def _reservation(plan):
        """Declared conservative format allowance, with one plan digest."""
        progress = dict(status='pending', accounts={name: dict(done=False, stage=None, orphans=[])
                                                   for name in journal.ACCOUNTS})
        added = len(state.envelope(plan).encode())+len(state.envelope(progress).encode())
        digest = _hash(plan)
        for name, account in plan['accounts'].items():
            added += len(state.envelope(state.receipt(plan, name)).encode())+512
            for item in account['items']:
                floor = dict(schema_version=1, purpose=state.PURPOSE, asset=item['asset'],
                    timeframe=item['timeframe'], through=item['at'],
                    operation_id=plan['operation_id'], plan_sha256=digest)
                added += len(state.envelope(floor).encode())+512
        return added

    @staticmethod
    def _inventory(root):
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob('*')) if path.is_file()}

    def _control_rows(self, worker):
        with closing(sqlite3.connect(worker/'adaptive_200/paper_publications.sqlite')) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_quote_retention'").fetchone():
                return []
            return list(db.execute('SELECT rowid,operation_id,plan,progress FROM paired_quote_retention ORDER BY rowid'))

    def _assert_fresh_controls(self, worker):
        self.assertEqual(self._control_rows(worker), [])
        with closing(sqlite3.connect(worker/'adaptive_200/paper_publications.sqlite')) as db:
            self.assertEqual(checkpoint.context(db), (None, None, None))
        for profile in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(worker/profile['directory']/'trading.db')) as db:
                self.assertFalse(db.execute(
                    "SELECT 1 FROM incidents WHERE incident_id LIKE 'archive:quote-%'").fetchone())

    def _original_tables(self, worker):
        result = {}
        for profile in self.case['manifest']['profiles']:
            directory = profile['directory']
            with closing(sqlite3.connect(worker/directory/'trading.db')) as db:
                tables = {}
                for table in ('decisions', 'projection_payloads', 'candles_processed', 'incidents'):
                    cursor = db.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid')
                    columns = [column[0] for column in cursor.description]
                    tables[table] = [dict(zip(columns, row)) for row in cursor]
                result[directory] = tables
        return result

    def _all_account_rows(self, worker):
        """The deadline boundary must not mutate any account table or schema."""
        result = {}
        for profile in self.case['manifest']['profiles']:
            with closing(sqlite3.connect(worker/profile['directory']/'trading.db')) as db:
                schema = list(db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
                rows = {}
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                    quoted = '"'+table.replace('"', '""')+'"'
                    rows[table] = list(db.execute('SELECT rowid,* FROM '+quoted+' ORDER BY rowid'))
                result[profile['directory']] = (schema, rows)
        return result

    def _assert_selected_originals_and_replay(self, plan, before):
        after = self._original_tables(self.worker)
        selected = []
        for name in self._ordered_names():
            account = plan['accounts'][name]
            directory = account['directory']
            ids = {item['decision_id'] for item in account['items']}
            candles = {row['candle_id'] for row in before[directory]['decisions'] if row['decision_id'] in ids}
            markers = {'projection:item:'+identity for identity in ids}
            expected = {
                'decisions': [row for row in before[directory]['decisions'] if row['decision_id'] not in ids],
                'projection_payloads': [row for row in before[directory]['projection_payloads'] if row['decision_id'] not in ids],
                'candles_processed': [row for row in before[directory]['candles_processed'] if row['candle_id'] not in candles],
                'incidents': [row for row in before[directory]['incidents'] if row['incident_id'] not in markers],
            }
            for table in ('decisions', 'projection_payloads', 'candles_processed'):
                self.assertEqual(expected[table], after[directory][table], (name, table))
            ordinary_incidents = [row for row in after[directory]['incidents']
                                  if not row['incident_id'].startswith('archive:quote-')]
            self.assertEqual(expected['incidents'], ordinary_incidents, name)
            source_decisions = {row['decision_id']: row for row in before[directory]['decisions']}
            source_payloads = {row['decision_id']: row for row in before[directory]['projection_payloads']}
            source_markers = {row['incident_id']: row for row in before[directory]['incidents']}
            source_candles = {row['candle_id']: row for row in before[directory]['candles_processed']}
            for item in account['items']:
                row = source_decisions[item['decision_id']]
                bundle = dict(decision=row, payload=source_payloads[item['decision_id']],
                    incident=source_markers['projection:item:'+item['decision_id']],
                    processed=source_candles[row['candle_id']])
                self.assertEqual(_hash(bundle), item['bundle_sha256'])
                selected.append((directory, item, row))
        self.assertTrue(selected)
        for directory, item, row in selected:
            repository = StateRepository.open(self.worker/directory/'trading.db')
            try:
                repository.migrate()
                self.assertTrue(repository.is_candle_processed(item['asset'], item['timeframe'], item['at']))
                repository.mark_candle_processed(item['asset'], item['timeframe'], item['at'])
                decision = Decision(decision_id=row['decision_id'], asset=row['asset'], action=row['action'],
                    reason=row['reason'], candle_id=row['candle_id'], strategy_version=row['strategy_version'],
                    reference_price=Decimal(row['reference_price']), decided_at=row['decided_at'])
                self.assertFalse(repository.persist_candle_outcome(item['asset'], item['timeframe'], item['at'], decision))
                self.assertFalse(repository._connection.execute(
                    'SELECT 1 FROM decisions WHERE decision_id=?', (row['decision_id'],)).fetchone())
                self.assertFalse(repository._connection.execute(
                    'SELECT 1 FROM candles_processed WHERE candle_id=?', (row['candle_id'],)).fetchone())
            finally:
                repository.close()

    def test_first_operation_uses_fitting_byte_prefix_with_native_originals(self):
        self._assert_fresh_controls(self.worker)
        finances = self.finances()
        originals = self._original_tables(self.worker)
        archive_before = self._inventory(self.archive)
        sibling = self.root/'full-plan-reference-worker'
        shutil.copytree(self.worker, sibling)
        full_result = self._retain_worker(sibling, operation_id='1'*32)
        self.assertEqual(full_result['status'], 'complete')
        self.assertEqual(set(full_result['accounts']), journal.ACCOUNTS)
        full_rows = self._control_rows(sibling)
        self.assertEqual(len(full_rows), 1)
        full = state.unpack(full_rows[0][2])
        full_count = len(self._flat(full))
        self.assertGreater(full_count, 1)
        self.assertEqual(full_count, full_result['retired_outcomes'])
        cap = 16*1024
        identity = '2'*32
        full_candidate = self._prefix(full, full_count, identity)
        one_candidate = self._prefix(full, 1, identity)
        self.assertGreater(self._reservation(full_candidate), cap, 'native full-plan fixture no longer exceeds the declared cap')
        self.assertLessEqual(self._reservation(one_candidate), cap, 'declared cap cannot hold even one native outcome')
        fitting = max(count for count in range(1, full_count+1)
                      if self._reservation(self._prefix(full, count, identity)) <= cap)
        with patch.object(checkpoint, 'MAX_DETAILED_BYTES', cap):
            try:
                result = self.retain(operation_id=identity)
            except ValueError as error:
                self.fail('A fresh native request must select a fitting nonempty prefix before requiring a checkpoint: '+str(error))
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(set(result['accounts']), journal.ACCOUNTS)
            self.assertEqual(result['retired_outcomes'], fitting)
            self.assertGreater(result['retired_outcomes'], 0)
            self.assertLess(result['retired_outcomes'], full_count)
            rows = self._control_rows(self.worker)
            self.assertEqual([(row[0], row[1]) for row in rows], [(1, identity)])
            actual, progress = state.unpack(rows[0][2]), state.unpack(rows[0][3])
            self.assertEqual(actual, self._prefix(full, fitting, identity))
            self.assertEqual(progress['status'], 'complete')
            self.assertTrue(all(account['done'] for account in progress['accounts'].values()))
            self.assertLessEqual(self._reservation(actual), cap)
            self.assertGreater(self._reservation(self._prefix(full, fitting+1, identity)), cap)
            with closing(sqlite3.connect(self.shared)) as db:
                self.assertEqual(checkpoint.context(db), (None, None, None))
            for name, account in actual['accounts'].items():
                with closing(sqlite3.connect(self.worker/account['directory']/'trading.db')) as db:
                    receipt = db.execute('SELECT status,reason FROM incidents WHERE incident_id=?',
                                         (state.RECEIPT+identity,)).fetchone()
                    self.assertIsNotNone(receipt, name)
                    self.assertEqual(receipt[0], 'resolved')
                    self.assertEqual(state.unpack(receipt[1]), state.receipt(actual, name))
            self.assertFalse(state.assert_quote_cohort(self.worker))
            observed_bytes = _control_footprint(self.worker, self.case['manifest'],
                _Budget(32*1024**2, 120, self.domain.clock), sealed=False)
            self.assertLessEqual(observed_bytes, cap)
            self._assert_selected_originals_and_replay(actual, originals)
            before_retry = self._control_rows(self.worker)
            accounts_before_retry = self._all_account_rows(self.worker)
            self.assertEqual(self.retain(operation_id=identity), result)
            self.assertEqual(self._control_rows(self.worker), before_retry)
            self.assertEqual(self._all_account_rows(self.worker), accounts_before_retry)
        self.assertEqual(finances, self.finances())
        self.assertEqual(archive_before, self._inventory(self.archive))

    def test_expired_selection_cannot_commit_a_pending_plan_or_account_marker(self):
        self._assert_fresh_controls(self.worker)
        before = self._all_account_rows(self.worker)
        archive_before = self._inventory(self.archive)
        original_clock = self.domain.clock
        elapsed = [0]
        completed = []
        original_new_plan = retention._new_plan

        def expire_after_real_selection(*args, **kwargs):
            plan = original_new_plan(*args, **kwargs)
            completed.append(dict(operation_id=plan['operation_id'], outcomes=len(self._flat(plan))))
            self.assertGreater(completed[-1]['outcomes'], 0)
            elapsed[0] = 121
            return plan

        self.domain.clock = lambda: original_clock()+elapsed[0]
        try:
            with patch.object(retention, '_new_plan', side_effect=expire_after_real_selection):
                with self.assertRaisesRegex(TimeoutError, 'absolute deadline exceeded'):
                    self.retain(operation_id='3'*32)
        finally:
            self.domain.clock = original_clock
        self.assertEqual(len(completed), 1, 'the real native selection must finish before injected expiry')
        self.assertEqual(elapsed[0], 121)
        rows = self._control_rows(self.worker)
        # The existing empty shared table may be created. Its file identity is
        # deliberately not asserted; no pending authority/account mutation is allowed.
        self.assertEqual(rows, [], 'expired native selection committed a pending quote-retention authority')
        self.assertEqual(before, self._all_account_rows(self.worker))
        self._assert_fresh_controls(self.worker)
        self.assertEqual(archive_before, self._inventory(self.archive))


if __name__=='__main__':unittest.main()
