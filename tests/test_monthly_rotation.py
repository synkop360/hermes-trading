import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hermes_trading.market_data import ClosedCandle
from hermes_trading.monthly_rotation import choose, simulate


def bars(start, values):
    return [ClosedCandle(start+timedelta(days=i),start+timedelta(days=i+1),*(Decimal(str(v)) for _ in range(4)),Decimal(1)) for i,v in enumerate(values)]


class MonthlyRotationTests(unittest.TestCase):
    def test_choice_uses_completed_history_and_positive_returns(self):
        start=datetime(2020,1,1,tzinfo=UTC)
        data={'BTC':bars(start,range(100,200)), 'ETH':bars(start,range(200,300))}
        self.assertEqual(choose(data,91),'BTC')
        data['ETH'][91:]=bars(start+timedelta(days=91),[999999]*9)
        self.assertEqual(choose(data,91),'BTC')
        self.assertIsNone(choose(data,90))

    def test_flat_or_declining_returns_hold_cash(self):
        start=datetime(2020,1,1,tzinfo=UTC)
        self.assertIsNone(choose({'BTC':bars(start,[100]*92),'ETH':bars(start,range(200,108,-1))},91))

    def test_history_gap_prevents_selection(self):
        start=datetime(2020,1,1,tzinfo=UTC)
        data={'BTC':bars(start,range(100,193)),'ETH':bars(start,range(200,293))}
        del data['BTC'][30]
        with self.assertRaises(ValueError):simulate(data,start+timedelta(days=91),start+timedelta(days=92))

    def test_retaining_selection_does_not_recharge_fees(self):
        start=datetime(2020,1,1,tzinfo=UTC)
        # Increasing pre-entry history, constant price after Apr1. Both later monthly signals stay positive.
        values=list(range(100,191))+[191]*91
        data={'BTC':bars(start,values),'ETH':bars(start,[100]*len(values))}
        result=simulate(data,datetime(2020,4,1,tzinfo=UTC),datetime(2020,7,1,tzinfo=UTC))
        expected=10000*(1-.001)*(1-.008)/((1+.001)*(1+.008))
        self.assertAlmostEqual(result['final_equity'],expected)
        self.assertEqual(len(result['trades']),1)
        self.assertEqual(result['trades'][0]['exit_reason'],'end_of_period')

    def test_no_signal_cash_has_no_cost(self):
        start=datetime(2020,1,1,tzinfo=UTC)
        data={asset:bars(start,[100]*182) for asset in ('BTC','ETH')}
        result=simulate(data,datetime(2020,4,1,tzinfo=UTC),datetime(2020,7,1,tzinfo=UTC))
        self.assertEqual(result['final_equity'],10000)
        self.assertEqual(result['trades'],[])


if __name__=='__main__':unittest.main()
