"""Hand-calculated configured admission checks, not economic backtests."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from dataclasses import replace
import unittest

from hermes_trading import paired_cost_stress
from hermes_trading.market_data import MarketSnapshot,ClosedCandle


class ConfiguredStressFeasibilityTests(unittest.TestCase):
    def setUp(self):
        at=datetime(2026,1,1,tzinfo=timezone.utc)
        self.snapshot=MarketSnapshot('ETH/EUR','1h','available','evaluate','synthetic',
            ClosedCandle(at-timedelta(hours=1),at,D('10'),D('10'),D('10'),D('10'),None))
        self.strategy={'version':'1','position_size_r':'.5','stop_loss_pct':'2',
            'entry':{'fee':'taker'},'exit':{'take_profit_pct':'5'},
            'simulation':{'taker_fee_rate':'.008','maker_fee_rate':'.004','slippage_rate':'.001',
                'max_concurrent_positions':2,'minimum_order_quote':'1','minimum_order_quantity':'.01',
                'quantity_decimals':3}}
        self.order={'asset':'ETH/EUR','side':'buy','quantity':'.300','fill_price':'10.010',
            'fee_rate':'.008','slippage_rate':'.001','initial_stop_price':'9.80980'}

    def check_order(self,**kwargs):
        self.assertTrue(hasattr(paired_cost_stress,'configured_order_feasibility'),
                        'fixed-order report lacks configured admission checks')
        return paired_cost_stress.configured_order_feasibility(self.order,self.strategy,self.snapshot,
            equity=kwargs.get('equity','100'),spendable_cash=kwargs.get('cash','100'),
            existing_exposure=kwargs.get('exposure','0'),open_positions=kwargs.get('positions',0),
            remaining_quantity=kwargs.get('remaining',None))

    def test_affordable_original_risk_sized_entry_fails_doubled_risk_budget(self):
        # Original risk 3.003*(.02+.018)=.114114 < .125. Stress:
        # same q, gross3.006, risk3.006*(.02+.036)=.168336 > .125.
        result=self.check_order()
        self.assertEqual(result['failures'],['risk_budget'])
        self.assertEqual(D(result['risk_amount']),D('.168336'))
        self.assertEqual(D(result['risk_budget']),D('.125'))
        self.assertFalse(result['feasible'])

    def test_small_entry_passes_and_keeps_original_quantity(self):
        self.order['quantity']='.100'
        result=self.check_order()
        self.assertTrue(result['feasible'])
        self.assertEqual(D(result['quantity']),D('.100'))
        self.assertEqual(D(result['stressed_notional']),D('1.002'))

    def test_cash_exposure_order_and_concurrency_caps_are_independent(self):
        self.order['quantity']='.600'
        result=self.check_order(cash='1',exposure='9',positions=2)
        self.assertEqual(set(result['failures']),{'risk_budget','cash','symbol_exposure',
            'portfolio_exposure','max_order_quote','concurrent_positions'})

    def test_missing_original_stop_is_unknown_and_mismatch_is_source_error(self):
        self.order['quantity']='.100';self.order.pop('initial_stop_price')
        result=self.check_order()
        self.assertIsNone(result['feasible'])
        self.assertEqual(result['unknowns'],['original_initial_stop_missing'])
        self.order['initial_stop_price']='8'
        with self.assertRaisesRegex(ValueError,'original initial stop'):self.check_order()

    def test_quantity_precision_and_minimums_cannot_be_rounded_up(self):
        self.order['quantity']='.0001'
        result=self.check_order()
        self.assertEqual(set(result['failures']),{'quantity_precision','minimum_order_quantity','minimum_order_quote'})

    def test_protective_sell_checks_minimums_without_new_entry_limits(self):
        self.order.update(side='sell',fill_price='9.990',quantity='.100')
        self.order.pop('initial_stop_price')
        result=self.check_order(cash='-1',equity='1',exposure='100',positions=9)
        self.assertEqual(result['failures'],['minimum_order_quote'])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['unknowns'],[])

    def test_partial_sell_cannot_leave_untradeable_remainder(self):
        self.order.update(side='sell',fill_price='9.990',quantity='.200')
        result=self.check_order(remaining='.001')
        self.assertEqual(set(result['failures']),{'remaining_minimum_order_quote','remaining_minimum_order_quantity'})

    def test_configured_payoff_filter_must_clear_doubled_costs(self):
        self.order['quantity']='.100';self.strategy['exit']['take_profit_pct']='3'
        result=self.check_order()
        self.assertEqual(result['failures'],['configured_payoff_cost_filter'])

    def test_frozen_equity_does_not_turn_marked_gains_into_cash(self):
        self.order['quantity']='.200'
        result=self.check_order(equity='200',cash='1')
        self.assertEqual(result['failures'],['cash'])

    def test_missing_atr_history_is_unknown_not_a_feasible_trade(self):
        self.strategy['exit']['stop_atr']='2';self.order['quantity']='.100'
        result=self.check_order()
        self.assertIsNone(result['feasible'])
        self.assertIn('initial_stop_geometry_unavailable',result['unknowns'])

    def test_nonfinite_or_unsupported_doubled_costs_are_rejected(self):
        with self.assertRaises(ValueError):self.check_order(equity='NaN')
        self.strategy['simulation']['slippage_rate']='.6';self.order['slippage_rate']='.6'
        with self.assertRaises(ValueError):self.check_order()

    def test_atr_stop_uses_original_closed_history_and_stressed_entry_price(self):
        at=self.snapshot.candle.close_timestamp
        candles=[{'timestamp':(at-timedelta(hours=15-index)).isoformat(),
            'open':'10','high':'10.05','low':'9.95','close':'10','volume':'1'} for index in range(15)]
        self.snapshot=replace(self.snapshot,price={'candles':candles},
                              candle=replace(self.snapshot.candle,high=D('10.05'),low=D('9.95')))
        self.strategy['exit']['stop_atr']='2'
        self.order.update(quantity='.100',initial_stop_price='9.810')
        result=self.check_order()
        self.assertTrue(result['feasible'])
        self.assertEqual(D(result['stressed_initial_stop']),D('9.820'))
        self.assertEqual(D(result['risk_amount']),D('.056072'))
        # Appending an unclosed candle must never establish ATR feasibility.
        candles.append(dict(candles[-1],timestamp=at.isoformat()))
        result=self.check_order()
        self.assertIsNone(result['feasible'])
        self.assertIn('closed_history_provenance_unavailable',result['unknowns'])
