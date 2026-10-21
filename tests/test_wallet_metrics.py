from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.wallet_metrics import (
    CostAccrual, LiquidationMark, WalletJournal, value_spot_wallet, rolling_wallet_metrics,
)

START=datetime(2026,9,1,tzinfo=timezone.utc)
NOW=START+timedelta(days=30)
D=Decimal


class ValuationTests(unittest.TestCase):
    def position(self,identity,status='open',quantity='1',pnl='0',fees='.1'):
        return {'position_id':identity,'asset':'BTC/EUR','status':status,'quantity':quantity,
                'entry_price':'10','realized_pnl':pnl,'fees':fees,
                'opened_at':START.isoformat(),'closed_at':NOW.isoformat() if status=='closed' else None}

    def costs(self):
        return {name:CostAccrual(D('0'),'EUR','explicit_test_zero','actual') for name in ('hosting','ai','tools','funding')}

    def value(self,positions,marks=None,costs=None):
        return value_spot_wallet(D('100'),'EUR',positions,
            {'BTC/EUR':LiquidationMark(D('11'),NOW,D('.01'),D('0'),'test_bid')} if marks is None else marks,
            self.costs() if costs is None else costs,now=NOW)

    def test_open_inventory_and_net_closed_pnl_reconcile_without_double_charging_fees(self):
        result=self.value([self.position('closed','closed',pnl='2',fees='.3'), self.position('open')])
        self.assertEqual(D(result['net_equity']),D('102.79'))
        self.assertEqual(D(result['trading_cash']),D('91.9'))
        self.assertEqual(D(result['open_liquidation_value']),D('10.89'))

    def test_partial_close_and_remaining_entry_fee_are_each_counted_once(self):
        result=self.value([self.position('partial','closed',quantity='.5',pnl='.395',fees='.105'),
                           self.position('remainder',quantity='.5',fees='.05')])
        self.assertEqual(D(result['net_equity']),D('100.79'))
        self.assertEqual(D(result['net_equity']),D(self.value([self.position('whole')])['net_equity']))

    def test_missing_cost_is_unknown_and_not_zero_profit(self):
        costs=self.costs(); costs['hosting']=CostAccrual(None,'EUR','unknown','missing')
        result=self.value([],costs=costs)
        self.assertIsNone(result['net_equity'])
        self.assertEqual(D(result['known_cost_equity']),D('100'))
        self.assertIn('cost:hosting',result['missing'])

    def test_missing_quote_cannot_hide_open_loss(self):
        result=self.value([self.position('open')],marks={})
        self.assertIsNone(result['net_equity'])
        self.assertIsNone(result['known_cost_equity'])

    def test_negative_open_pnl_and_operating_cost_reduce_wallet_equity(self):
        costs=self.costs(); costs['ai']=CostAccrual(D('1'),'EUR','actual_test','actual')
        mark=LiquidationMark(D('8'),NOW,D('0'),D('0'),'test_bid')
        self.assertEqual(D(self.value([self.position('open')],{'BTC/EUR':mark},costs)['net_equity']),D('96.9'))

    def test_bad_identity_currency_numbers_and_stale_marks_are_rejected(self):
        for rows,marks,costs in (
            ([self.position('same'),self.position('same')],None,None),
            ([dict(self.position('open'),asset='BTC/USD')],None,None),
            ([dict(self.position('open'),quantity=True)],None,None),
            ([self.position('open')],{'BTC/EUR':LiquidationMark(D('11'),START,D('0'),D('0'),'old_bid')},None),
            ([self.position('open')],{'BTC/EUR':{'bid':'11'}},None),
            ([],None,{'ai':CostAccrual(D('1'),'USD','wrong_currency','actual')}),
        ):
            with self.subTest(rows=rows),self.assertRaises(ValueError): self.value(rows,marks,costs)

    def test_partial_realization_preserves_equity_across_wallets_and_fee_models(self):
        for capital in (D('50'),D('100'),D('150'),D('200')):
            for quantity in (D('.01'),D('.5'),D('1')):
                for fraction in (D('.25'),D('.5'),D('.75')):
                    for exit_fee in (D('0'),D('.004'),D('.008')):
                        entry_fee=quantity*D('10')*D('.004'); fill=D('11')*D('.999')
                        closed_quantity=quantity*fraction
                        exit_cost=closed_quantity*fill*exit_fee
                        closed_pnl=closed_quantity*(fill-D('10'))-entry_fee*fraction-exit_cost
                        whole=[self.position('whole',quantity=str(quantity),fees=str(entry_fee))]
                        split=[self.position('closed','closed',quantity=str(closed_quantity),pnl=str(closed_pnl),fees=str(entry_fee*fraction+exit_cost)),
                               self.position('remaining',quantity=str(quantity-closed_quantity),fees=str(entry_fee*(1-fraction)))]
                        mark={'BTC/EUR':LiquidationMark(D('11'),NOW,exit_fee,D('.001'),'test_bid')}
                        original=value_spot_wallet(capital,'EUR',whole,mark,self.costs(),now=NOW)
                        partial=value_spot_wallet(capital,'EUR',split,mark,self.costs(),now=NOW)
                        self.assertEqual(D(original['net_equity']),D(partial['net_equity']))

    def test_future_nonfinite_and_missing_fee_models_cannot_become_known_equity(self):
        for mark in (LiquidationMark(D('11'),NOW+timedelta(seconds=1),D('0'),D('0'),'future'),
                     LiquidationMark(D('NaN'),NOW,D('0'),D('0'),'bad_bid'),
                     LiquidationMark(D('11'),NOW,D('-1'),D('0'),'bad_fee')):
            with self.subTest(mark=mark),self.assertRaises(ValueError): self.value([self.position('open')],{'BTC/EUR':mark})
        missing=LiquidationMark(D('11'),NOW,None,D('0'),'unknown_fee')
        self.assertIsNone(self.value([self.position('open')],{'BTC/EUR':missing})['net_equity'])

    def test_executed_fee_ledger_cannot_be_subtracted_from_net_pnl_again(self):
        costs=self.costs(); costs['kraken_fee']=CostAccrual(D('1'),'EUR','executed','actual')
        with self.assertRaises(ValueError): self.value([],costs=costs)


