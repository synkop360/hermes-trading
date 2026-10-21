import unittest
from hermes_trading.crowding_study import direction, discrete_return, HOUR, DAY

class CrowdingTests(unittest.TestCase):
    def test_threshold_excludes_signal_day(self):
        d={i*DAY:.001 for i in range(61)};d[60*DAY]=.002
        self.assertEqual(direction(d,61*DAY,-.01),-1)
        self.assertEqual(direction(d,61*DAY,.01),0)
        d[61*DAY]=100
        self.assertEqual(direction(d,61*DAY,-.01),-1)

    def test_negative_funding_reversal_long(self):
        d={i*DAY:-.001 for i in range(61)};d[60*DAY]=-.002
        self.assertEqual(direction(d,61*DAY,.01),1)

    def test_gap_rejects_feature(self):
        d={i*DAY:.001 for i in range(61)};del d[7*DAY]
        self.assertIsNone(direction(d,61*DAY,-.01))

    def test_short_price_and_funding_sign(self):
        p={0:[100]*4,HOUR:[90]*4,2*HOUR:[80]*4}
        r=discrete_return(p,p,{HOUR:.01},0,2*HOUR,-1,fee=0,slip=0)
        self.assertAlmostEqual(r['price_return'],.2)
        self.assertAlmostEqual(r['funding_return'],.009)
        self.assertAlmostEqual(r['net_return'],.209)

    def test_long_pays_positive_funding_and_both_notional_fees(self):
        p={0:[100]*4,HOUR:[110]*4,2*HOUR:[120]*4}
        r=discrete_return(p,p,{HOUR:.01},0,2*HOUR,1,fee=.001,slip=0)
        self.assertAlmostEqual(r['net_return'],.2-.011-.0022)

    def test_future_funding_not_charged(self):
        p={0:[100]*4,HOUR:[100]*4}
        r=discrete_return(p,p,{2*HOUR:100},0,HOUR,1,fee=0,slip=0)
        self.assertEqual(r['net_return'],0)

    def test_missing_held_hour_rejected(self):
        p={0:[100]*4,2*HOUR:[100]*4}
        with self.assertRaises(ValueError):discrete_return(p,p,{},0,2*HOUR,1)
