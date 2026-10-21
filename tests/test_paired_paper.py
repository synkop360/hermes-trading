import asyncio
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
import sqlite3
import hashlib
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import yaml

from hermes_trading.paired_paper import run_paired_cycle
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.market_data import ClosedCandle,MarketSnapshot
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _hash

class PairedPaperTests(unittest.IsolatedAsyncioTestCase):
    async def test_cost_reserve_is_frozen_and_reused_after_account_restart(self):
        from hermes_trading.paired_paper import process_paper_account
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'strategy.yaml'
            rules=yaml.safe_load(path.read_text())
            rules['simulation'].update(cost_stress_reserve=True,taker_fee_rate='.008',maker_fee_rate='.004',slippage_rate='.001')
            rules['exit']={'take_profit_pct':'5'}
            path.write_text(yaml.safe_dump(rules))
            profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        async def crash(*args,**kwargs):
            await process_paper_account(*args,**kwargs)
            raise RuntimeError('after reserved account fill')
        with self.assertRaisesRegex(RuntimeError,'reserved account'):
            await run_paired_cycle(self.root,self.market,now=self.now,process_account=crash)
        packet=self.packet();frozen=packet['budgets']['adaptive_100']['entry_cost_reserve']
        self.assertEqual(frozen['equity'],'100')
        self.assertEqual(frozen['status'],'ready')
        self.assertEqual((await run_paired_cycle(self.root,self.market,now=self.now+timedelta(seconds=1)))['status'],'complete')
        self.assertEqual(self.market.collect.await_count,1)
        self.assertEqual(self.packet()['budgets']['adaptive_100']['entry_cost_reserve'],frozen)
        for profile in self.manifest['profiles']:
            repo=StateRepository.open(self.root/profile['directory']/'trading.db')
            try:
                orders=repo.export_canonical_tables()['orders'];self.assertEqual(len(orders),1)
                order=orders[0]
                self.assertEqual(Decimal(order['fees']),Decimal(order['filled_quantity'])*Decimal(order['average_fill_price'])*Decimal('.008'))
            finally:repo.close()

    async def test_fractional_goal_values_are_losslessly_bound_before_processing(self):
        from hermes_trading.paired_paper import _encode,_load_yaml
        goal='assets: [ETH/EUR]\nmax_drawdown_pct: 3.0\nreturn_target: 0.26\nreflection_every: 5\n'
        for profile in self.manifest['profiles']:
            (self.root/profile['directory']/'goal.yaml').write_text(goal)
        result=await run_paired_cycle(self.root,self.market,now=self.now)
        self.assertEqual(result['status'],'complete')
        for profile in self.manifest['profiles']:
            name=profile['directory']
            expected=_hash(_encode(_load_yaml(self.root/name/'goal.yaml')))
            self.assertEqual(self.packet()['bindings'][name]['goal_hash'],expected)
        self.assertEqual(len(self.positions('adaptive_100')),1)

    async def test_pending_fractional_goal_replay_rejects_changed_value(self):
        from hermes_trading.paired_paper import process_paper_account
        for profile in self.manifest['profiles']:
            (self.root/profile['directory']/'goal.yaml').write_text('assets: [ETH/EUR]\nmax_drawdown: 0.03\n')
        async def crash(*args,**kwargs):
            await process_paper_account(*args,**kwargs)
            raise RuntimeError('after account transaction')
        with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market,now=self.now,process_account=crash)
        path=self.root/'adaptive_100/goal.yaml';original=path.read_bytes()
        path.write_text('assets: [ETH/EUR]\nmax_drawdown: 0.04\n')
        with self.assertRaises(ValueError):await run_paired_cycle(self.root,self.market,now=self.now)
        path.write_bytes(original)
        self.assertEqual((await run_paired_cycle(self.root,self.market,now=self.now))['status'],'complete')
        self.assertEqual(self.market.collect.await_count,1)

    async def test_one_supervisor_cost_source_is_frozen_for_all_adaptive_wallets(self):
        account=json.loads((self.root/'adaptive_100/wallet_account.json').read_text())
        start=account['activated_at']
        feed={'schema_version':1,'experiment_id':'paired-test','policy_hash':'a'*64,'currency':'EUR',
              'scope_start':start,'covered_through':self.now.isoformat(),'scope':'supervised_inference_only',
              'fixed_subscription_included':False,'invoice_reconciled':False,'source_hashes':{'ledger':'b'*64,'reviews':'c'*64},
              'model_usage':{'mode':'events','source':'single_writer_supervisor_usage_v1','coverage_start':start,
                             'coverage_end':self.now.isoformat(),'events':[]}}
        primary=self.root/'adaptive_100/supervisor_usage_snapshot.json';primary.write_text(json.dumps(feed))
        for wallet in (50,100,150,200):
            folder=self.root/f'adaptive_{wallet}'
            (folder/'wallet_cost_sources.json').write_text(json.dumps({'schema_version':1,'experiment_id':'paired-test','currency':'EUR','scope_start':start,
                'categories':{'hosting':{'mode':'recurring','daily_rate':'0','quality':'estimated','source':'test'},
                              'tools':{'mode':'recurring','daily_rate':'0','quality':'estimated','source':'test'},
                              'funding':{'mode':'spot_zero','quality':'actual','source':'unleveraged_spot_no_borrowing'},
                              'ai':{'mode':'supervised','source':'supervised_ai_with_subscription_v1','policy_hash':'a'*64,
                                    'subscription':{'daily_rate':'.24','quality':'estimated','source':'test_full_standalone_allocation'}}}}))
        from hermes_trading.wallet_cost_accrual import refresh_wallet_costs
        def refresh(path,**kwargs):
            result=refresh_wallet_costs(path,**kwargs)
            # Simulate another valid atomic import between account refreshes.
            changed=json.loads(json.dumps(feed));changed['source_hashes']['ledger']='d'*64
            primary.write_text(json.dumps(changed))
            return result
        with patch('hermes_trading.paired_paper.refresh_wallet_costs',side_effect=refresh):
            await run_paired_cycle(self.root,self.market,now=self.now)
        for wallet in (50,100,150,200):
            result=json.loads((self.root/f'adaptive_{wallet}/wallet_costs.json').read_text())
            self.assertEqual(result['supervisor_usage_hash'],_hash(feed))
            self.assertEqual(Decimal(result['totals']['ai']['amount']),Decimal('.02'))
        packet=self.packet()
        self.assertEqual(packet['supervisor_usage']['data'],feed)

    async def test_pending_batch_replays_original_supervisor_source_without_refresh(self):
        from hermes_trading.paired_paper import process_paper_account
        primary=self.root/'adaptive_100/supervisor_usage_snapshot.json'
        original={'test_source':'frozen_before_processing'};primary.write_text(json.dumps(original))
        async def crash(*args,**kwargs):
            await process_paper_account(*args,**kwargs)
            raise RuntimeError('after account transaction')
        with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market,process_account=crash)
        primary.write_text('{broken newer source')
        with patch('hermes_trading.paired_paper.refresh_wallet_costs',side_effect=AssertionError('must not refresh pending inputs')):
            result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(self.packet()['supervisor_usage']['data'],original)
        self.assertEqual(self.market.collect.await_count,1)

    def packet(self):
        db=sqlite3.connect(self.root/'adaptive_100/paper_publications.sqlite')
        try:return load_packet(db,db.execute('SELECT payload FROM paired_batches ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        finally:db.close()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.now=datetime.now(timezone.utc)-timedelta(seconds=2)
        strategy={'version':'1','entry':{'indicator':'rsi','threshold':'30','direction':'long'},'stop_loss_pct':'2','position_size_r':'.5',
                  'simulation':{'maker_fee_rate':'0','taker_fee_rate':'0','slippage_rate':'0','minimum_expected_edge_pct':'0','max_order_quote':'5'}}
        profiles=[]
        for arm in ('adaptive','control'):
            for wallet in (50,100,150,200):
                folder=self.root/f'{arm}_{wallet}';folder.mkdir()
                rules=dict(strategy)
                if arm=='control':rules['research']={'frozen':True}
                (folder/'strategy.yaml').write_text(yaml.safe_dump(rules)); (folder/'goal.yaml').write_text('assets: [ETH/EUR]')
                (folder/'wallet_account.json').write_text(json.dumps({'schema_version':1,'experiment_id':'paired-test','currency':'EUR','starting_balance':str(wallet),'activated_at':(self.now-timedelta(hours=2)).isoformat()}))
                (folder/'wallet_costs.json').write_text(json.dumps({'schema_version':1,'experiment_id':'paired-test','currency':'EUR','scope_start':(self.now-timedelta(hours=2)).isoformat(),'as_of':self.now.isoformat(),
                    'totals':{name:{'amount':'0','source':'explicit_test_zero','quality':'actual'} for name in ('hosting','ai','tools','funding')}}))
                profiles.append({'arm':arm,'wallet_eur':wallet,'directory':folder.name,'strategy_sha256':hashlib.sha256((folder/'strategy.yaml').read_bytes()).hexdigest()})
        self.manifest={'schema_version':1,'status':'paper_ready','assets':['ETH/EUR'],'timeframe':'1h','primary_wallet_eur':100,'profiles':profiles}
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        from venue_fixtures import venue_record
        self.market=AsyncMock()
        self.snapshot=MarketSnapshot('ETH/EUR','1h','available','evaluate','test',
            candle=ClosedCandle(self.now-timedelta(hours=1),self.now,Decimal('80'),Decimal('80'),Decimal('80'),Decimal('80'),Decimal('1')),
            price={'closes':tuple(Decimal('100') for _ in range(15))+(Decimal('80'),),'bid':Decimal('80'),'ask':Decimal('80'),'received_at':self.now.isoformat(),'source':'test'},
            venue_rules=venue_record('ETH/EUR',self.now))
        self.market.collect.return_value={'ETH/EUR':self.snapshot}
    def tearDown(self):self.tmp.cleanup()
    def positions(self,profile):
        repo=StateRepository.open(self.root/profile/'trading.db')
        try:return repo.portfolio_positions()
        finally:repo.close()
    async def test_one_collection_eight_isolated_accounts_and_shared_packet(self):
        result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,1)
        self.assertEqual(len(result['accounts']),8)
        self.assertEqual(result['status'],'complete')
        for wallet in (50,100,150,200):
            a=self.positions(f'adaptive_{wallet}'); c=self.positions(f'control_{wallet}')
            self.assertEqual(len(a),1);self.assertEqual(a,c)
        self.assertNotEqual(self.positions('adaptive_50')[0].quantity,self.positions('adaptive_100')[0].quantity)
        self.assertEqual(len({row['input_hash'] for row in result['accounts']}),1)

    async def test_common_packet_executes_at_ask_not_closed_candle(self):
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,price=dict(self.snapshot.price,ask=Decimal('81')))}
        result=await run_paired_cycle(self.root,self.market)
        for profile in self.manifest['profiles']:
            self.assertEqual(self.positions(profile['directory'])[0].entry_price,Decimal('81'))
            self.assertGreater(datetime.fromisoformat(self.positions(profile['directory'])[0].opened_at),self.now)

    async def test_maker_baseline_is_rejected_before_common_collection(self):
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'strategy.yaml';strategy=yaml.safe_load(path.read_text())
            strategy['entry']['fee']='maker';path.write_text(yaml.safe_dump(strategy))
            profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError,'maker'):await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,0)

    async def test_cost_producer_refreshes_new_batch_but_never_pending_replay(self):
        from hermes_trading.paired_paper import process_paper_account
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory'];account=json.loads((path/'wallet_account.json').read_text())
            sources={'schema_version':1,'experiment_id':'paired-test','currency':'EUR','scope_start':account['activated_at'],
                     'categories':{'hosting':{'mode':'recurring','daily_rate':'.24','source':'test_budget','quality':'estimated'},
                                   'tools':{'mode':'recurring','daily_rate':'0','source':'test_tools','quality':'estimated'},
                                   'funding':{'mode':'spot_zero','source':'unleveraged_spot_no_borrowing','quality':'actual'},
                                   'ai':{'mode':'events','source':'test_covered_usage','coverage_start':account['activated_at'],'coverage_end':self.now.isoformat(),'events':[]}}}
            (path/'wallet_cost_sources.json').write_text(json.dumps(sources))
        async def crash(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after first refreshed account')
        with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market,process_account=crash)
        for profile in self.manifest['profiles']:
            payload=json.loads((self.root/profile['directory']/'wallet_costs.json').read_text())
            self.assertGreater(Decimal(payload['totals']['hosting']['amount']),Decimal('0'))
            self.assertIn('source_hash',payload)
        with patch('hermes_trading.paired_paper.refresh_wallet_costs',side_effect=AssertionError('must not refresh pending costs')):
            result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(result['status'],'complete');self.assertEqual(self.market.collect.await_count,1)

    async def test_cost_journal_failure_does_not_starve_protective_exits(self):
        await run_paired_cycle(self.root,self.market)
        at=datetime.fromisoformat(self.positions('adaptive_50')[0].opened_at)+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=at),
                                            price=dict(self.snapshot.price,bid=Decimal('70'),received_at=at.isoformat()))}
        with patch('hermes_trading.paired_paper.refresh_wallet_costs',side_effect=sqlite3.OperationalError('cost journal busy')):
            result=await run_paired_cycle(self.root,self.market,now=at+timedelta(seconds=1))
        self.assertTrue(all(row['net_equity'] is None for row in result['accounts']))
        self.assertTrue(all(self.positions(profile['directory'])[0].status.value=='closed' for profile in self.manifest['profiles']))
    async def test_restart_reuses_committed_packet_and_strategy_without_recollecting(self):
        from hermes_trading.paired_paper import process_paper_account
        calls=[]
        async def interrupted(*args,**kwargs):
            result=await process_paper_account(*args,**kwargs);calls.append(args[0].name)
            if len(calls)==2:raise RuntimeError('injected crash after account commit')
            return result
        with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market,process_account=interrupted)
        newer=yaml.safe_load((self.root/'adaptive_100/strategy.yaml').read_text());newer['version']='2';newer['entry']['threshold']='1'
        (self.root/'adaptive_100/strategy.yaml').write_text(yaml.safe_dump(newer))
        result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,1)
        self.assertEqual(result['status'],'complete')
        for wallet in (50,100,150,200):
            self.assertEqual(self.positions(f'adaptive_{wallet}')[0].strategy_version,'1')
            self.assertEqual(len(self.positions(f'adaptive_{wallet}')),1)
    async def test_publication_after_candle_close_does_not_apply_retroactively(self):
        path=self.root/'adaptive_100';before_bytes=(path/'strategy.yaml').read_bytes();before=yaml.safe_load(before_bytes)
        (path/'history').mkdir();(path/'history/v0001.yaml').write_bytes(before_bytes)
        newer=json.loads(json.dumps(before));newer['version']='2';newer['entry']['threshold']='1'
        (path/'strategy.yaml').write_text(yaml.safe_dump(newer))
        db=sqlite3.connect(path/'paper_publications.sqlite')
        db.execute('CREATE TABLE publications (review_id TEXT PRIMARY KEY,request_hash TEXT,request TEXT,base_bytes TEXT,receipt TEXT)')
        receipt={'published_at':(self.now+timedelta(seconds=1)).isoformat(),'candidate_sha256':hashlib.sha256((path/'strategy.yaml').read_bytes()).hexdigest()}
        db.execute('INSERT INTO publications VALUES (?,?,?,?,?)',('test','unused',json.dumps({'base_strategy':before,'candidate':newer}),'unused',json.dumps(receipt)));db.commit();db.close()
        await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.positions('adaptive_100')[0].strategy_version,'1')

    async def test_delayed_replay_uses_original_costs_and_risk_budget(self):
        from hermes_trading.paired_paper import process_paper_account
        calls=[]
        async def interrupted(*args,**kwargs):
            result=await process_paper_account(*args,**kwargs);calls.append(args[0].name)
            if len(calls)==2:raise RuntimeError('crash')
            return result
        with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market,process_account=interrupted)
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'wallet_costs.json';costs=json.loads(path.read_text())
            costs['totals']['hosting']['amount']='45';path.write_text(json.dumps(costs))
        with patch('hermes_trading.paired_paper._now',side_effect=lambda value=None:value or self.now+timedelta(hours=1)):
            result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,1)
        self.assertEqual(self.positions('adaptive_50'),self.positions('control_50'))
        self.assertEqual(self.positions('adaptive_100'),self.positions('control_100'))
        self.assertTrue(all(row['net_equity']=='50.00000000' or Decimal(row['net_equity'])==row['wallet_eur'] for row in result['accounts']))
    async def test_all_controls_cannot_drift_together_or_initial_adaptive_change(self):
        for arm in ('control','adaptive'):
            for wallet in (50,100,150,200):
                path=self.root/f'{arm}_{wallet}/strategy.yaml';rules=yaml.safe_load(path.read_text());rules['entry']['threshold']='1';path.write_text(yaml.safe_dump(rules))
        with self.assertRaises(ValueError):await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,0)
    async def test_crash_between_assets_preserves_prebatch_equity_budget(self):
        from hermes_trading.positions import PositionEngine
        self.manifest['assets']=['ETH/EUR','XRP/EUR']
        for profile in self.manifest['profiles']:
            folder=self.root/profile['directory'];rules=yaml.safe_load((folder/'strategy.yaml').read_text())
            rules['simulation']['taker_fee_rate']='.008';rules['simulation']['slippage_rate']='.001'
            (folder/'strategy.yaml').write_text(yaml.safe_dump(rules));profile['strategy_sha256']=hashlib.sha256((folder/'strategy.yaml').read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        from venue_fixtures import venue_record
        self.market.collect.return_value={'ETH/EUR':self.snapshot,'XRP/EUR':replace(self.snapshot,asset='XRP/EUR',
            venue_rules=venue_record('XRP/EUR',self.now,minimum_quantity='0.002'))}
        original=PositionEngine.process_candle; interrupted=[]
        def crash_after_first_fill(engine,snapshot,*args,**kwargs):
            result=original(engine,snapshot,*args,**kwargs)
            if not interrupted:
                interrupted.append(True);raise RuntimeError('crash between asset commits')
            return result
        with patch.object(PositionEngine,'process_candle',crash_after_first_fill):
            with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market)
        await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,1)
        for wallet in (50,100,150,200):
            self.assertEqual(self.positions(f'adaptive_{wallet}'),self.positions(f'control_{wallet}'))
            self.assertEqual(len(self.positions(f'adaptive_{wallet}')),2)

    async def test_known_cost_measurement_failure_does_not_starve_later_accounts(self):
        await run_paired_cycle(self.root,self.market)
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'wallet_costs.json';costs=json.loads(path.read_text());costs['currency']='USD';path.write_text(json.dumps(costs))
        next_at=datetime.fromisoformat(self.positions('adaptive_50')[0].opened_at)+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at,open=Decimal('70'),low=Decimal('70'),close=Decimal('70')),
                                                          price={**self.snapshot.price,'bid':Decimal('70'),'received_at':next_at.isoformat()})}
        result=await run_paired_cycle(self.root,self.market,now=next_at+timedelta(seconds=1))
        self.assertEqual(len(result['accounts']),8)
        self.assertTrue(all(row['status']=='degraded' and row['net_equity'] is None for row in result['accounts']))
        self.assertTrue(all(self.positions(profile['directory'])[0].status.value=='closed' for profile in self.manifest['profiles']))
    async def test_malformed_cost_json_is_frozen_and_protective_exits_continue(self):
        await run_paired_cycle(self.root,self.market)
        for profile in self.manifest['profiles']:
            (self.root/profile['directory']/'wallet_costs.json').write_text('{not-json')
        next_at=datetime.fromisoformat(self.positions('adaptive_50')[0].opened_at)+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at,open=Decimal('70'),low=Decimal('70'),close=Decimal('70')),
                                                          price={**self.snapshot.price,'bid':Decimal('70'),'received_at':next_at.isoformat()})}
        result=await run_paired_cycle(self.root,self.market,now=next_at+timedelta(seconds=1))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(len(result['accounts']),8)
        self.assertTrue(all(row['status']=='degraded' and row['net_equity'] is None for row in result['accounts']))
        self.assertTrue(all(self.positions(profile['directory'])[0].status.value=='closed' for profile in self.manifest['profiles']))

    async def test_primary_archives_are_available_before_all_adaptive_budgets(self):
        await run_paired_cycle(self.root,self.market)
        path=self.root/'adaptive_100';before_bytes=(path/'strategy.yaml').read_bytes();before=yaml.safe_load(before_bytes)
        (path/'history/v0001.yaml').write_bytes(before_bytes)
        newer=json.loads(json.dumps(before));newer['version']='2';newer['entry']['threshold']='29';(path/'strategy.yaml').write_text(yaml.safe_dump(newer))
        published=self.now+timedelta(seconds=1)
        db=sqlite3.connect(path/'paper_publications.sqlite')
        db.execute('CREATE TABLE publications (review_id TEXT PRIMARY KEY,request_hash TEXT,request TEXT,base_bytes TEXT,receipt TEXT)')
        db.execute('INSERT INTO publications VALUES (?,?,?,?,?)',('test','unused',json.dumps({'base_strategy':before,'candidate':newer}),'unused',json.dumps({'published_at':published.isoformat(),'candidate_sha256':hashlib.sha256((path/'strategy.yaml').read_bytes()).hexdigest()})));db.commit();db.close()
        next_at=self.now+timedelta(seconds=2)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at),price={**self.snapshot.price,'received_at':next_at.isoformat()})}
        await run_paired_cycle(self.root,self.market,now=next_at+timedelta(seconds=1))
        db=sqlite3.connect(path/'paper_publications.sqlite');packet=load_packet(db,db.execute('SELECT payload FROM paired_batches ORDER BY rowid DESC LIMIT 1').fetchone()[0]);db.close()
        self.assertTrue(all(packet['budgets'][f'adaptive_{wallet}'].get('net_equity') is not None for wallet in (50,100,150,200)))

    async def test_nonprimary_drawdown_halts_pair_liquidates_and_remains_latched(self):
        await run_paired_cycle(self.root,self.market)
        cost_path=self.root/'adaptive_50/wallet_costs.json';cost=json.loads(cost_path.read_text());cost['totals']['hosting']['amount']='1.5';cost_path.write_text(json.dumps(cost))
        next_at=self.now+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at))}
        result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(result['risk_state']['halted_wallets'],[50])
        self.assertFalse(result['risk_state']['global_halted'])
        self.assertEqual(self.positions('adaptive_50')[0].status.value,'closed')
        self.assertEqual(self.positions('control_50')[0].status.value,'closed')
        self.assertEqual(self.positions('adaptive_100')[0].status.value,'open')
        record=json.loads((self.root/'adaptive_50/trades.jsonl').read_text().strip())
        self.assertEqual(record['evidence_classification'],'operational_risk_stop')
        cost['totals']['hosting']['amount']='2';cost_path.write_text(json.dumps(cost))
        next_at=self.now+timedelta(seconds=2)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at))}
        result=await run_paired_cycle(self.root,self.market)
        row=next(row for row in result['accounts'] if row['arm']=='adaptive' and row['wallet_eur']==50)
        self.assertEqual(Decimal(row['net_equity']),Decimal('48'))
        self.assertEqual(len(self.positions('adaptive_50')),1)
        self.assertEqual(result['risk_state']['halted_wallets'],[50])
    async def test_primary_drawdown_liquidates_all_accounts_and_blocks_steering(self):
        from hermes_trading.paper_publication import assert_no_pending_paired_batch
        await run_paired_cycle(self.root,self.market)
        path=self.root/'control_100/wallet_costs.json';cost=json.loads(path.read_text());cost['totals']['hosting']['amount']='3';path.write_text(json.dumps(cost))
        next_at=self.now+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at))}
        result=await run_paired_cycle(self.root,self.market)
        self.assertTrue(result['risk_state']['global_halted'])
        self.assertEqual(result['status'],'halted')
        self.assertTrue(all(self.positions(profile['directory'])[0].status.value=='closed' for profile in self.manifest['profiles']))
        with self.assertRaisesRegex(ValueError,'risk halt'):assert_no_pending_paired_batch(self.root/'adaptive_100')

    async def test_post_entry_cost_breach_closes_earlier_accounts_in_same_batch(self):
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'strategy.yaml';rules=yaml.safe_load(path.read_text());rules['simulation']['taker_fee_rate']='.008'
            path.write_text(yaml.safe_dump(rules));profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        path=self.root/'adaptive_100/wallet_costs.json';cost=json.loads(path.read_text());cost['totals']['hosting']['amount']='2.99';path.write_text(json.dumps(cost))
        result=await run_paired_cycle(self.root,self.market)
        self.assertTrue(result['risk_state']['global_halted'])
        self.assertTrue(all(position.status.value=='closed' for profile in self.manifest['profiles'] for position in self.positions(profile['directory'])))
        self.assertEqual(self.positions('adaptive_50')[0].status.value,'closed')
    async def test_liquidation_restart_is_idempotent_and_missing_bid_never_fabricates_fill(self):
        from hermes_trading.paired_paper import _liquidate_halted_account
        await run_paired_cycle(self.root,self.market)
        path=self.root/'adaptive_50';rules=yaml.safe_load((path/'strategy.yaml').read_text())
        no_bid=replace(self.snapshot,price={key:value for key,value in self.snapshot.price.items() if key!='bid'})
        self.assertFalse(_liquidate_halted_account(path,rules,{'ETH/EUR':no_bid},at=self.now+timedelta(seconds=1),batch_id='test-no-bid'))
        self.assertEqual(self.positions('adaptive_50')[0].status.value,'open')
        cost_path=path/'wallet_costs.json';cost=json.loads(cost_path.read_text());cost['totals']['hosting']['amount']='1.5';cost_path.write_text(json.dumps(cost))
        next_at=self.now+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at))}
        crashed=[]
        def interrupted(*args,**kwargs):
            result=_liquidate_halted_account(*args,**kwargs)
            if not crashed:crashed.append(True);raise RuntimeError('crash after risk close commit')
            return result
        with patch('hermes_trading.paired_paper._liquidate_halted_account',side_effect=interrupted):
            with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market)
        result=await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,2)
        self.assertEqual(result['risk_state']['halted_wallets'],[50])
        self.assertEqual(len((path/'trades.jsonl').read_text().splitlines()),1)
        self.assertEqual(self.positions('control_50')[0].status.value,'closed')

    async def test_failed_packet_commit_rolls_back_prebatch_halt(self):
        from hermes_trading.paired_paper import _connect
        path=self.root/'control_100/wallet_costs.json';cost=json.loads(path.read_text());cost['totals']['hosting']['amount']='3';path.write_text(json.dumps(cost))
        class FailingConnection:
            def __init__(self,db):self.db=db
            def execute(self,sql,*args):
                if sql.startswith('INSERT INTO paired_batches'):raise sqlite3.OperationalError('injected packet write failure')
                return self.db.execute(sql,*args)
            def __getattr__(self,key):return getattr(self.db,key)
        with patch('hermes_trading.paired_paper._connect',side_effect=lambda path:FailingConnection(_connect(path))):
            with self.assertRaises(sqlite3.OperationalError):await run_paired_cycle(self.root,self.market)
        db=sqlite3.connect(self.root/'adaptive_100/paper_publications.sqlite')
        state=json.loads(db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()[0])
        count=db.execute('SELECT COUNT(*) FROM paired_batches').fetchone()[0];db.close()
        self.assertFalse(state['global_halted'])
        self.assertEqual(count,0)
    async def test_late_final_valuation_breach_is_settled_before_observation(self):
        from hermes_trading.paired_paper import value_current_wallet
        await run_paired_cycle(self.root,self.market)
        next_at=self.now+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at))}
        calls={}
        def late_value(path,*args,**kwargs):
            value=value_current_wallet(path,*args,**kwargs)
            calls[path.name]=calls.get(path.name,0)+1
            if path.name=='control_100' and calls[path.name]>=3:value=dict(value,net_equity='97')
            return value
        with patch('hermes_trading.paired_paper.value_current_wallet',side_effect=late_value):
            result=await run_paired_cycle(self.root,self.market)
        self.assertTrue(result['risk_state']['global_halted'])
        self.assertTrue(all(self.positions(profile['directory'])[0].status.value=='closed' for profile in self.manifest['profiles']))

    async def test_failed_account_checkpoint_rolls_back_risk_but_replays_committed_fill(self):
        from hermes_trading.paired_paper import _connect
        for profile in self.manifest['profiles']:
            path=self.root/profile['directory']/'strategy.yaml';rules=yaml.safe_load(path.read_text());rules['simulation']['taker_fee_rate']='.008';path.write_text(yaml.safe_dump(rules))
            profile['strategy_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        class FailingConnection:
            def __init__(self,db):self.db=db
            def execute(self,sql,*args):
                if sql.startswith('UPDATE paired_batches SET completed'):raise sqlite3.OperationalError('injected account checkpoint failure')
                return self.db.execute(sql,*args)
            def __getattr__(self,key):return getattr(self.db,key)
        with patch('hermes_trading.paired_paper._connect',side_effect=lambda path:FailingConnection(_connect(path))):
            with self.assertRaises(sqlite3.OperationalError):await run_paired_cycle(self.root,self.market)
        db=sqlite3.connect(self.root/'adaptive_100/paper_publications.sqlite')
        state=json.loads(db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()[0]);completed=json.loads(db.execute('SELECT completed FROM paired_batches').fetchone()[0]);db.close()
        self.assertEqual(Decimal(state['accounts']['adaptive_50']['net_equity']),Decimal('50'))
        self.assertEqual(completed,{})
        self.assertEqual(len(self.positions('adaptive_50')),1)
        await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,1)
        self.assertEqual(self.positions('adaptive_50'),self.positions('control_50'))

    async def test_settled_journal_append_failure_replays_one_observation_and_close(self):
        from hermes_trading.paired_paper import _journal_final_account
        await run_paired_cycle(self.root,self.market)
        path=self.root/'adaptive_50/wallet_costs.json';cost=json.loads(path.read_text());cost['totals']['hosting']['amount']='1.5';path.write_text(json.dumps(cost))
        next_at=self.now+timedelta(seconds=1)
        self.market.collect.return_value={'ETH/EUR':replace(self.snapshot,candle=replace(self.snapshot.candle,close_timestamp=next_at))}
        crashed=[]
        def interrupted(*args,**kwargs):
            result=_journal_final_account(*args,**kwargs)
            if not crashed:crashed.append(True);raise RuntimeError('crash after settled observation append')
            return result
        with patch('hermes_trading.paired_paper._journal_final_account',side_effect=interrupted):
            with self.assertRaises(RuntimeError):await run_paired_cycle(self.root,self.market)
        db=sqlite3.connect(self.root/'adaptive_100/paper_publications.sqlite');settlements=db.execute('SELECT COUNT(*) FROM paired_settlements').fetchone()[0];db.close()
        self.assertEqual(settlements,2)
        await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,2)
        db=sqlite3.connect(self.root/'adaptive_50/wallet_equity.sqlite');count=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0];db.close()
        self.assertEqual(count,2)
        self.assertEqual(len((self.root/'adaptive_50/trades.jsonl').read_text().splitlines()),1)

    async def test_draft_and_path_escape_are_rejected_before_collection(self):
        self.manifest['status']='prepared_draft_not_activated';(self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):await run_paired_cycle(self.root,self.market)
        self.manifest['status']='paper_ready';self.manifest['profiles'][0]['directory']='../outside'
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):await run_paired_cycle(self.root,self.market)
        self.assertEqual(self.market.collect.await_count,0)

if __name__=='__main__':unittest.main()
