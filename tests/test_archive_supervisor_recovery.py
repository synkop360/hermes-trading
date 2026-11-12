"""Supervisor cut/recovery correctness with invented records and no model calls."""
from contextlib import closing
from datetime import timedelta
import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

import test_archive_review as fixtures
from hermes_trading.archive_snapshot import snapshot,INVENTORY
from hermes_trading.archive_store import seal,restore
from hermes_trading.archive_review import reconcile_reviews
from hermes_trading.reflection_supervision import ReviewLease
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.providers import ProviderAttempt,ProviderRouter,HermesCatalog,ModelCandidate
from hermes_trading.control_auth import SQLiteNonceReplayStore
from hermes_trading.costs import CostLedger,CostEvent
from hermes_trading.model_runner import ReflectionModelRunner
from hermes_trading.global_reflection import run_global_review


class SupervisorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ReviewHistoryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.target=self.root/'restored'
        (self.f.root/'trades.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in self.f.rows))
        with closing(sqlite3.connect(self.f.root/'watcher_cycle.sqlite')) as db:
            db.execute('CREATE TABLE cycle(id INTEGER PRIMARY KEY,state TEXT)')
            db.execute("INSERT INTO cycle VALUES(1,'original bounded cycle')");db.commit()
        repository=StateRepository.open(self.f.root/'llm_providers.sqlite')
        try:
            repository.migrate()
            self.attempt=ProviderAttempt('nous','fixed:free','failed',self.f.now,retry_after_seconds=120)
            repository.record_provider_attempt(self.attempt)
            self.assertTrue(SQLiteNonceReplayStore(repository).add_once('a'*32,
                accepted_at=self.f.now,expires_at=self.f.now+timedelta(minutes=5)))
        finally:repository.close()

    def pending(self,*,response=False):
        review=self.f.prepare(self.f.rows[:5])
        with ReviewLease(self.f.root) as lease:
            lease.record_material(review['review_id'],request={'prompt':'original synthetic request',
                'strategy_sha256':'1'*64,'goal_sha256':'2'*64},now=self.f.now)
            if response:
                lease.record_material(review['review_id'],response={'provider':'nous','model':'fixed:free',
                    'output':'{"outcome":"no_change","reason":"synthetic retained output"}','stderr':''},now=self.f.now)
            review=lease.pending_review()
        CostLedger(self.f.root).record(CostEvent('llm',None,'EUR','missing','synthetic usage receipt unavailable',
            self.f.now,metadata={'review_id':review['review_id']}))
        return review

    def roundtrip(self):
        exported=self.root/'snapshot'
        result=snapshot(self.f.root,exported,role='supervisor',enabled=True,max_bytes=8*1024*1024,timeout_seconds=10)
        inventory=[row['path'] for row in json.loads((exported/INVENTORY).read_text())['files']]+[INVENTORY]
        digest=seal(exported,self.root/'objects',inventory,experiment='review-test',first_batch=1,last_batch=1)
        receipt=restore(self.root/'objects',digest,self.target)
        self.assertFalse(receipt['remote_retirement_authorized'])
        self.assertFalse(result['independent_backup_verified'])
        for name in inventory:self.assertEqual((self.target/name).read_bytes(),(exported/name).read_bytes())

    def reconcile(self):
        with closing(sqlite3.connect(self.target/'llm_review_state.sqlite')) as db:
            state=json.loads(db.execute('SELECT state FROM supervisor').fetchone()[0])
            reviews=[json.loads(row[0]) for row in db.execute('SELECT record FROM reviews ORDER BY review_id')]
        return reconcile_reviews(state,reviews,self.f.rows,policy=self.f.policy,now=self.f.now)

    def assert_pending_survives(self,*,response):
        original=self.pending(response=response);self.roundtrip()
        with ReviewLease(self.target) as lease:
            self.assertEqual(lease.pending_review(),original)
            self.assertEqual(lease.due(self.f.rows,now=self.f.now)['reason'],'pending_review_requires_reconciliation')
        reconciliation=self.reconcile()
        self.assertEqual(reconciliation['pending_review_id'],original['review_id'])
        self.assertEqual(len(reconciliation['unreviewed_trade_ids']),35)
        self.assertEqual(len(reconciliation['recent_trades']),25)
        self.assertEqual(reconciliation['consumed_trade_ids'],[])
        command=Mock()
        runner=ReflectionModelRunner(ProviderRouter(Mock(),HermesCatalog(())),command_run=command,
            strict_pinned=True,pinned_candidate=ModelCandidate('nous','fixed:free',True,'synthetic test'))
        self.assertEqual(run_global_review(self.target,runner,now=self.f.now)['reason'],
                         'pending_review_requires_reconciliation')
        command.assert_not_called()
        self.assertEqual(CostLedger(self.target).missing_categories(),['llm'])
        self.assertEqual((self.target/'cost_events.jsonl').read_bytes(),(self.f.root/'cost_events.jsonl').read_bytes())
        repository=StateRepository.open(self.target/'llm_providers.sqlite')
        try:
            self.assertEqual(repository.provider_attempts(),(self.attempt,))
            self.assertFalse(SQLiteNonceReplayStore(repository).add_once('a'*32,
                accepted_at=self.f.now,expires_at=self.f.now+timedelta(minutes=5)))
        finally:repository.close()

    def test_pending_request_retains_original_id_and_blocks_duplicate_dispatch(self):
        self.assert_pending_survives(response=False)

    def test_saved_response_is_preserved_without_implicit_completion_or_redispatch(self):
        self.assert_pending_survives(response=True)

    def test_completed_review_consumes_exact_original_ids_once_after_restore(self):
        review=self.pending(response=True)
        with ReviewLease(self.f.root) as lease:
            lease.complete(review['review_id'],outcome='no_change',provider='nous',model='fixed:free',now=self.f.now)
        self.roundtrip();result=self.reconcile()
        self.assertIsNone(result['pending_review_id']);self.assertEqual(len(result['consumed_trade_ids']),5)
        self.assertEqual(len(result['unreviewed_trade_ids']),30);self.assertEqual(len(result['recent_trades']),25)
        with ReviewLease(self.target) as lease:
            due=lease.due(self.f.rows,now=self.f.now)
            self.assertTrue(due['eligible']);self.assertEqual(len(due['new_trade_ids']),30)
            next_review=lease.prepare(self.f.rows,now=self.f.now)
            self.assertTrue(set(next_review['trade_ids']).isdisjoint(review['trade_ids']))

    def test_failed_review_keeps_backoff_and_all_unconsumed_ids(self):
        review=self.pending()
        with ReviewLease(self.f.root) as lease:lease.fail(review['review_id'],reason='synthetic disconnect',now=self.f.now)
        self.roundtrip();result=self.reconcile()
        self.assertEqual(result['consumed_trade_ids'],[])
        self.assertEqual(len(result['unreviewed_trade_ids']),35)
        with ReviewLease(self.target) as lease:
            self.assertEqual(lease.due(self.f.rows,now=self.f.now)['reason'],'attempt_backoff')
            self.assertTrue(lease.due(self.f.rows,now=self.f.now+timedelta(minutes=31))['eligible'])


class NativeSupervisorRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_five_close_review_and_uncertain_response_survive_whole_cohort_restore(self):
        from dataclasses import replace
        from datetime import timedelta
        import subprocess,yaml,hashlib
        from unittest.mock import patch
        import test_paired_worker as worker_fixtures
        from hermes_trading.archive_coordinator import capture
        from hermes_trading.archive_review import canonical_closed_rows
        from hermes_trading.paper_publication import _release_hashes
        from hermes_trading.reflection_supervision import initialize_supervisor
        f=worker_fixtures.WorkerTests();f.setUp();self.addCleanup(f.tearDown)
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name)
        for profile in f.manifest['profiles']:
            folder=f.root/profile['directory'];rules=yaml.safe_load((folder/'strategy.yaml').read_bytes())
            rules['simulation'].update(taker_fee_rate='.001',maker_fee_rate='.001',quantity_decimals=8)
            raw=yaml.safe_dump(rules).encode();(folder/'strategy.yaml').write_bytes(raw)
            profile['strategy_sha256']=hashlib.sha256(raw).hexdigest()
        (f.root/'manifest.json').write_text(json.dumps(f.manifest))
        f.limits['max_cycles']=11;(f.root/'worker_limits.json').write_text(json.dumps(f.limits))
        async def collect(assets):
            number=f.market.collect.await_count
            candle_at=f.now+timedelta(minutes=2*((number-1)//2))
            price=dict(f.snapshot.price,bid='80' if number%2 else '77',ask='80' if number%2 else '78',received_at=f.at.isoformat())
            quote=replace(f.snapshot,candle=replace(f.snapshot.candle,
                open_timestamp=candle_at-timedelta(hours=1),close_timestamp=candle_at),price=price,
                venue_rules=__import__('venue_fixtures').venue_record(f.snapshot.asset,f.at))
            for profile in f.manifest['profiles']:
                costs=f.root/profile['directory']/'wallet_costs.json'
                data=json.loads(costs.read_text());data['as_of']=f.at.isoformat();costs.write_text(json.dumps(data))
            return {quote.asset:quote}
        f.market.collect.side_effect=collect
        async def pause(seconds):
            await f.sleep(seconds)
            if f.market.collect.await_count==10:raise RuntimeError('after five native closes')
        with self.assertRaisesRegex(RuntimeError,'five native closes'):await f.invoke(sleep=pause)
        with closing(sqlite3.connect(f.primary/'trading.db')) as db:
            db.row_factory=sqlite3.Row;closed=canonical_closed_rows(db)
        self.assertEqual(len(closed),5)
        pc=root/'supervisor';pc.mkdir()
        for name in ('reflection_owner.json','strategy.yaml','goal.yaml','trades.jsonl','wallet_metrics.json'):
            (pc/name).write_bytes((f.primary/name).read_bytes())
        initialize_supervisor(pc,now=f.now)
        with closing(sqlite3.connect(pc/'watcher_cycle.sqlite')) as db:
            db.execute('CREATE TABLE cycle(id INTEGER PRIMARY KEY,state TEXT)')
            db.execute("INSERT INTO cycle VALUES(1,'original synthetic cycle')");db.commit()
        repository=StateRepository.open(pc/'llm_providers.sqlite');repository.migrate()
        self.addCleanup(repository.close)
        self.assertTrue(SQLiteNonceReplayStore(repository).add_once('b'*32,
            accepted_at=f.at,expires_at=f.at+timedelta(minutes=5)))
        command=Mock(return_value=subprocess.CompletedProcess([],0,
            '{"outcome":"no_change","reason":"synthetic fault/recovery check"}',''))
        runner=ReflectionModelRunner(ProviderRouter(repository,HermesCatalog(())),command_run=command,
            strict_pinned=True,pinned_candidate=ModelCandidate('nous','fixed:free',True,'synthetic test'),cost_ledger=CostLedger(pc))
        original_record=ReviewLease.record_material
        def fail_after_response(lease,review_id,**kwargs):
            if kwargs.get('decision') is not None:raise OSError('synthetic crash before decision commit')
            return original_record(lease,review_id,**kwargs)
        with patch.object(ReviewLease,'record_material',fail_after_response),self.assertRaisesRegex(OSError,'decision commit'):
            run_global_review(pc,runner,now=f.at)
        self.assertEqual(command.call_count,1)
        with ReviewLease(pc) as lease:pending=lease.pending_review()
        self.assertEqual(len(pending['trade_ids']),5)
        self.assertIn('request',pending);self.assertIn('response',pending);self.assertNotIn('decision',pending)
        attempts=repository.provider_attempts();self.assertEqual(len(attempts),1)
        repository.close()
        target=root/'cohort'
        def worker_capture(destination,**limits):return snapshot(f.root,destination,enabled=True,**limits)
        result=capture(pc,target,enabled=True,worker_capture=worker_capture,experiment_id='paired-test',
            expected_release=_release_hashes(),through_sequence=1,max_bytes=128*1024*1024,timeout_seconds=90,now=f.at)
        self.assertFalse(result['retirement_authorized'])
        names=[row['path'] for row in json.loads((target/INVENTORY).read_text())['files']]+[INVENTORY]
        digest=seal(target,root/'archive',names,experiment='paired-test',first_batch=1,last_batch=10)
        restored=root/'restored';restore(root/'archive',digest,restored)
        for name in names:self.assertEqual((restored/name).read_bytes(),(target/name).read_bytes())
        restored_pc=restored/'supervisor'
        with ReviewLease(restored_pc) as lease:
            self.assertEqual(lease.pending_review(),pending)
            self.assertEqual(lease.due(closed,now=f.at)['reason'],'pending_review_requires_reconciliation')
        reopened=StateRepository.open(restored_pc/'llm_providers.sqlite')
        try:
            new_command=Mock()
            new_runner=ReflectionModelRunner(ProviderRouter(reopened,HermesCatalog(())),command_run=new_command,
                strict_pinned=True,pinned_candidate=ModelCandidate('nous','fixed:free',True,'synthetic test'),cost_ledger=CostLedger(restored_pc))
            self.assertEqual(run_global_review(restored_pc,new_runner,now=f.at)['reason'],'pending_review_requires_reconciliation')
            new_command.assert_not_called();self.assertEqual(reopened.provider_attempts(),attempts)
            self.assertFalse(SQLiteNonceReplayStore(reopened).add_once('b'*32,
                accepted_at=f.at,expires_at=f.at+timedelta(minutes=5)))
        finally:reopened.close()
        self.assertEqual((restored_pc/'cost_events.jsonl').read_bytes(),(pc/'cost_events.jsonl').read_bytes())
        self.assertEqual(CostLedger(restored_pc).missing_categories(),CostLedger(pc).missing_categories())
        self.assertTrue(CostLedger(restored_pc).missing_categories())
        self.assertEqual(f.market.collect.await_count,10)


if __name__=='__main__':unittest.main()
