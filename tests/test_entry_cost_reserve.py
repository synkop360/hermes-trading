from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import unittest
import json,tempfile
from pathlib import Path
import yaml

from hermes_trading.entry_cost_reserve import reserved_quantity
from hermes_trading.market_data import ClosedCandle,MarketSnapshot
from hermes_trading.paired_cost_stress import configured_order_feasibility


class EntryCostReserveTests(unittest.TestCase):
    def setUp(self):
        at=datetime(2026,1,1,tzinfo=timezone.utc)
        self.snapshot=MarketSnapshot('ETH/EUR','1h','available','evaluate','synthetic',
            ClosedCandle(at-timedelta(hours=1),at,D('10'),D('10'),D('10'),D('10'),None))
        self.strategy={'position_size_r':'.5','stop_loss_pct':'2','entry':{'fee':'taker'},
            'exit':{'take_profit_pct':'5'},'simulation':{'cost_stress_reserve':True,
            'taker_fee_rate':'.008','slippage_rate':'.001','quantity_decimals':8,'max_concurrent_positions':2}}
        self.context={'equity':'100','cash':'100','exposure':'0','positions':0}

    def reserve(self,quantity='.32861874',**changes):
        return reserved_quantity(self.strategy,self.snapshot,D('10.010'),D(quantity),**dict(self.context,**changes))

    def test_hand_calculated_two_percent_stop_reserves_doubled_cost_risk(self):
        # Stress fill10.02; risk per unit10.02*(.02+.036)=.56112.
        # Budget .125 -> q floor .22276874, instead of ordinary .32861874.
        self.assertEqual(self.reserve(),D('.22276874'))
        self.assertEqual(self.strategy['simulation']['taker_fee_rate'],'.008')

    def test_sequential_cash_exposure_and_concurrency_limit_quantity(self):
        self.assertLess(self.reserve(cash='.5'),self.reserve())
        self.assertLess(self.reserve(exposure='9.5'),self.reserve())
        self.assertEqual(self.reserve(positions=2),0)
        self.assertEqual(self.reserve(equity='0'),0)

    def test_unknown_closed_history_and_minimums_block_without_rounding_up(self):
        self.strategy['exit']['stop_atr']='2.5'
        self.assertEqual(self.reserve(),0)
        self.strategy['exit'].pop('stop_atr')
        self.strategy['simulation']['minimum_order_quantity']='.3'
        self.assertEqual(self.reserve(),0)

    def test_reserved_orders_obey_independent_feasibility_and_never_increase_size(self):
        for equity in (D('50'),D('100'),D('150'),D('200')):
            for cash in (D('0'),D('.1'),D('1'),D('100')):
                for exposure in (D('0'),D('1'),D('5'),D('15')):
                    q=self.reserve(equity=equity,cash=cash,exposure=exposure)
                    self.assertTrue(0<=q<=D('.32861874'))
                    if q:
                        check=configured_order_feasibility({'asset':'ETH/EUR','side':'buy','quantity':str(q),
                            'fill_price':'10.010','fee_rate':'.008','slippage_rate':'.001',
                            'initial_stop_price':'9.80980'},self.strategy,self.snapshot,equity=equity,
                            spendable_cash=cash,existing_exposure=exposure,open_positions=0)
                        self.assertTrue(check['feasible'],check)

    def test_reserve_does_not_mutate_strategy_or_frozen_inputs(self):
        before=deepcopy((self.strategy,self.context))
        self.reserve();self.reserve(cash='.5')
        self.assertEqual((self.strategy,self.context),before)


