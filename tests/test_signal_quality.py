from dataclasses import replace
from decimal import Decimal as D
import unittest

from tests.test_return_validation import bars


class SignalQualityTests(unittest.TestCase):
    def test_outcome_starts_at_next_open_and_uses_exact_horizon(self):
        from hermes_trading.signal_quality import outcome
        cs = bars([10,100,110,120,130,999])
        r = outcome(cs,0,4)
        self.assertAlmostEqual(r['gross_return'],.3)
        self.assertAlmostEqual(r['net_return'],130*.999*.992/(100*1.001*1.008)-1)
        self.assertAlmostEqual(r['favorable_excursion'],.3)

    def test_gap_and_incomplete_horizon_are_excluded(self):
        from hermes_trading.signal_quality import outcome
        cs=bars([100]*8)
        self.assertIsNone(outcome(cs[:3]+cs[4:],0,4))
        self.assertIsNone(outcome(cs,5,4))

    def test_nonoverlap_uses_elapsed_time(self):
        from hermes_trading.signal_quality import spaced_indices
        cs=bars([100]*105)
        self.assertEqual(spaced_indices(cs,[0,1,95,96,100],24),[0,96])

    def test_matching_uses_only_preceding_same_bucket(self):
        from hermes_trading.signal_quality import preceding_control
        self.assertEqual(preceding_control([2,5,8,12],8),5)
        self.assertIsNone(preceding_control([8,12],8))

    def test_control_can_require_disjoint_24h_window(self):
        from hermes_trading.signal_quality import preceding_control
        self.assertEqual(preceding_control([1,4,99],100,minimum_distance=96),4)

    def test_interval_requires_four_occupied_weeks(self):
        from datetime import UTC,datetime
        from hermes_trading.signal_quality import paired_interval
        rows=[{'signal_at':'2026-01-05T00:00:00+00:00','gross_return':.01,'control_gross_return':0} for _ in range(10)]
        self.assertIsNone(paired_interval(rows,datetime(2026,1,1,tzinfo=UTC),datetime(2026,4,1,tzinfo=UTC)))


if __name__=='__main__':
    unittest.main()