class RollingMetricsTests(unittest.TestCase):
    def points(self,values):
        return [{'at':(START+timedelta(days=i)).isoformat(),'currency':'EUR','net_equity':str(value),
                 'external_flow':'0','starting_balance':'100'} for i,value in enumerate(values)]

    def test_calendar_return_and_drawdown_use_wallet_equity(self):
        points=self.points([100]+[110]*14+[88]+[105]*15)
        report=rolling_wallet_metrics(points,'EUR',now=NOW,max_gap=timedelta(days=1))
        self.assertEqual(report['status'],'measured')
        self.assertEqual(D(report['return_30d']),D('.05'))
        self.assertEqual(D(report['max_drawdown_30d']),D('.2'))
        self.assertEqual(report['daily_return_count'],30)

    def test_missing_start_day_gap_and_unknown_equity_do_not_invent_history(self):
        full=self.points([100]*31)
        for points in (full[1:],full[:15]+full[16:],full[:15]+[{**full[15],'net_equity':None}]+full[16:]):
            with self.subTest(points=points):
                report=rolling_wallet_metrics(points,'EUR',now=NOW,max_gap=timedelta(days=1))
                self.assertEqual(report['status'],'incomplete')
                self.assertIsNone(report['return_30d'])

    def test_constant_returns_do_not_receive_an_invented_sharpe(self):
        report=rolling_wallet_metrics(self.points([100]*31),'EUR',now=NOW,max_gap=timedelta(days=1))
        self.assertIsNone(report['annualized_daily_return_ratio_zero_reference'])

    def test_one_observation_cannot_be_reused_as_a_month_of_daily_endpoints(self):
        with self.assertRaises(ValueError):
            rolling_wallet_metrics(self.points([100]),'EUR',now=NOW,boundary_tolerance=timedelta(days=30))

    def test_insolvency_is_reported_without_dividing_through_zero(self):
        report=rolling_wallet_metrics(self.points([100]*29+[0,-1]),'EUR',now=NOW,max_gap=timedelta(days=1))
        self.assertEqual(report['status'],'insolvent')
        self.assertEqual(D(report['return_30d']),D('-1.01'))

    def test_external_flows_and_mixed_currency_are_not_accepted_as_returns(self):
        for changed in ({'external_flow':'50'},{'currency':'USD'}):
            rows=self.points([100]*31); rows[10].update(changed)
            with self.assertRaises(ValueError): rolling_wallet_metrics(rows,'EUR',now=NOW,max_gap=timedelta(days=1))

    def test_journal_survives_restart_and_refuses_rewriting_or_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'wallet.sqlite'
            config={'experiment_id':'test','currency':'EUR','starting_balance':'100','activated_at':START.isoformat()}
            with WalletJournal(path,config) as journal:
                journal.append(self.points([100])[0],now=NOW)
                self.assertFalse(journal.append(self.points([100])[0],now=NOW))
                with self.assertRaises(ValueError): journal.append(self.points([101])[0],now=NOW)
            with WalletJournal(path,config) as journal: self.assertEqual(len(journal.observations()),1)
            with self.assertRaises(ValueError): WalletJournal(path,{**config,'starting_balance':'200'})

    def test_journal_rejects_other_wallet_capital_and_rolls_back_failed_insert(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            config={'experiment_id':'test','currency':'EUR','starting_balance':'100','activated_at':START.isoformat()}
            with WalletJournal(Path(tmp)/'wallet.sqlite',config) as journal:
                with self.assertRaises(ValueError): journal.append({**self.points([100])[0],'starting_balance':'200'},now=NOW)
                journal.db.execute("CREATE TEMP TRIGGER fail_insert BEFORE INSERT ON observations "
                                   "BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
                with self.assertRaises(sqlite3.IntegrityError): journal.append(self.points([100])[0],now=NOW)
                self.assertEqual(journal.observations(),[])
                journal.db.execute('DROP TRIGGER fail_insert')
                self.assertTrue(journal.append(self.points([100])[0],now=NOW))


if __name__=='__main__': unittest.main()
