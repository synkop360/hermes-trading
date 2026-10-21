from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import hashlib,json,sqlite3,unittest,yaml
from unittest.mock import patch

import test_paired_worker as fixtures
from hermes_trading.paired_worker import _stats,inspect_storage
from hermes_trading.paired_paper import _liquidate_halted_account
from hermes_trading.reflection_supervision import ReviewLease,initialize_supervisor
from hermes_trading.paired_storage_admission import validate_storage_limits


class StorageRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.f.limits['max_cycles']=3
        self.f.storage['protective_stop_on_pressure']=True
        self.pressure=False;self.stale=False;self.after_pressure=None
        for profile in self.f.manifest['profiles']:
            path=self.f.root/profile['directory']/'strategy.yaml';rules=yaml.safe_load(path.read_text())
            rules['simulation'].update(taker_fee_rate='.008',maker_fee_rate='.004')
            path.write_text(yaml.safe_dump(rules));profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.f.root/'manifest.json').write_text(json.dumps(self.f.manifest))

    async def sleep(self,seconds):
        await self.f.sleep(seconds);self.pressure=True
        at=self.f.now-timedelta(minutes=10) if self.stale else self.f.at
        self.f.market.collect.return_value={'ETH/EUR':replace(self.f.snapshot,
            price=dict(self.f.snapshot.price,bid='80',ask='80',received_at=at.isoformat()))}
        if self.after_pressure is not None:self.after_pressure()

    def probe(self,root,limits):
        return {'status':'blocked' if self.pressure else 'ready','reason':'state_budget' if self.pressure else None}

    async def invoke(self):
        (self.f.root/'worker_limits.json').write_text(json.dumps(self.f.limits))
        (self.f.root/'storage_limits.json').write_text(json.dumps(self.f.storage))
        return await self.f.invoke(storage_probe=self.probe,sleep=self.sleep)

    def assert_closed_once(self):
        for profile in self.f.manifest['profiles']:
            path=self.f.root/profile['directory']
            positions=self.f.positions(profile['directory']);self.assertEqual(len(positions),1)
            self.assertEqual(positions[0].status.value,'closed')
            with closing(sqlite3.connect(path/'trading.db')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],2)
                orders=db.execute('SELECT fees,filled_quantity,average_fill_price FROM orders ORDER BY rowid').fetchall()
                self.assertGreater(Decimal(orders[0][0]),0)
                self.assertEqual(Decimal(orders[0][0]),Decimal(orders[1][0]))
                for fee,quantity,price in orders:
                    self.assertEqual(Decimal(fee),Decimal(quantity)*Decimal(price)*Decimal('.008'))
            trade=json.loads((path/'trades.jsonl').read_text().splitlines()[0])
            self.assertEqual(trade['exit_reason'],'paired_storage_stop')
            self.assertEqual(trade['evidence_classification'],'operational_risk_stop')

    async def test_native_eight_wallet_protective_stop_never_enters_or_resets_limits(self):
        result=await self.invoke();self.assert_closed_once()
        self.assertEqual(result['stop_reason'],'storage_stop');self.assertEqual(result['completed_batches'],2)
        self.assertEqual(result['storage_protection']['status'],'flat')
        packet=_stats(self.f.primary)['latest']['packet']
        self.assertEqual(packet['operational_stop_reason'],'paired_storage_stop')
        self.assertTrue(packet['operational_entries_blocked'])
        self.assertFalse(packet['risk_state_before_execution']['global_halted'])
        self.assertEqual(packet['risk_state_before_execution']['threshold'],'0.03')
        again=await self.invoke();self.assertEqual(again['deadline'],result['deadline'])
        self.assertEqual(self.f.market.collect.await_count,2)
        initialize_supervisor(self.f.primary,now=self.f.now)
        with ReviewLease(self.f.primary) as lease:
            trades=[json.loads(line) for line in (self.f.primary/'trades.jsonl').read_text().splitlines()]
            self.assertEqual(lease.due(trades,now=self.f.at)['reason'],'recovery_trade_requires_reconciliation')

    async def test_recovery_is_opt_in(self):
        self.f.storage.pop('protective_stop_on_pressure')
        result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,1)
        self.assertEqual(self.f.positions('adaptive_100')[0].status.value,'open')
        self.assertNotIn('storage_protection',result)

    async def test_no_positions_never_trigger_protective_collection(self):
        self.pressure=True;result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,0)
        self.assertEqual(result['completed_batches'],0)
        self.assertEqual(result['storage_protection']['status'],'flat')

    async def test_missing_model_and_costs_still_allow_exit_but_keep_equity_unknown(self):
        def unavailable():
            (self.f.primary/'model_access.json').unlink()
            for profile in self.f.manifest['profiles']:
                (self.f.root/profile['directory']/'wallet_costs.json').unlink()
        self.after_pressure=unavailable
        result=await self.invoke();self.assert_closed_once()
        self.assertEqual(result['storage_protection']['status'],'flat')
        latest=_stats(self.f.primary)['latest']
        for name,account in latest['accounts'].items():
            self.assertIsNone(account['net_equity'])
            self.assertFalse(latest['packet']['costs'][name]['present'])

    async def test_stale_quote_keeps_explicit_pending_positions_without_fake_exit(self):
        self.stale=True;result=await self.invoke()
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(result['storage_protection']['status'],'positions_pending')
        self.assertEqual(self.f.market.collect.await_count,2)
        for profile in self.f.manifest['profiles']:
            self.assertEqual(self.f.positions(profile['directory'])[0].status.value,'open')

    async def test_malformed_bid_keeps_pending_positions_without_stranding_a_batch(self):
        def malformed():
            quote=self.f.market.collect.return_value['ETH/EUR']
            self.f.market.collect.return_value={'ETH/EUR':replace(quote,price=dict(quote.price,bid='invalid'))}
        self.after_pressure=malformed
        result=await self.invoke()
        self.assertEqual(result['storage_protection']['status'],'positions_pending')
        self.assertFalse(_stats(self.f.primary)['pending'])
        self.assertEqual(self.f.positions('adaptive_100')[0].status.value,'open')

    async def test_boolean_bid_cannot_be_used_as_an_exit_price(self):
        def malformed():
            quote=self.f.market.collect.return_value['ETH/EUR']
            self.f.market.collect.return_value={'ETH/EUR':replace(quote,price=dict(quote.price,bid=True))}
        self.after_pressure=malformed
        result=await self.invoke()
        self.assertEqual(result['storage_protection']['status'],'positions_pending')
        self.assertEqual(self.f.positions('adaptive_100')[0].status.value,'open')

    async def test_cycle_bound_still_prevents_extra_protective_collection(self):
        self.f.limits['max_cycles']=1;result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,1)
        self.assertEqual(result['completed_batches'],1)
        self.assertEqual(result['stop_reason'],'cycle_bound')

    async def test_deadline_still_prevents_extra_protective_collection(self):
        self.f.limits['max_runtime_seconds']=2*3600+30;result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,1)
        self.assertEqual(result['stop_reason'],'deadline')

    async def test_unknown_or_insufficient_rescue_space_blocks_new_writes(self):
        with patch('hermes_trading.paired_worker.inspect_storage',return_value={'status':'blocked','reason':'unknown'}):
            result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,1)
        self.assertEqual(result['storage_protection']['reason'],'protective_space_unavailable')

    async def test_other_experiment_halt_is_not_relabeled_as_a_storage_exit(self):
        self.after_pressure=lambda:(self.f.primary/'experiment_halt.json').write_text(json.dumps({'reason':'supervisor_stop'}))
        result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,1)
        self.assertEqual(result['storage_protection']['reason'],'storage_halt_binding_differs')

    async def test_lost_headroom_after_quote_aborts_without_an_entry_or_retry_loop(self):
        calls=[]
        def space(root,limits):
            calls.append(True)
            return inspect_storage(root,limits) if len(calls)==1 else {'status':'blocked','reason':'unknown'}
        with patch('hermes_trading.paired_worker.inspect_storage',side_effect=space):
            result=await self.invoke()
        self.assertEqual(self.f.market.collect.await_count,2)
        self.assertEqual(result['completed_batches'],1)
        self.assertEqual(result['storage_protection']['reason'],'protective_space_unavailable')
        self.assertEqual(result['storage_protection']['space_report']['measurement']['reason'],'unknown')
        self.assertEqual(self.f.positions('adaptive_100')[0].status.value,'open')

    async def test_fault_after_first_exit_resumes_same_frozen_packet_without_duplicate_orders(self):
        failed=[]
        def crash(*args,**kwargs):
            result=_liquidate_halted_account(*args,**kwargs)
            if not failed:failed.append(True);raise RuntimeError('after original protective exit')
            return result
        with patch('hermes_trading.paired_paper._liquidate_halted_account',side_effect=crash):
            with self.assertRaisesRegex(RuntimeError,'original protective exit'):await self.invoke()
        self.assertTrue(_stats(self.f.primary)['pending'])
        result=await self.invoke();self.assert_closed_once()
        self.assertEqual(result['completed_batches'],2)
        self.assertEqual(self.f.market.collect.await_count,2)

    async def test_completed_stop_packet_prevents_partial_lifecycle_rollback_from_resuming_collection(self):
        await self.invoke()
        with closing(sqlite3.connect(self.f.root/'worker_runtime.sqlite')) as db:
            state=json.loads(db.execute('SELECT state FROM lifecycle WHERE id=1').fetchone()[0])
            state.update(status='running',completed_batches=1)
            for field in ('stop_reason','storage_stop','storage_protection'):state.pop(field,None)
            db.execute('UPDATE lifecycle SET state=?',(json.dumps(state),));db.commit()
        (self.f.primary/'experiment_halt.json').unlink();self.pressure=False
        result=await self.invoke()
        self.assertEqual(result['stop_reason'],'storage_stop')
        self.assertEqual(self.f.market.collect.await_count,2)

    def test_opt_in_requires_a_literal_boolean(self):
        limits=dict(self.f.storage,protective_stop_on_pressure=1)
        with self.assertRaisesRegex(ValueError,'boolean'):validate_storage_limits(limits,'paired-test')


if __name__=='__main__':unittest.main()
