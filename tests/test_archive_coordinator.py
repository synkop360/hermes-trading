from contextlib import closing
from pathlib import Path
import json,sqlite3,tempfile,unittest
from dataclasses import replace
from unittest.mock import patch

import test_archive_certificate as fixtures
from hermes_trading.archive_coordinator import capture
from hermes_trading.archive_snapshot import snapshot,ownership
from hermes_trading.archive_certificate import _verified_inventory
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import _json


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.CertificateTests('test_completed_eight_account_cut_and_current_finance_are_bound')
        self.f.setUp()
        # Run synchronous fixture cleanups on this live async test case. Calling
        # another IsolatedAsyncioTestCase.doCleanups without its runner silently
        # records cleanup failures on that unused case.
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.pc=self.root/'pc';self.pc.mkdir()
        self.target=self.root/'completed';self.calls=0

    async def ready(self):
        await self.f.capture()
        self.copy_pc()

    def copy_pc(self):
        # Quiescent synthetic copies establish genuinely separate role roots.
        for name in ('reflection_owner.json','llm_review_state.sqlite'):
            (self.pc/name).write_bytes((self.f.target/'adaptive_100'/name).read_bytes())
        with closing(sqlite3.connect(self.pc/'watcher_cycle.sqlite')) as db:
            db.execute('CREATE TABLE cycle(id)');db.commit()

    async def test_original_protective_close_survives_coordinated_capture(self):
        f=self.f.f
        f.limits['max_cycles']=4;(f.root/'worker_limits.json').write_text(json.dumps(f.limits))
        await self.f.capture()
        # Existing original entry now exits via the native protective path.
        quote=dict(f.snapshot.price,bid='77',ask='78',received_at=f.at.isoformat())
        f.market.collect.return_value={'ETH/EUR':replace(f.snapshot,price=quote)}
        async def pause(seconds):
            await f.sleep(seconds)
            if f.market.collect.await_count==3:raise RuntimeError('after original close')
        with self.assertRaisesRegex(RuntimeError,'after original close'):await f.invoke(sleep=pause)
        self.copy_pc()
        (self.pc/'trades.jsonl').write_bytes((f.primary/'trades.jsonl').read_bytes())
        result=self.invoke()
        metadata=json.loads((self.target/'cohort_capture.json').read_text())
        self.assertEqual(result['certificate']['observed_complete'],3)
        self.assertEqual(len(metadata['review_reconciliation']['unreviewed_trade_ids']),1)
        self.assertEqual(len(metadata['review_reconciliation']['recent_trades']),1)

    def worker_capture(self,target,**limits):
        self.calls+=1
        # A concurrent Hermes cycle is excluded throughout remote export.
        with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
            with ownership(self.pc,role='supervisor',enabled=True):pass
        return snapshot(self.f.f.root,target,enabled=True,**limits)

    def invoke(self,**kwargs):
        options={'enabled':True,'worker_capture':self.worker_capture,'experiment_id':'paired-test',
            'expected_release':_release_hashes(),'through_sequence':1,'max_bytes':128*1024*1024,
            'timeout_seconds':90,'now':self.f.f.at}
        options.update(kwargs)
        return capture(self.pc,self.target,**options)

    async def test_completed_bundle_binds_worker_and_real_pc_owner_without_retirement(self):
        await self.ready();result=self.invoke()
        self.assertEqual(self.calls,1)
        self.assertEqual(result['certificate']['supervisor_scope'],'separate_snapshot')
        self.assertEqual(len(result['certificate']['account_snapshots']),8)
        _verified_inventory(self.target,result['manifest_sha256'],lambda:None)
        self.assertEqual(json.loads((self.target/'cohort_capture.json').read_text())['certificate'],result['certificate'])
        self.assertFalse(result['retirement_authorized'])
        self.assertFalse(result['independent_backup_verified'])
        with ownership(self.pc,role='supervisor',enabled=True):pass

    async def test_default_observation_follows_export_but_explicit_cutoff_stays_fixed(self):
        await self.ready();observed=[self.f.f.now]
        def delayed(target,**limits):
            result=self.worker_capture(target,**limits);observed[0]=self.f.f.at;return result
        with patch('hermes_trading.archive_coordinator._now',side_effect=lambda value=None:value or observed[0]):
            self.assertEqual(self.invoke(now=None,worker_capture=delayed)['certificate']['observed_complete'],2)
            self.target=self.root/'explicit-cutoff'
            with self.assertRaisesRegex(ValueError,'future'):
                self.invoke(now=self.f.f.now,worker_capture=delayed)
        self.assertFalse(self.target.exists())

    async def test_disabled_timeout_budget_or_active_controller_never_publishes(self):
        await self.ready()
        with self.assertRaisesRegex(ValueError,'disabled'):self.invoke(enabled=False)
        with self.assertRaisesRegex(ValueError,'budget'):self.invoke(max_bytes=1)
        ticks=iter(range(0,1000,100))
        with self.assertRaisesRegex(TimeoutError,'deadline'):self.invoke(clock=lambda:next(ticks))
        with ownership(self.pc,role='supervisor',enabled=True):
            with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):self.invoke()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.calls,1)  # budget failure reaches bounded exporter

    async def test_changed_release_or_forged_export_inventory_is_not_published(self):
        await self.ready()
        with self.assertRaisesRegex(ValueError,'release'):self.invoke(expected_release={})
        def forged(target,**limits):
            result=self.worker_capture(target,**limits);result['manifest_sha256']='0'*64;return result
        with self.assertRaisesRegex(ValueError,'inventory'):self.invoke(worker_capture=forged)
        self.assertFalse(self.target.exists())

    async def test_controller_ahead_of_worker_cannot_freeze_a_cohort(self):
        await self.ready()
        (self.pc/'trades.jsonl').write_text(_json({'asset':'ETH/EUR','position_id':'missing',
            'closed_at':self.f.f.at.isoformat()})+'\n')
        with self.assertRaisesRegex(ValueError,'ahead|original'):self.invoke()
        self.assertFalse(self.target.exists())

    async def test_controller_policy_must_preserve_exact_json_types(self):
        await self.ready()
        policy=json.loads((self.pc/'reflection_owner.json').read_text());policy['schema_version']=True
        (self.pc/'reflection_owner.json').write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError,'policy'):self.invoke()
        self.assertFalse(self.target.exists())

    async def test_staged_bytes_changed_before_publication_cannot_qualify(self):
        await self.ready()
        def mutate(point):
            if point=='before_publish':
                stage=next(self.root.glob('.cohort-*'))
                (stage/'supervisor/reflection_owner.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'inventory|evidence'):self.invoke(fault=mutate)
        self.assertFalse(self.target.exists())

    async def test_worker_publication_without_original_controller_review_is_rejected(self):
        await self.ready();self.f.publish_after_last_batch()
        with self.assertRaisesRegex(ValueError,'publication|review'):self.invoke()
        self.assertFalse(self.target.exists())

    async def test_fault_retains_all_source_and_never_publishes_partial_bundle(self):
        await self.ready();before=(self.pc/'llm_review_state.sqlite').read_bytes()
        def fail(point):
            if point=='before_publish':raise RuntimeError('synthetic capture interruption')
        with self.assertRaisesRegex(RuntimeError,'interruption'):self.invoke(fault=fail)
        self.assertFalse(self.target.exists())
        self.assertEqual((self.pc/'llm_review_state.sqlite').read_bytes(),before)
        self.assertTrue((self.f.f.primary/'paper_publications.sqlite').exists())
        self.assertFalse(list(self.root.glob('.cohort-*')))


if __name__=='__main__':unittest.main()
