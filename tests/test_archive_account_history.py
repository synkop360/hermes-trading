"""Original financial rows across archived prefixes; invented correctness data."""
from contextlib import closing
import hashlib,sqlite3,tempfile,unittest
from pathlib import Path

from hermes_trading.archive_account_history import account_history
from hermes_trading.persistence.repository import StateRepository


class AccountHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.first=self.root/'first.db';self.active=self.root/'active.db'
        with closing(sqlite3.connect(self.first)) as db:
            StateRepository(db).migrate()
            db.execute("INSERT INTO positions(position_id,asset,status,quantity,entry_price,stop_price,strategy_version,opened_at,realized_pnl,fees) VALUES('p','ETH/EUR','open','1','80','75','1','2026-10-16T00:00:00+00:00','0','0.2')")
            db.execute("INSERT INTO decisions VALUES('d','ETH/EUR','buy','original','c','1','80','2026-10-16T00:00:00+00:00')")
            db.commit()
        self.active.write_bytes(self.first.read_bytes())

    def refs(self):
        return [{'path':path,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in (self.first,self.active)]

    def invoke(self,**kwargs):
        options=dict(enabled=True,cache_root=self.root,max_bytes=8*1024*1024,timeout_seconds=10)
        options.update(kwargs);return account_history(self.refs(),**options)

    def change(self,sql):
        with closing(sqlite3.connect(self.active)) as db:db.execute(sql);db.commit()

    def test_read_only_union_preserves_original_rowids_and_removes_disposable_cache(self):
        self.change('DELETE FROM decisions')
        before={p:p.read_bytes() for p in (self.first,self.active)}
        with self.invoke() as db:
            self.assertEqual(db.execute('SELECT rowid,reason FROM decisions').fetchall()[0][1],'original')
            self.assertEqual(StateRepository(db).portfolio_positions()[0].position_id,'p')
            with self.assertRaises(sqlite3.OperationalError):db.execute("DELETE FROM positions")
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertFalse(list(self.root.glob('.account-history-*')))

    def test_active_position_updates_do_not_discard_original_closed_evidence(self):
        self.change("UPDATE positions SET status='closed',closed_at='2026-10-16T01:00:00+00:00',realized_pnl='2',fees='0.4'")
        with self.invoke() as db:
            position=StateRepository(db).closed_positions()[0]
            self.assertEqual(str(position.realized_pnl),'2');self.assertEqual(str(position.fees),'0.4')

    def test_changed_immutable_evidence_or_original_entry_identity_is_rejected(self):
        for sql in ("UPDATE decisions SET reason='changed'","UPDATE positions SET entry_price='81'"):
            self.active.write_bytes(self.first.read_bytes());self.change(sql)
            with self.assertRaisesRegex(ValueError,'immutable|entry'):
                with self.invoke():pass

    def test_rowid_reuse_or_primary_identity_relocation_is_rejected(self):
        for sql in ("UPDATE decisions SET decision_id='other'","UPDATE decisions SET rowid=9"):
            self.active.write_bytes(self.first.read_bytes());self.change(sql)
            with self.assertRaisesRegex(ValueError,'rowid|identity'):
                with self.invoke():pass

    def test_missing_open_inventory_cannot_be_treated_as_closed(self):
        self.change('DELETE FROM positions')
        with self.assertRaisesRegex(ValueError,'open|inventory'):
            with self.invoke():pass

    def test_closed_position_cannot_reopen(self):
        with closing(sqlite3.connect(self.first)) as db:
            db.execute("UPDATE positions SET status='closed',closed_at='2026-10-16T01:00:00+00:00'");db.commit()
        with self.assertRaisesRegex(ValueError,'closed|immutable'):
            with self.invoke():pass

    def test_disabled_wrong_hash_quota_and_expiry_fail_without_source_changes(self):
        for options in ({'enabled':False},{'max_bytes':4096},{'clock':lambda:100,'timeout_seconds':0}):
            with self.assertRaises((ValueError,TimeoutError,sqlite3.Error)):
                with self.invoke(**options):pass
        refs=self.refs();refs[0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'digest|hash'):
            with account_history(refs,enabled=True,cache_root=self.root,max_bytes=8*1024*1024,timeout_seconds=10):pass
        self.assertFalse(list(self.root.glob('.account-history-*')))

    def test_source_growth_after_preflight_is_bounded_during_hashing(self):
        calls=[0]
        def growing_clock():
            calls[0]+=1
            if calls[0]==3:
                with self.first.open('ab') as stream:stream.write(b'X'*(9*1024*1024))
            return 0
        with self.assertRaisesRegex(ValueError,'byte bound'):
            with self.invoke(clock=growing_clock):pass
        self.assertFalse(list(self.root.glob('.account-history-*')))

    def test_expiry_during_original_hash_read_removes_cache(self):
        ticks=iter((0,0,0,20))
        with self.assertRaises(TimeoutError):
            with self.invoke(clock=lambda:next(ticks,20)):pass
        self.assertFalse(list(self.root.glob('.account-history-*')))


class NativeAccountHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_eight_wallets_orders_fees_reports_and_review_rows_match_intact_history(self):
        await self.compare_history(partial=False)

    async def test_all_eight_partial_legs_open_inventory_and_lineage_counts_match_intact_history(self):
        await self.compare_history(partial=True)

    async def compare_history(self,*,partial,restore=False,block_venue=False):
        import json,yaml
        from dataclasses import replace
        from datetime import timedelta
        from decimal import Decimal
        import test_paired_worker as fixtures
        from hermes_trading.archive_snapshot import snapshot
        from hermes_trading.archive_review import canonical_closed_rows
        from hermes_trading.paired_performance_report import _canonical_account
        from hermes_trading.paired_cost_stress import completed_entry_lineages
        from hermes_trading.wallet_observer import value_current_wallet
        from hermes_trading.loop import _load_yaml
        f=fixtures.WorkerTests();f.setUp();self.addCleanup(f.tearDown)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        for profile in f.manifest['profiles']:
            folder=f.root/profile['directory'];strategy=yaml.safe_load((folder/'strategy.yaml').read_bytes())
            strategy['simulation'].update(taker_fee_rate='.001',maker_fee_rate='.001')
            if partial:strategy['exit']={'indicator':'rsi','threshold':'50','trailing_atr':'1.5','partial_close_pct':'0.5'}
            raw=yaml.safe_dump(strategy).encode();(folder/'strategy.yaml').write_bytes(raw)
            profile['strategy_sha256']=hashlib.sha256(raw).hexdigest()
        (f.root/'manifest.json').write_text(json.dumps(f.manifest))
        f.limits['max_cycles']=3 if partial else 2
        (f.root/'worker_limits.json').write_text(json.dumps(f.limits))
        async def pause(seconds):
            await f.sleep(seconds);raise RuntimeError('after original entry')
        with self.assertRaisesRegex(RuntimeError,'original entry'):await f.invoke(sleep=pause)
        prefix=root/'prefix';snapshot(f.root,prefix,enabled=True,max_bytes=128*1024*1024,timeout_seconds=30)
        quote=replace(f.snapshot,price=dict(f.snapshot.price,bid='77',ask='78',received_at=f.at.isoformat()))
        if partial:
            quote=replace(f.snapshot,candle=replace(f.snapshot.candle,
                open_timestamp=f.at-timedelta(hours=1),close_timestamp=f.at,
                open=Decimal('82'),high=Decimal('82'),low=Decimal('82'),close=Decimal('82')),
                price=dict(f.snapshot.price,closes=(Decimal('80'),)*15+(Decimal('82'),),
                    bid='82',ask='82',received_at=f.at.isoformat()))
        f.market.collect.return_value={quote.asset:quote}
        if partial:
            with self.assertRaisesRegex(RuntimeError,'original entry'):await f.invoke(sleep=pause)
        else:await f.invoke()
        closed=root/'closed';snapshot(f.root,closed,enabled=True,max_bytes=128*1024*1024,timeout_seconds=30)
        if restore:
            import shutil
            recovered=root/'recovered'
            shutil.copytree(closed,recovered,ignore=shutil.ignore_patterns('trading.db','snapshot_inventory.json'))
        for profile in f.manifest['profiles']:
            name=profile['directory'];account=f.root/name
            active=root/(name+'.db');active.write_bytes((closed/name/'trading.db').read_bytes())
            # Synthetic copies only: model a missing retired financial prefix.
            with closing(sqlite3.connect(active)) as db:
                for table in ('order_fills','orders','order_intents','projection_payloads','decisions'):
                    db.execute('DELETE FROM '+table)
                db.execute("DELETE FROM positions WHERE status='closed'")
                db.commit()
            sources=[{'path':p,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                     for p in (prefix/name/'trading.db',closed/name/'trading.db',active)]
            if restore:
                from hermes_trading.archive_account_restore import restore_account
                restore_account(sources,recovered/name/'trading.db',enabled=True,cache_root=root,
                    max_bytes=8*1024*1024,timeout_seconds=10)
            with closing(sqlite3.connect(account/'trading.db')) as original:
                original.row_factory=sqlite3.Row
                self.assertGreater(original.execute('SELECT COUNT(*) FROM orders WHERE CAST(fees AS REAL)>0').fetchone()[0],0)
                with account_history(sources,enabled=True,cache_root=root,max_bytes=8*1024*1024,timeout_seconds=10) as joined:
                    for table in ('positions','decisions','order_intents','orders','order_fills','projection_payloads'):
                        self.assertEqual([tuple(row) for row in joined.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid')],
                                         [tuple(row) for row in original.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid')])
                    self.assertEqual(canonical_closed_rows(joined),canonical_closed_rows(original))
                    restored=_canonical_account(account,f.at,database=joined)
                    intact=_canonical_account(account,f.at,database=original)
                    self.assertEqual(restored,intact)
                    lineage=completed_entry_lineages(restored[1],restored[2],start=f.now-timedelta(hours=1),end=f.at)
                    self.assertEqual(lineage['completed'],0 if partial else 1)
                    if partial:
                        self.assertEqual(len(StateRepository(joined).active_positions()),1)
                        self.assertEqual(len(StateRepository(joined).closed_positions()),1)
                        self.assertEqual(StateRepository(joined).active_positions()[0].partial_exit_count,1)
                    strategy=_load_yaml(account/'strategy.yaml')
                    self.assertEqual(value_current_wallet(account,StateRepository(joined),{quote.asset:quote},strategy,now=f.at),
                                     value_current_wallet(account,StateRepository(original),{quote.asset:quote},strategy,now=f.at))
        self.assertEqual(f.market.collect.await_count,2)
        self.assertFalse(list(root.glob('.account-history-*')))
        if restore:
            from unittest.mock import AsyncMock
            from hermes_trading.paired_worker import run_worker
            resume_at=f.at
            exit_quote=replace(quote,price=dict(quote.price,bid='77',ask='78',received_at=resume_at.isoformat()))
            if block_venue:
                from venue_fixtures import venue_record
                exit_quote=replace(exit_quote,venue_rules=venue_record(exit_quote.asset,resume_at,minimum_quantity='1'))
            f.market.collect.return_value={exit_quote.asset:exit_quote}
            intact_state=await f.invoke()
            market=AsyncMock();market.collect.return_value={exit_quote.asset:exit_quote};at=[resume_at]
            async def sleep(seconds):at[0]+=timedelta(seconds=seconds)
            restored_state=await run_worker(recovered,collector_factory=lambda:market,clock=lambda:at[0],sleep=sleep)
            self.assertEqual(restored_state['completed_batches'],3)
            self.assertEqual(restored_state['deadline'],intact_state['deadline'])
            self.assertEqual(restored_state['status'],intact_state['status'])
            self.assertEqual(market.collect.await_count,1)
            self.assertEqual(f.market.collect.await_count,3)
            self.assertEqual(at[0],f.at)
            for profile in f.manifest['profiles']:
                name=profile['directory'];original_path=f.root/name;restored_path=recovered/name
                with closing(sqlite3.connect(original_path/'trading.db')) as original,closing(sqlite3.connect(restored_path/'trading.db')) as joined:
                    original.row_factory=joined.row_factory=sqlite3.Row
                    self.assertEqual(len(StateRepository(joined).active_positions()),1 if block_venue else 0)
                    self.assertEqual(len(StateRepository(joined).closed_positions()),1 if block_venue else 2)
                    if block_venue:
                        position=StateRepository(joined).active_positions()[0]
                        pending=StateRepository(joined).pending_paper_exit(position.position_id)
                        self.assertEqual(pending['reason'],'stop_loss')
                        self.assertEqual(pending,StateRepository(original).pending_paper_exit(position.position_id))
                    self.assertEqual(_canonical_account(restored_path,f.at,database=joined),_canonical_account(original_path,f.at,database=original))
                    strategy=_load_yaml(restored_path/'strategy.yaml')
                    self.assertEqual(value_current_wallet(restored_path,StateRepository(joined),{},strategy,now=f.at),
                                     value_current_wallet(original_path,StateRepository(original),{},strategy,now=f.at))
                self.assertEqual((restored_path/'wallet_costs.json').read_bytes(),(original_path/'wallet_costs.json').read_bytes())
            with closing(sqlite3.connect(f.primary/'paper_publications.sqlite')) as original,closing(sqlite3.connect(recovered/'adaptive_100'/'paper_publications.sqlite')) as joined:
                self.assertEqual(joined.execute('SELECT state FROM paired_risk_state').fetchall(),original.execute('SELECT state FROM paired_risk_state').fetchall())


if __name__=='__main__':unittest.main()
