from datetime import timedelta
import json,unittest
from unittest.mock import patch,AsyncMock
from dataclasses import replace
from decimal import Decimal
import test_paired_paper as fixtures

from hermes_trading.paired_worker import run_worker
from hermes_trading.reflection_supervision import _hash


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.PairedPaperTests.setUp(self)
        self.at=self.now;self.primary=self.root/'adaptive_100'
        self.policy={'schema_version':1,'experiment_id':'paired-test','owner':'external_llm','cadence_closed_trades':5,
                     'one_variable_only':True,'model':{'provider':'nous','name':'fixed:free'}}
        (self.primary/'reflection_owner.json').write_text(json.dumps(self.policy))
        self.access={'schema_version':1,'provider':'nous','model':'fixed:free','payment':'verified_free','policy_hash':_hash(self.policy),
                     'valid_until':(self.now+timedelta(hours=1)).isoformat(),'provenance_ref':'test'}
        (self.primary/'model_access.json').write_text(json.dumps(self.access))
        self.limits={'schema_version':1,'experiment_id':'paired-test','max_cycles':1,'max_runtime_seconds':86400,'interval_seconds':60}
        (self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        self.storage={'schema_version':1,'experiment_id':'paired-test','max_state_bytes':512*1024*1024,'reserve_bytes':128*1024*1024}
        (self.root/'storage_limits.json').write_text(json.dumps(self.storage))
        from venue_fixtures import venue_record
        self.venue_client=AsyncMock();self.venue_client.__aenter__.return_value=self.venue_client
        self.venue_client.fetch_many.side_effect=lambda assets:{asset:venue_record(asset,self.at,
            minimum_quantity='0.002' if asset=='XRP/EUR' else None) for asset in assets}
    tearDown=fixtures.PairedPaperTests.tearDown
    positions=fixtures.PairedPaperTests.positions
    async def sleep(self,seconds):self.at+=timedelta(seconds=seconds)
    def clock(self):return self.at
    async def invoke(self,**kwargs):
        return await run_worker(self.root,collector_factory=lambda:self.market,clock=self.clock,sleep=kwargs.pop('sleep',self.sleep),**kwargs)

    async def test_malformed_access_blocks_entries_but_running_worker_closes_due_exit(self):
        self.limits['max_cycles']=2;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        async def change_access(seconds):
            self.at+=timedelta(seconds=seconds)
            (self.primary/'model_access.json').write_text('null')
            snapshot=replace(self.snapshot,price=dict(self.snapshot.price,bid='77',ask='78',received_at=self.at.isoformat()))
            self.market.collect.return_value={'ETH/EUR':snapshot}
            for profile in self.manifest['profiles']:
                path=self.root/profile['directory']/'wallet_costs.json';costs=json.loads(path.read_text());costs['as_of']=self.at.isoformat();path.write_text(json.dumps(costs))
        result=await self.invoke(sleep=change_access)
        self.assertEqual(result['completed_batches'],2)
        for profile in self.manifest['profiles']:
            positions=self.positions(profile['directory'])
            self.assertEqual(len(positions),1)
            self.assertEqual(positions[0].status.value,'closed')

    async def test_bound_survives_restart_and_no_new_collection(self):
        result=await self.invoke();self.assertEqual(result['status'],'completed')
        self.assertEqual(result['completed_batches'],1);self.assertEqual(self.market.collect.await_count,1)
        again=await self.invoke();self.assertEqual(again['deadline'],result['deadline'])
        self.assertEqual(self.market.collect.await_count,1)
        heartbeat=json.loads((self.primary/'heartbeat.json').read_text())
        self.assertEqual(heartbeat['execution'],'simulated');self.assertEqual(heartbeat['economic_gate']['status'],'blocked')
    async def test_draft_rejected_before_factory(self):
        self.manifest['status']='prepared_draft_not_activated';(self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):await self.invoke()
        self.assertEqual(self.market.collect.await_count,0)
    async def test_second_worker_cannot_interleave_during_sleep(self):
        self.limits['max_cycles']=2;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        async def nested(seconds):
            with self.assertRaisesRegex(RuntimeError,'worker'):await self.invoke()
            await self.sleep(seconds)
        await self.invoke(sleep=nested)
        self.assertEqual(self.market.collect.await_count,2)
    async def test_crash_after_core_commit_counts_once_on_restart(self):
        from hermes_trading import paired_worker
        original=paired_worker._write_heartbeat;failed=[]
        def crash(*args,**kwargs):
            if args[4]['complete'] and not failed:failed.append(True);raise RuntimeError('heartbeat failed after committed batch')
            return original(*args,**kwargs)
        with patch('hermes_trading.paired_worker._write_heartbeat',side_effect=crash):
            with self.assertRaises(RuntimeError):await self.invoke()
        result=await self.invoke();self.assertEqual(result['completed_batches'],1)
        self.assertEqual(self.market.collect.await_count,1)
    async def test_bound_change_cannot_restart_completed_experiment(self):
        await self.invoke();self.limits['max_cycles']=3;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        with self.assertRaisesRegex(ValueError,'lifecycle'):await self.invoke()
    async def test_expired_model_access_rejected_before_collection(self):
        self.access['valid_until']=(self.now-timedelta(seconds=1)).isoformat();(self.primary/'model_access.json').write_text(json.dumps(self.access))
        with self.assertRaisesRegex(ValueError,'model'):await self.invoke()
        self.assertEqual(self.market.collect.await_count,0)

    async def test_cycle_failure_replaces_previous_healthy_heartbeat(self):
        self.limits['max_cycles']=2;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        self.market.collect.side_effect=[{'ETH/EUR':self.snapshot},RuntimeError('price failed')]
        with self.assertRaises(RuntimeError):await self.invoke()
        heartbeat=json.loads((self.primary/'heartbeat.json').read_text())
        self.assertEqual(heartbeat['status'],'degraded');self.assertIn('RuntimeError',heartbeat['error'])

    async def test_deadline_crossed_during_collection_cannot_open_positions(self):
        self.limits['max_runtime_seconds']=2*3600+5;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        async def slow(assets):self.at+=timedelta(seconds=10);return {'ETH/EUR':self.snapshot}
        self.market.collect.side_effect=slow
        result=await self.invoke();self.assertEqual(result['stop_reason'],'deadline')
        self.assertEqual(result['completed_batches'],0)

    async def test_stop_during_collection_cannot_open_positions(self):
        async def stopped(assets):
            (self.root/'paper_stop.json').write_text(json.dumps({'experiment_id':'paired-test'}))
            return {'ETH/EUR':self.snapshot}
        self.market.collect.side_effect=stopped
        result=await self.invoke()
        self.assertEqual(result['stop_reason'],'operator_stop')
        self.assertEqual(result['completed_batches'],0)
        self.assertFalse((self.primary/'trading.db').exists())

    async def test_flat_wallet_bad_quote_cannot_report_healthy(self):
        self.limits['max_cycles']=2;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        bad=replace(self.snapshot,price=dict(self.snapshot.price,received_at=(self.now-timedelta(minutes=6)).isoformat()))
        self.market.collect.return_value={'ETH/EUR':bad}
        async def inspect(seconds):
            heartbeat=json.loads((self.primary/'heartbeat.json').read_text())
            self.assertEqual(heartbeat['status'],'degraded')
            self.assertFalse(heartbeat['market_sources']['ETH/EUR']['valid'])
            (self.root/'paper_stop.json').write_text(json.dumps({'experiment_id':'paired-test'}))
        await self.invoke(sleep=inspect)

    async def test_public_collector_normalizes_and_excludes_forming_bar(self):
        from hermes_trading.paired_worker import PublicQuoteCollector
        candles=[]
        for index in range(17):
            opened=self.now-timedelta(hours=16-index)
            value='80' if index==15 else ('10000' if index==16 else '100')
            candles.append({'timestamp':opened.isoformat(),'open':value,'high':value,'low':value,'close':value,'volume':'1'})
        payload={'last':'80','bid':'80','ask':'80','received_at':self.now.isoformat(),'candles':candles,'closes':[float(c['close']) for c in candles]}
        client=AsyncMock();client.__aenter__.return_value=client;client.fetch_many.return_value={'ETH/EUR':payload}
        with patch('hermes_trading.paired_worker.PriceClient',return_value=client),patch('hermes_trading.paired_worker.PublicVenueClient',return_value=self.venue_client),patch('hermes_trading.paired_worker._now',return_value=self.now):
            collector=PublicQuoteCollector('1h')
            snapshots=await collector.collect(['ETH/EUR'])
        self.assertEqual(snapshots['ETH/EUR'].price['closes'][-1],Decimal('80'))
        self.assertEqual(len(snapshots['ETH/EUR'].price['closes']),16)
        from hermes_trading.paired_paper import run_paired_cycle
        frozen=AsyncMock();frozen.collect.return_value=snapshots
        await run_paired_cycle(self.root,frozen,now=self.now)
        self.assertEqual(len(self.positions('adaptive_100')),1)

    def public_payload(self,bid='80'):
        return {'last':bid,'bid':bid,'ask':str(Decimal(bid)+1),'received_at':self.at.isoformat(),
                'candles':[{'timestamp':(self.at-timedelta(hours=15-index)).isoformat(),'open':'80','high':'80','low':'80','close':'80','volume':'1'} for index in range(15)]}

    async def test_public_collector_preserves_successful_peer_for_failed_missing_and_malformed_asset(self):
        from hermes_trading.paired_worker import PublicQuoteCollector
        for failure in (RuntimeError('private request details'),None,{}, {'candles':[]}):
            payload={'ETH/EUR':self.public_payload()}
            if failure is not None:payload['XRP/EUR']=failure
            client=AsyncMock();client.__aenter__.return_value=client;client.fetch_many.return_value=payload
            with self.subTest(failure=type(failure).__name__),patch('hermes_trading.paired_worker.PriceClient',return_value=client),patch('hermes_trading.paired_worker.PublicVenueClient',return_value=self.venue_client), \
                    patch('hermes_trading.paired_worker._now',return_value=self.at):
                result=await PublicQuoteCollector('1h').collect(['ETH/EUR','XRP/EUR'])
            self.assertEqual(set(result),{'ETH/EUR','XRP/EUR'})
            self.assertEqual(result['ETH/EUR'].availability,'available')
            self.assertEqual(result['XRP/EUR'].availability,'unavailable')
            self.assertIsNone(result['XRP/EUR'].candle);self.assertIsNone(result['XRP/EUR'].price)
            self.assertNotIn('private request details',result['XRP/EUR'].reason)

    async def test_public_collector_whole_failure_is_complete_unavailable_batch(self):
        from hermes_trading.paired_worker import PublicQuoteCollector
        client=AsyncMock();client.__aenter__.return_value=client;client.fetch_many.side_effect=OSError('network')
        with patch('hermes_trading.paired_worker.PriceClient',return_value=client),patch('hermes_trading.paired_worker.PublicVenueClient',return_value=self.venue_client):
            result=await PublicQuoteCollector('1h').collect(['ETH/EUR','XRP/EUR'])
        self.assertEqual(set(result),{'ETH/EUR','XRP/EUR'})
        self.assertTrue(all(snapshot.availability=='unavailable' and snapshot.candle is None for snapshot in result.values()))

    async def test_public_collector_cancellation_is_not_an_unavailable_result(self):
        import asyncio
        from hermes_trading.paired_worker import PublicQuoteCollector
        client=AsyncMock();client.__aenter__.return_value=client;client.fetch_many.side_effect=asyncio.CancelledError()
        with patch('hermes_trading.paired_worker.PriceClient',return_value=client),patch('hermes_trading.paired_worker.PublicVenueClient',return_value=self.venue_client),self.assertRaises(asyncio.CancelledError):
            await PublicQuoteCollector('1h').collect(['ETH/EUR'])

    async def test_partial_public_failure_protects_peer_and_recovers_running_worker(self):
        from hermes_trading.paired_worker import PublicQuoteCollector
        self.manifest['assets']=['ETH/EUR','XRP/EUR']
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        self.limits['max_cycles']=3;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        candles=[]
        for index in range(16):
            value='80' if index==15 else '100'
            candles.append({'timestamp':(self.now-timedelta(hours=16-index)).isoformat(),
                            'open':value,'high':value,'low':value,'close':value,'volume':'1'})
        client=AsyncMock();client.__aenter__.return_value=client
        step=[]
        async def collect(assets,timeframe):
            price={'last':'80','bid':'77' if len(step)==1 else '80','ask':'80',
                   'received_at':self.at.isoformat(),'candles':candles}
            return {'ETH/EUR':price,'XRP/EUR':RuntimeError('private failure') if len(step)==1 else price}
        client.fetch_many.side_effect=collect
        async def inspect(seconds):
            step.append(True)
            heartbeat=json.loads((self.primary/'heartbeat.json').read_text())
            if len(step)==2:
                self.assertEqual(heartbeat['status'],'degraded')
                self.assertFalse(heartbeat['market_sources']['XRP/EUR']['valid'])
                for profile in self.manifest['profiles']:
                    positions={p.asset:p for p in self.positions(profile['directory'])}
                    self.assertEqual(positions['ETH/EUR'].status.value,'closed')
                    self.assertEqual(positions['XRP/EUR'].status.value,'open')
                    self.assertIsNone(heartbeat['accounts'][profile['directory']]['net_equity'])
            if len(step)==3:
                self.assertEqual(heartbeat['status'],'ok')
                self.assertTrue(all(row['net_equity'] is not None for row in heartbeat['accounts'].values()))
            await self.sleep(seconds)
        def current(value=None):return self.at if value is None else value
        with patch('hermes_trading.paired_worker.PriceClient',return_value=client),patch('hermes_trading.paired_worker.PublicVenueClient',return_value=self.venue_client),patch('hermes_trading.paired_worker._now',side_effect=current):
            result=await run_worker(self.root,collector_factory=lambda:PublicQuoteCollector('1h'),clock=self.clock,sleep=inspect)
        self.assertEqual(result['completed_batches'],3)
        self.assertEqual(client.fetch_many.await_count,3)

    async def test_day30_checkpoint_is_preserved(self):
        from hermes_trading.paired_worker import _write_heartbeat,_stats
        result=await self.invoke()
        at=self.now+timedelta(days=31)
        _write_heartbeat(self.root,self.primary,result,{},_stats(self.primary),at=at,model_ready=False)
        first=(self.root/'checkpoint_30d.json').read_bytes()
        _write_heartbeat(self.root,self.primary,result,{},_stats(self.primary),at=at+timedelta(days=1),model_ready=False)
        self.assertEqual((self.root/'checkpoint_30d.json').read_bytes(),first)

    async def test_stale_signal_with_fresh_quote_cannot_open(self):
        stale=replace(self.snapshot,candle=replace(self.snapshot.candle,open_timestamp=self.now-timedelta(hours=4),close_timestamp=self.now-timedelta(hours=3)))
        self.market.collect.return_value={'ETH/EUR':stale}
        await self.invoke()
        self.assertEqual(len(self.positions('adaptive_100')),0)

    async def test_supervisor_cannot_use_quote_older_than_five_minutes(self):
        from hermes_trading.supervisor_sync import make_snapshot
        self.limits['max_cycles']=2;(self.root/'worker_limits.json').write_text(json.dumps(self.limits))
        async def inspect(seconds):
            make_snapshot(self.primary,now=self.now)
            with self.assertRaisesRegex(ValueError,'market'):
                make_snapshot(self.primary,now=self.now+timedelta(minutes=6))
            (self.root/'paper_stop.json').write_text(json.dumps({'experiment_id':'paired-test'}))
        await self.invoke(sleep=inspect)

    async def test_completed_packet_database_hash_must_match(self):
        import sqlite3
        from hermes_trading.paired_worker import _stats
        from hermes_trading.reflection_supervision import _json
        await self.invoke();packet=_stats(self.primary)['latest']['packet']
        db=sqlite3.connect(self.primary/'paper_publications.sqlite')
        try:
            db.execute('UPDATE paired_batches SET input_hash=?',('corrupt',));db.commit()
            with self.assertRaisesRegex(ValueError,'hash'):_stats(self.primary)
            db.execute('UPDATE paired_batches SET payload=?',(_json(packet),));db.commit()
            with self.assertRaisesRegex(ValueError,'hash'):_stats(self.primary)
        finally:db.close()

    async def test_storage_breach_stops_before_collection_and_survives_restart(self):
        probe=lambda root,limits:{'status':'blocked','reason':'filesystem_reserve','state_bytes':0,'free_bytes':1}
        result=await self.invoke(storage_probe=probe)
        self.assertEqual(result['stop_reason'],'storage_stop');self.assertEqual(self.market.collect.await_count,0)
        again=await self.invoke(storage_probe=lambda root,limits:{'status':'ready'})
        self.assertEqual(again['stop_reason'],'storage_stop');self.assertEqual(self.market.collect.await_count,0)

    async def test_storage_crossed_during_collection_cannot_execute(self):
        probes=[]
        def inspect(root,limits):
            probes.append(True)
            return {'status':'ready' if len(probes)==1 else 'blocked','reason':'filesystem_reserve'}
        result=await self.invoke(storage_probe=inspect)
        self.assertEqual(result['stop_reason'],'storage_stop');self.assertEqual(result['completed_batches'],0)
        self.assertEqual(self.market.collect.await_count,1)

    async def test_missing_storage_policy_prevents_collection(self):
        (self.root/'storage_limits.json').unlink()
        with self.assertRaises(ValueError):await self.invoke()
        self.assertEqual(self.market.collect.await_count,0)

    async def test_storage_limit_changes_cannot_reset_lifecycle(self):
        await self.invoke();self.storage['max_state_bytes']+=1024
        (self.root/'storage_limits.json').write_text(json.dumps(self.storage))
        with self.assertRaisesRegex(ValueError,'lifecycle'):await self.invoke()

    async def test_low_storage_replays_original_pending_batch_then_stops(self):
        from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
        async def crash(root,market,**kwargs):
            async def account(*args,**options):
                await process_paper_account(*args,**options)
                raise RuntimeError('after account write')
            return await run_paired_cycle(root,market,process_account=account,**kwargs)
        with patch('hermes_trading.paired_worker.run_paired_cycle',side_effect=crash):
            with self.assertRaises(RuntimeError):await self.invoke()
        result=await self.invoke(storage_probe=lambda root,limits:{'status':'blocked','reason':'filesystem_reserve'})
        self.assertEqual(result['completed_batches'],1);self.assertEqual(result['stop_reason'],'storage_stop')
        self.assertEqual(self.market.collect.await_count,1)
        self.assertEqual(len(self.positions('adaptive_100')),1)

if __name__=='__main__':unittest.main()
