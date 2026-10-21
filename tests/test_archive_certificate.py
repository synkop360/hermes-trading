from contextlib import closing
import hashlib,json,sqlite3,tempfile,unittest
from datetime import timedelta
from dataclasses import replace
import uuid,yaml
from pathlib import Path

import test_paired_worker as fixtures
from hermes_trading.archive_certificate import certify
from hermes_trading.archive_snapshot import snapshot
from hermes_trading.paper_publication import _release_hashes
from hermes_trading.reflection_supervision import initialize_supervisor,_json,ReviewLease
from hermes_trading.paper_publication import build_publication_request,publish_paper_request
from hermes_trading.paper_strategy_proposal import apply_paper_strategy_proposal
from hermes_trading.reflect import _next_version


class CertificateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests('test_bound_survives_restart_and_no_new_collection');self.f.setUp();self.addCleanup(self.f.tearDown)
        initialize_supervisor(self.f.primary,now=self.f.now)
        for profile in self.f.manifest['profiles']:
            folder=self.f.root/profile['directory'];(folder/'history').mkdir(exist_ok=True)
            (folder/'history/v0001.yaml').write_bytes((folder/'strategy.yaml').read_bytes())
        self.f.limits['max_cycles']=3;(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.target=Path(self.tmp.name)/'snapshot'

    async def capture(self,mutate=None):
        async def pause(seconds):
            await self.f.sleep(seconds)
            if self.f.market.collect.await_count==2:raise RuntimeError('synthetic pause')
        with self.assertRaisesRegex(RuntimeError,'synthetic pause'):await self.f.invoke(sleep=pause)
        if mutate:mutate()
        self.receipt=snapshot(self.f.root,self.target,enabled=True,max_bytes=128*1024*1024,timeout_seconds=30)

    def certify(self,**overrides):
        return certify(self.target,**dict({'expected_inventory_sha256':self.receipt['manifest_sha256'],
            'experiment_id':'paired-test','expected_release':_release_hashes(),'through_sequence':1,'now':self.f.at},**overrides))

    async def test_completed_eight_account_cut_and_current_finance_are_bound(self):
        await self.capture();certificate=self.certify()
        self.assertEqual(certificate['observed_complete'],2)
        self.assertEqual(certificate['through_sequence'],1)
        self.assertEqual(len(certificate['account_snapshots']),8)
        self.assertFalse(certificate['retirement_authorized'])
        self.assertEqual(certificate['pending_review'],None)
        with self.assertRaisesRegex(ValueError,'release'):self.certify(expected_release={})
        with self.assertRaisesRegex(ValueError,'experiment'):self.certify(experiment_id='wrong')

    async def test_pending_common_batch_is_not_a_completed_cut(self):
        def mutate():
            with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
                db.execute("UPDATE paired_batches SET status='pending' WHERE rowid=2");db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'pending'):self.certify()

    async def test_unresolved_publication_is_not_certified(self):
        def mutate():
            with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
                db.execute('CREATE TABLE publications(review_id TEXT PRIMARY KEY,request TEXT,receipt TEXT)')
                db.execute("INSERT INTO publications VALUES('pending','{}',NULL)");db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'publication'):self.certify()

    async def test_positions_changed_after_settlement_are_not_certified(self):
        def mutate():
            with closing(sqlite3.connect(self.f.root/'adaptive_50/trading.db')) as db:
                db.execute("UPDATE positions SET stop_price='1'");db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'positions'):self.certify()

    async def test_missing_or_changed_file_rejects_even_if_databases_look_valid(self):
        await self.capture()
        (self.target/'adaptive_100/goal.yaml').write_text('different goal')
        with self.assertRaisesRegex(ValueError,'inventory|evidence'):self.certify()

    async def test_pending_review_is_preserved_without_dispatch_or_watermark_reset(self):
        prepared=[]
        def mutate():
            # Exercise the native state shape; prepare performs no model call.
            trades=[{'asset':'ETH/EUR','position_id':f'close-{i}',
                'closed_at':(self.f.now+timedelta(seconds=i+1)).isoformat()} for i in range(5)]
            with ReviewLease(self.f.primary) as lease:
                prepared.append(lease.prepare(trades,now=self.f.at))
        await self.capture(mutate)
        self.assertEqual(self.certify()['pending_review'],prepared[0])

    async def test_pending_review_pointer_and_record_must_match_exactly(self):
        def mutate():
            with closing(sqlite3.connect(self.f.primary/'llm_review_state.sqlite')) as db:
                state=json.loads(db.execute('SELECT state FROM supervisor WHERE id=1').fetchone()[0])
                record={'review_id':'pending-id','outcome':'pending','trade_ids':['one']}
                state['pending_review']=dict(record,trade_ids=['different'])
                db.execute('INSERT INTO reviews VALUES(?,?)',('pending-id',_json(record)))
                db.execute('UPDATE supervisor SET state=?',(_json(state),));db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'pending review'):self.certify()

    async def test_pending_review_json_boolean_is_not_an_integer(self):
        def mutate():
            trades=[{'asset':'ETH/EUR','position_id':f'close-{i}','quantity':1,
                'closed_at':(self.f.now+timedelta(seconds=i+1)).isoformat()} for i in range(5)]
            with ReviewLease(self.f.primary) as lease:
                review=lease.prepare(trades,now=self.f.at)
            review['trade_snapshot'][0]['quantity']=True
            with closing(sqlite3.connect(self.f.primary/'llm_review_state.sqlite')) as db:
                db.execute('UPDATE reviews SET record=? WHERE review_id=?',(_json(review),review['review_id']));db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'pending review'):self.certify()

    async def test_goal_changed_since_last_completed_batch_is_not_certified(self):
        await self.capture(lambda:(self.f.root/'adaptive_50/goal.yaml').write_text('assets: [ETH/EUR]\nmax_drawdown_pct: 99\n'))
        with self.assertRaisesRegex(ValueError,'goal'):self.certify()

    async def test_modified_original_version_archive_is_not_certified(self):
        await self.capture(lambda:(self.f.primary/'history/v0001.yaml').write_text('version: 1\nsimulation: {taker_fee_rate: 0}\n'))
        with self.assertRaisesRegex(ValueError,'strategy|contract|history'):self.certify()

    async def test_changed_fixed_control_is_not_certified(self):
        def mutate():
            path=self.f.root/'control_50/strategy.yaml'
            path.write_text(path.read_text()+'\nchanged_after_batch: true\n')
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'control|strategy'):self.certify()

    async def test_separate_pc_supervisor_snapshot_is_explicitly_bound(self):
        await self.capture()
        controller=Path(self.tmp.name)/'pc-supervisor';controller.mkdir()
        files=[]
        for name in ('llm_review_state.sqlite','reflection_owner.json'):
            raw=(self.target/'adaptive_100'/name).read_bytes();(controller/name).write_bytes(raw)
            files.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        raw=(_json({'schema_version':1,'purpose':'paired_consistent_snapshot_v1','files':files})+'\n').encode()
        (controller/'snapshot_inventory.json').write_bytes(raw);controller_hash=hashlib.sha256(raw).hexdigest()
        # A synthetic worker-only export omits PC-owned review state.
        (self.target/'adaptive_100/llm_review_state.sqlite').unlink()
        inventory=json.loads((self.target/'snapshot_inventory.json').read_text())
        inventory['files']=[row for row in inventory['files'] if row['path']!='adaptive_100/llm_review_state.sqlite']
        raw=(_json(inventory)+'\n').encode();(self.target/'snapshot_inventory.json').write_bytes(raw)
        self.receipt['manifest_sha256']=hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ValueError,'supervisor'):self.certify()
        result=self.certify(supervisor_root=controller,expected_supervisor_inventory_sha256=controller_hash)
        self.assertEqual(result['supervisor_scope'],'separate_snapshot')
        self.assertEqual(result['supervisor_inventory_sha256'],controller_hash)
        (controller/'reflection_owner.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'inventory|evidence'):
            self.certify(supervisor_root=controller,expected_supervisor_inventory_sha256=controller_hash)

    def publish_after_last_batch(self):
        primary=self.f.primary;raw=(primary/'strategy.yaml').read_bytes();base=yaml.safe_load(raw)
        candidate,_,_=apply_paper_strategy_proposal(base,{'variable':'entry.threshold','asset':None,'value':31},assets=['ETH/EUR'])
        candidate['version']=_next_version(str(base['version']))
        review={'review_id':str(uuid.uuid4()),'outcome':'pending','started_at':(self.f.at-timedelta(seconds=1)).isoformat(),
            'request':{'strategy':base,'strategy_sha256':hashlib.sha256(raw).hexdigest(),
                'goal_sha256':hashlib.sha256((primary/'goal.yaml').read_bytes()).hexdigest()},
            'decision':{'status':'awaiting_publication','based_on_sha256':hashlib.sha256(raw).hexdigest(),
                'candidate':candidate,'variable':'entry.threshold','asset':None},
            'response':{'provider':'nous','model':'fixed:free'}}
        request=build_publication_request(review,self.f.policy)
        publish_paper_request(primary,request,env={'HERMES_TRADING_MODE':'paper'},now=self.f.at)

    async def test_publication_after_last_signal_preserves_original_entry_contract(self):
        await self.capture(self.publish_after_last_batch)
        self.assertEqual(self.certify()['observed_complete'],2)
        self.assertEqual(int(yaml.safe_load((self.target/'adaptive_100/strategy.yaml').read_bytes())['version']),2)

    async def test_invalid_publication_request_hash_cannot_supply_strategy_contracts(self):
        def mutate():
            self.publish_after_last_batch()
            with closing(sqlite3.connect(self.f.primary/'paper_publications.sqlite')) as db:
                db.execute('UPDATE publications SET request_hash=?',('0'*64,));db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'publication|request'):self.certify()

    async def test_altered_order_fee_must_reconcile_with_fills_and_settlement(self):
        def mutate():
            with closing(sqlite3.connect(self.f.root/'adaptive_50/trading.db')) as db:
                db.execute("UPDATE orders SET fees='1'");db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'fee|fill|cash'):self.certify()

    async def test_future_dated_financial_activity_is_not_filtered_away(self):
        def mutate():
            with closing(sqlite3.connect(self.f.root/'adaptive_50/trading.db')) as db:
                db.execute('UPDATE orders SET created_at=?',((self.f.at+timedelta(days=1)).isoformat(),));db.commit()
        await self.capture(mutate)
        with self.assertRaisesRegex(ValueError,'future|cut|financial'):self.certify()

    async def test_nonzero_fee_entry_and_closed_position_reconcile_for_all_wallets(self):
        # Invented executable quotes and configured fees, not a market backtest.
        for profile in self.f.manifest['profiles']:
            path=self.f.root/profile['directory'];rules=yaml.safe_load((path/'strategy.yaml').read_bytes())
            rules['simulation'].update(taker_fee_rate='.001',maker_fee_rate='.001')
            raw=yaml.safe_dump(rules).encode();(path/'strategy.yaml').write_bytes(raw)
            (path/'history/v0001.yaml').write_bytes(raw);profile['strategy_sha256']=hashlib.sha256(raw).hexdigest()
        (self.f.root/'manifest.json').write_text(json.dumps(self.f.manifest))
        self.f.limits['max_cycles']=4;(self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        await self.capture()
        quote=dict(self.f.snapshot.price,bid='77',ask='78',received_at=self.f.at.isoformat())
        self.f.market.collect.return_value={'ETH/EUR':replace(self.f.snapshot,price=quote)}
        async def pause_after_close(seconds):
            await self.f.sleep(seconds)
            if self.f.market.collect.await_count==3:raise RuntimeError('after fee close')
        with self.assertRaisesRegex(RuntimeError,'after fee close'):await self.f.invoke(sleep=pause_after_close)
        self.target=Path(self.tmp.name)/'snapshot-after-close'
        self.receipt=snapshot(self.f.root,self.target,enabled=True,max_bytes=128*1024*1024,timeout_seconds=30)
        self.assertEqual(self.certify()['observed_complete'],3)
        for profile in self.f.manifest['profiles']:
            with closing(sqlite3.connect(self.target/profile['directory']/'trading.db')) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM positions WHERE status='closed'").fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)


if __name__=='__main__':unittest.main()
