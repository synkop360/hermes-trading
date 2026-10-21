from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import AsyncMock,patch
import httpx

import test_paired_worker as fixtures
from venue_fixtures import venue_record
from hermes_trading.venue_collection import PublicVenueClient
from hermes_trading.paired_paper import run_paired_cycle,process_paper_account
from hermes_trading.paired_worker import PublicQuoteCollector


class VenueCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_quotes_use_the_same_exchange_as_the_frozen_venue_rules(self):
        import os
        at=datetime(2026,10,17,12,tzinfo=timezone.utc)
        prices=AsyncMock();prices.__aenter__.return_value=prices
        prices.fetch_many.return_value={'ETH/EUR':{'bid':'80','ask':'81','received_at':at.isoformat(),'candles':[]}}
        venues=AsyncMock();venues.__aenter__.return_value=venues
        venues.fetch_many.return_value={'ETH/EUR':venue_record('ETH/EUR',at)}
        with patch.dict(os.environ,{'EXCHANGE_ID':'another_exchange'}), \
             patch('hermes_trading.paired_worker.PriceClient',return_value=prices) as factory, \
             patch('hermes_trading.paired_worker.PublicVenueClient',return_value=venues), \
             patch('hermes_trading.paired_worker._now',return_value=at):
            await PublicQuoteCollector('1h').collect(['ETH/EUR'])
        factory.assert_called_once_with(exchange_id='kraken',quote_only_on_history_failure=True)

    async def test_one_bounded_public_request_records_scope_and_actual_receipt(self):
        at=datetime(2026,10,17,12,tzinfo=timezone.utc);seen=[]
        pairs={asset:venue_record(asset,at)['source_pair'] for asset in ['BTC/EUR','ETH/EUR']}
        def response(request):
            seen.append(request)
            return httpx.Response(200,json={'error':[],'result':{str(i):row for i,row in enumerate(pairs.values())}})
        async with PublicVenueClient(transport=httpx.MockTransport(response),clock=lambda:at) as client:
            records=await client.fetch_many(['BTC/EUR','ETH/EUR'])
        self.assertEqual(len(seen),1);self.assertEqual(seen[0].url.host,'api.kraken.com')
        self.assertEqual(seen[0].url.params['country_code'],'FR')
        self.assertNotIn('authorization',seen[0].headers)
        for asset,record in records.items():
            self.assertEqual(record['source_pair'],pairs[asset]);self.assertEqual(record['received_at'],at.isoformat())
        self.assertTrue(client.closed)

    async def test_missing_peer_error_redirect_oversize_and_cancel_never_create_fallback_rules(self):
        at=datetime(2026,10,17,12,tzinfo=timezone.utc)
        for result in [httpx.Response(200,json={'error':[],'result':{'eth':venue_record('ETH/EUR',at)['source_pair']}}),
                       httpx.Response(200,json={'error':['rejected'],'result':{}}),
                       httpx.Response(302,headers={'location':'https://example.com'}),
                       httpx.Response(200,content=b'x'*65537)]:
            async with PublicVenueClient(transport=httpx.MockTransport(lambda request:result),clock=lambda:at) as client:
                records=await client.fetch_many(['ETH/EUR','XRP/EUR'])
            self.assertEqual(set(records),{'ETH/EUR','XRP/EUR'})
            self.assertNotIn('source_pair',records['XRP/EUR'])
        import asyncio
        async def cancelled(request):raise asyncio.CancelledError()
        async with PublicVenueClient(transport=httpx.MockTransport(cancelled)) as client:
            with self.assertRaises(asyncio.CancelledError):await client.fetch_many(['ETH/EUR'])
        self.assertTrue(client.closed)


class CurrentVenueWorkerTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.WorkerTests.setUp
    tearDown=fixtures.WorkerTests.tearDown
    positions=fixtures.WorkerTests.positions

    async def test_all_eight_pending_exits_replay_exact_rules_without_recollection(self):
        first=await run_paired_cycle(self.root,self.market,now=self.now)
        original={name:self.positions(name)[0] for name in [row['directory'] for row in self.manifest['profiles']]}
        at=self.now+timedelta(seconds=2)
        stopped=replace(self.snapshot,price=dict(self.snapshot.price,bid='70',ask='71',received_at=at.isoformat()),
                        venue_rules=venue_record('ETH/EUR',at,minimum_quantity='1'))
        self.market.collect.return_value={'ETH/EUR':stopped}
        calls=[]
        async def crash(*args,**kwargs):
            calls.append(True)
            value=await process_paper_account(*args,**kwargs)
            if len(calls)==3:raise RuntimeError('synthetic account crash')
            return value
        with self.assertRaisesRegex(RuntimeError,'synthetic account crash'):
            await run_paired_cycle(self.root,self.market,now=at,process_account=crash,operational_stop='paired_archive_stop')
        # Newer metadata would allow a sale, but cannot replace this pending input.
        self.market.collect.return_value={'ETH/EUR':replace(stopped,venue_rules=venue_record('ETH/EUR',at))}
        completed=await run_paired_cycle(self.root,self.market,now=at+timedelta(seconds=1))
        self.assertEqual(self.market.collect.await_count,2)
        packet=fixtures.fixtures.PairedPaperTests.packet(self)
        self.assertEqual(packet['schema_version'],2)
        from hermes_trading.paired_paper import _snapshot
        self.assertEqual(_snapshot(packet['snapshots']['ETH/EUR']).venue_rules,stopped.venue_rules)
        self.assertEqual(len(completed['accounts']),8)
        for name,position in original.items():
            self.assertEqual(list(self.positions(name)),[position])
            row=next(item for item in completed['accounts'] if f"{item['arm']}_{item['wallet_eur']}"==name)
            self.assertTrue(row['liquidation_pending'])


if __name__=='__main__':unittest.main()
