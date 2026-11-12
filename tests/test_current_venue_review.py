"""Invented review reproductions; no network, provider, deployment or orders."""
from datetime import timedelta
import hashlib,json,unittest,uuid
from unittest.mock import AsyncMock,patch
import yaml

import test_venue_candidate as candidate_fixtures
import test_paired_worker as worker_fixtures
from hermes_trading.adapters.price import PriceClient
from hermes_trading.market_data import MarketSnapshot
from hermes_trading.paired_paper import run_paired_cycle,_profiles
from hermes_trading.paired_worker import PublicQuoteCollector
from hermes_trading.paired_inception import initialize_paired_inception,read_paired_inception
from venue_fixtures import venue_record


class ReviewRegressionCases(unittest.IsolatedAsyncioTestCase):
    async def test_real_authorized_adaptation_preserves_activation_and_snapshot_audit(self):
        from hermes_trading.paper_publication import build_publication_request,publish_paper_request
        from hermes_trading.paper_strategy_proposal import apply_paper_strategy_proposal
        from hermes_trading.archive_snapshot import snapshot
        f=candidate_fixtures.VenueCandidateTests();f.setUp()
        for function,args,kwargs in f._cleanups:self.addCleanup(function,*args,**kwargs)
        f._cleanups.clear()
        manifest=f.activate_copy()
        venue=candidate_fixtures.build_activation_receipt(f.target,f.venue,at=f.at)
        (f.target/'venue_activation.json').write_text(json.dumps(venue))
        for profile in manifest['profiles']:
            costs={'schema_version':1,'experiment_id':'synthetic-activation','currency':'EUR',
                'scope_start':f.at.isoformat(),'as_of':f.at.isoformat(),
                'totals':{name:{'amount':'0','source':'synthetic_review_only','quality':'actual'}
                         for name in ('hosting','ai','tools','funding')}}
            (f.target/profile['directory']/'wallet_costs.json').write_text(json.dumps(costs))
        primary=f.target/'adaptive_100'
        policy={'schema_version':1,'experiment_id':'synthetic-activation','owner':'external_llm',
            'cadence_closed_trades':5,'one_variable_only':True,'model':{'provider':'nous','name':'fixed:free'}}
        (primary/'reflection_owner.json').write_text(json.dumps(policy))
        opening=initialize_paired_inception(f.target,clock=lambda:f.at)
        raw=(primary/'strategy.yaml').read_bytes();base=yaml.safe_load(raw)
        proposed,variable,asset=apply_paper_strategy_proposal(base,
            {'variable':'exit.max_bars','asset':None,'value':23},assets=manifest['assets'])
        from hermes_trading.reflect import _next_version
        proposed['version']=_next_version(base['version'])
        review={'review_id':str(uuid.uuid4()),'outcome':'pending','started_at':f.at.isoformat(),
            'request':{'goal_sha256':hashlib.sha256((primary/'goal.yaml').read_bytes()).hexdigest(),
                'strategy_sha256':hashlib.sha256(raw).hexdigest(),'strategy':base},
            'decision':{'status':'awaiting_publication','candidate':proposed,'variable':variable,'asset':asset,
                'based_on_sha256':hashlib.sha256(raw).hexdigest()},
            'response':{'provider':'nous','model':'fixed:free'}}
        publish_paper_request(primary,build_publication_request(review,policy),
            env={'HERMES_TRADING_MODE':'paper'},now=f.at+timedelta(seconds=1))
        self.assertEqual(candidate_fixtures.validate_activation_venue(f.target),venue)
        self.assertEqual(read_paired_inception(f.target),opening)
        market=AsyncMock();market.collect.return_value={asset:MarketSnapshot(asset,'1h','unavailable','no_trade','synthetic_history_missing',
            venue_rules=f.venue['records'][asset]) for asset in manifest['assets']}
        from hermes_trading.reflection_supervision import _hash
        from hermes_trading.paired_worker import run_worker,_stats
        (primary/'model_access.json').write_text(json.dumps({'schema_version':1,'provider':'nous','model':'fixed:free',
            'payment':'verified_free','policy_hash':_hash(policy),'provenance_ref':'invented_correctness_fixture_no_inference',
            'valid_until':(f.at+timedelta(hours=1)).isoformat()}))
        (f.target/'worker_limits.json').write_text(json.dumps({'schema_version':1,'experiment_id':'synthetic-activation',
            'max_cycles':1,'max_runtime_seconds':3600,'interval_seconds':60}))
        (f.target/'storage_limits.json').write_text(json.dumps({'schema_version':1,'experiment_id':'synthetic-activation',
            'max_state_bytes':512*1024*1024,'reserve_bytes':128*1024*1024}))
        await run_worker(f.target,collector_factory=lambda:market,clock=lambda:f.at+timedelta(seconds=2),sleep=AsyncMock())
        self.assertEqual(len(_stats(primary)['latest']['accounts']),8)
        copied=f.root/'post-publication-snapshot'
        snapshot(f.target,copied,enabled=True,max_bytes=128*1024*1024,timeout_seconds=30)
        self.assertEqual(len(_profiles(copied,manifest)),8)
        self.assertEqual(read_paired_inception(copied),opening)

    async def test_history_failure_retains_quotes_for_eight_operational_exits(self):
        f=worker_fixtures.WorkerTests();f.setUp();self.addCleanup(f.tearDown)
        await run_paired_cycle(f.root,f.market,now=f.now)
        at=f.now+timedelta(seconds=1)
        prices=AsyncMock();prices.__aenter__.return_value=prices
        prices.fetch_many.return_value={'ETH/EUR':{'last':'77','bid':'77','ask':'78',
            'received_at':at.isoformat(),'source':'synthetic_received_quote','candles':[]}}
        venues=AsyncMock();venues.__aenter__.return_value=venues
        venues.fetch_many.return_value={'ETH/EUR':venue_record('ETH/EUR',at)}
        with patch('hermes_trading.paired_worker.PriceClient',return_value=prices), \
             patch('hermes_trading.paired_worker.PublicVenueClient',return_value=venues), \
             patch('hermes_trading.paired_worker._now',return_value=at):
            inputs=await PublicQuoteCollector('1h').collect(['ETH/EUR'])
        self.assertIsNone(inputs['ETH/EUR'].candle)
        self.assertIsNotNone(inputs['ETH/EUR'].price)
        self.assertEqual(inputs['ETH/EUR'].venue_rules,venues.fetch_many.return_value['ETH/EUR'])
        f.market.collect.return_value=inputs
        result=await run_paired_cycle(f.root,f.market,now=at,operational_stop='paired_archive_stop')
        self.assertEqual(len(result['accounts']),8)
        for profile in f.manifest['profiles']:
            self.assertTrue(all(position.status.value=='closed' for position in f.positions(profile['directory'])))

    async def test_native_price_adapter_retains_received_ticker_only_when_opted_in(self):
        from types import SimpleNamespace
        exchange=SimpleNamespace(fetch_ticker=AsyncMock(return_value={'last':'80','bid':'79','ask':'81'}),
            fetch_ohlcv=AsyncMock(side_effect=OSError('invented unavailable history')),close=AsyncMock())
        async with PriceClient(exchange_factory=lambda config:exchange,quote_only_on_history_failure=True) as client:
            payload=await client.fetch('ETH/EUR')
        self.assertEqual(str(payload['bid']),'79');self.assertEqual(str(payload['ask']),'81')
        self.assertEqual(payload['candles'],[]);self.assertIsNotNone(payload['received_at'])
        self.assertNotIn('invented unavailable history',str(payload))
        async with PriceClient(exchange_factory=lambda config:exchange) as client:
            with self.assertRaises(OSError):await client.fetch('ETH/EUR')
        self.assertEqual(exchange.close.await_count,2)


if __name__=='__main__':unittest.main()
