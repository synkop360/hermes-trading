from datetime import datetime,timedelta,timezone
from decimal import Decimal
import unittest

from hermes_trading.paired_statistics import paired_circular_bootstrap, aligned_calendar_equity, chronological_growth_blocks

START=datetime(2026,1,1,tzinfo=timezone.utc)


class PairedStatisticsTests(unittest.TestCase):
    def test_positive_final_growth_does_not_hide_a_losing_first_calendar_block(self):
        rows=[['100','100'] for _ in range(181)]
        rows[90]=['99','101'];rows[180]=['101','102']
        blocks=chronological_growth_blocks(rows)
        self.assertEqual(blocks[0]['adaptive_growth'],'-0.01')
        self.assertGreater(Decimal(blocks[1]['adaptive_growth']),0)
        self.assertFalse(blocks[0]['adaptive_positive'])

    def test_constant_series_and_short_final_block_preserve_means(self):
        report=paired_circular_bootstrap([[.01,.005]]*5,block_days=3)
        self.assertAlmostEqual(report['adaptive']['lower_95'],.01,places=14)
        self.assertAlmostEqual(report['control']['mean_daily_log_growth'],.005,places=14)
        self.assertAlmostEqual(report['adaptive_minus_control']['lower_95'],.005,places=14)

    def test_arms_are_resampled_jointly_even_when_each_is_volatile(self):
        rows=[[value,value-.001] for value in [.01,-.02,.03,.015,-.008]*36]
        report=paired_circular_bootstrap(rows)
        self.assertAlmostEqual(report['adaptive_minus_control']['lower_95'],.001,places=14)
        self.assertAlmostEqual(report['adaptive_minus_control']['bootstrap_standard_error'],0,places=14)
        self.assertEqual(report,paired_circular_bootstrap(rows))

    def test_small_circular_distribution_matches_exact_enumeration(self):
        # For [0,0,0,4], length2 circular block sums are [0,0,4,4].
        # Two independent starts give means0,1,2 with probabilities1/4,1/2,1/4.
        report=paired_circular_bootstrap([[0,0],[0,0],[0,0],[4,0]],block_days=2)
        self.assertEqual(report['adaptive']['lower_95'],0)
        self.assertAlmostEqual(report['adaptive']['bootstrap_mean'],1,delta=.025)
        self.assertAlmostEqual(report['adaptive']['bootstrap_standard_error'],.70710678,delta=.02)

    def test_nonfinite_boolean_shape_and_invalid_bounds_are_rejected(self):
        for rows in ([],[[.01]],[[.01,True]]*3,[[float('nan'),0]]*3,[[0,float('inf')]]*3):
            with self.subTest(rows=rows),self.assertRaises(ValueError):paired_circular_bootstrap(rows,block_days=2)
        for kwargs in ({'block_days':0},{'block_days':3},{'resamples':True},{'seed':-1}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                paired_circular_bootstrap([[0,0]]*3,**kwargs)

    def pairs(self):
        for step in range(577):
            at=START+timedelta(minutes=5*step)
            value=str(Decimal('100')+Decimal(step)/Decimal(288))
            def row():
                return {'at':at.isoformat(),'currency':'EUR','external_flow':'0','starting_balance':'100',
                    'net_equity':value,'trading_cash':value,'open_liquidation_value':'0','known_accrued_costs':'0',
                    'missing':[],'valuation_model':'long_spot_net_v1',
                    'cost_provenance':{name:{'amount':'0','quality':'actual','source':'test'}
                                       for name in ('hosting','ai','tools','funding')}}
            yield row(),row()

    def test_calendar_uses_aligned_complete_observations(self):
        report=aligned_calendar_equity(self.pairs(),start=START,days=2)
        self.assertEqual(report['daily_equity'],[['100','100'],['101','101'],['102','102']])
        self.assertEqual(report['observations'],577)

    def test_unknown_intraday_value_cannot_be_hidden_by_valid_endpoints(self):
        rows=list(self.pairs());rows[15][0]['net_equity']=None
        with self.assertRaisesRegex(ValueError,'unknown'):aligned_calendar_equity(rows,start=START,days=2)

    def test_gaps_and_unpaired_timestamps_are_rejected(self):
        rows=list(self.pairs());del rows[15]
        with self.assertRaisesRegex(ValueError,'gap'):aligned_calendar_equity(rows,start=START,days=2)
        rows=list(self.pairs());rows[15][1]['at']=(START+timedelta(minutes=76)).isoformat()
        with self.assertRaisesRegex(ValueError,'aligned'):aligned_calendar_equity(rows,start=START,days=2)

    def test_missing_inception_boundary_is_not_invented(self):
        rows=list(self.pairs())[1:]
        with self.assertRaisesRegex(ValueError,'boundary'):aligned_calendar_equity(rows,start=START,days=2)

    def test_external_flow_and_inconsistent_valuation_are_rejected(self):
        for change in ({'external_flow':'1'},{'trading_cash':'0'}):
            rows=list(self.pairs());rows[15][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                aligned_calendar_equity(rows,start=START,days=2)
