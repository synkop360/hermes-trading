from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import unittest

from hermes_trading.market_data import ClosedCandle


def daily(prices):
    start=datetime(2020,1,1,tzinfo=UTC)
    return [ClosedCandle(start+timedelta(days=i),start+timedelta(days=i+1),D(p),D(p),D(p),D(p),D(1)) for i,p in enumerate(prices)]


class DailyBreakoutTests(unittest.TestCase):
    def test_breakout_excludes_signal_day_high(self):
        from hermes_trading.daily_breakout_study import features
        bars=daily(range(100,205));bars[-1]=replace(bars[-1],high=D(999))
        self.assertTrue(features(bars,104)['signal'])
        bars[-2]=replace(bars[-2],high=D(205))
        self.assertFalse(features(bars,104)['signal'])

    def test_equal_high_is_not_breakout(self):
        from hermes_trading.daily_breakout_study import features
        bars=daily(range(100,205));bars[-2]=replace(bars[-2],high=D(204))
        self.assertFalse(features(bars,104)['signal'])

    def test_requires_105_contiguous_days(self):
        from hermes_trading.daily_breakout_study import features
        self.assertIsNone(features(daily(range(100,204)),103))
        bars=daily(range(100,206));del bars[50]
        self.assertIsNone(features(bars,104))

    def test_future_changes_do_not_change_signal(self):
        from hermes_trading.daily_breakout_study import features
        bars=daily(range(100,210));first=features(bars,104)
        bars[105:]=[replace(c,close=D(1),high=D(1),low=D(1)) for c in bars[105:]]
        self.assertEqual(features(bars,104),first)

    def test_quarter_interval_rejects_sparse_clusters(self):
        from hermes_trading.daily_breakout_study import quarter_interval
        rows=[{'signal_at':'2024-01-01T00:00:00+00:00','net_return':.01} for _ in range(30)]
        self.assertIsNone(quarter_interval(rows,'net_return'))


if __name__=='__main__':unittest.main()