class EntryReserveExecutionTests(unittest.TestCase):
    def setUp(self):
        EntryCostReserveTests.setUp(self)
        from hermes_trading.persistence.repository import StateRepository
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.at=self.snapshot.candle.close_timestamp+timedelta(seconds=2)
        self.strategy.update(version='1')
        self.strategy['simulation']['max_total_exposure_pct']='.03'
        (self.root/'strategy.yaml').write_text(yaml.safe_dump(self.strategy))
        account={'schema_version':1,'experiment_id':'reserve-test','currency':'EUR','starting_balance':'100',
            'activated_at':(self.at-timedelta(minutes=1)).isoformat()}
        (self.root/'wallet_account.json').write_text(json.dumps(account))
        self.write_costs(account['activated_at'])
        self.repo=StateRepository.open(self.root/'trading.db');self.repo.migrate()
        self.snapshots={asset:self.quote(asset) for asset in ('ETH/EUR','BTC/EUR')}
        from hermes_trading.wallet_observer import value_current_wallet
        self.valuation=value_current_wallet(self.root,self.repo,self.snapshots,self.strategy,now=self.at)
        self.context=self.valuation['entry_cost_reserve']

    def tearDown(self):self.repo.close();self.tmp.cleanup()

    def write_costs(self,start,hosting='0'):
        totals={name:{'amount':hosting if name=='hosting' else '0','quality':'actual','source':'synthetic'}
            for name in ('hosting','ai','tools','funding')}
        (self.root/'wallet_costs.json').write_text(json.dumps({'schema_version':1,'experiment_id':'reserve-test',
            'currency':'EUR','scope_start':start,'as_of':self.at.isoformat(),'totals':totals}))

    def quote(self,asset,bid='9.99'):
        from dataclasses import replace
        from venue_fixtures import venue_record
        return replace(self.snapshot,asset=asset,price={'bid':bid,'ask':'10',
            'received_at':(self.at-timedelta(seconds=1)).isoformat(),'source':'synthetic'},
            venue_rules=venue_record(asset,self.at))

    def engine(self,context=True):
        from hermes_trading.positions import PositionEngine
        from hermes_trading.entry_contracts import EntryContractResolver
        return PositionEngine(self.repo,equity=D(self.valuation['net_equity']),wallet_cash_capital=D('100'),
            prospective_at=self.at,entry_cost_reserve=self.context if context else None,reserve_state_dir=self.root,
            entry_contract_resolver=EntryContractResolver(self.root,self.strategy))

    def enter(self,asset,engine=None):
        from hermes_trading.strategy import StrategySignal
        snapshot=self.snapshots[asset]
        signal=StrategySignal('enter',asset,'synthetic_entry',D('10'),snapshot.candle_identity.candle_id)
        return (engine or self.engine()).process_candle(snapshot,self.strategy,signal_override=signal)

    def test_actual_fee_and_stop_unchanged_with_reserved_entry(self):
        entry=self.enter('ETH/EUR')
        self.assertEqual(entry.position.quantity,D('.22276874'))
        self.assertEqual(entry.orders[0].fees,entry.position.quantity*D('10.010')*D('.008'))
        self.assertEqual(entry.position.stop_price,D('9.80980'))

    def test_missing_closed_stop_history_rejects_entry_without_aborting_processing(self):
        self.strategy['exit']['stop_atr']='2.5'
        result=self.enter('ETH/EUR')
        self.assertIsNone(result.position)
        self.assertEqual(result.reason,'entry_cost_reserve_unavailable')

    def test_restart_reconstructs_sequential_stress_exposure_and_cash(self):
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.entry_cost_reserve import current_stress_book
        first=self.enter('ETH/EUR')
        self.repo.close();self.repo=StateRepository.open(self.root/'trading.db');self.repo.migrate()
        second=self.enter('BTC/EUR')
        book=current_stress_book(self.root,'100',self.at)
        self.assertLess(second.position.quantity,first.position.quantity)
        gross=sum((p['quantity']*p['stress_entry'] for p in book.holdings.values()),D(0))
        self.assertLessEqual(gross,D('3'))
        self.assertEqual(self.context['equity'],'100')
        replay=self.enter('BTC/EUR')
        self.assertEqual(replay.orders,())

    def test_missing_reserve_blocks_entries_but_retains_protective_exit(self):
        self.assertIsNone(self.enter('ETH/EUR',self.engine(context=False)).position)
        # The no-entry decision consumes this candle; use an untouched symbol.
        entry=self.enter('BTC/EUR');self.assertIsNotNone(entry.position)
        from dataclasses import replace
        snap=replace(self.snapshots['BTC/EUR'],price={'bid':'9','ask':'9.01',
            'received_at':(self.at+timedelta(seconds=1)).isoformat(),'source':'synthetic'})
        self.at+=timedelta(seconds=2)
        result=self.engine(context=False).process_candle(snap,self.strategy)
        self.assertEqual(result.reason,'stop_loss')
        self.assertEqual(result.orders[0].fees,result.orders[0].filled_quantity*result.orders[0].average_fill_price*D('.008'))

    def test_existing_inventory_and_accrued_costs_reduce_frozen_stress_nav(self):
        self.enter('ETH/EUR')
        account=json.loads((self.root/'wallet_account.json').read_text())
        self.write_costs(account['activated_at'],hosting='1')
        from hermes_trading.wallet_observer import value_current_wallet
        value=value_current_wallet(self.root,self.repo,self.snapshots,self.strategy,now=self.at)
        self.assertEqual(value['entry_cost_reserve']['status'],'ready')
        self.assertLess(D(value['entry_cost_reserve']['equity']),D(value['net_equity']))
        self.assertEqual(value['entry_cost_reserve']['operating_costs'],'1')
