import unittest
from hermes_trading.funding_carry import simulate


def bars(prices):
    return {i*3600000: [p,p,p,p] for i,p in enumerate(prices)}


class CarryTests(unittest.TestCase):
    def test_entry_cost_drawdown_is_not_hidden_by_first_hour_gain(self):
        flat=bars([100]*3);spot=bars([100,110,110]);spot[0]=[100,110,100,110]
        reference=simulate(flat,flat,flat,{},0,7200000)
        result=simulate(spot,flat,flat,{},0,7200000)
        self.assertGreaterEqual(result['max_drawdown']+1e-12,reference['max_drawdown'])

    def test_exit_open_margin_is_checked(self):
        b=bars([100,100,210])
        result=simulate(b,b,b,{},0,7200000)
        self.assertTrue(result['unsafe'])

    def test_hedge_cancels_price_movement_without_costs(self):
        b=bars([100,120,80])
        r=simulate(b,b,b,{},0,7200000,spot_fee=0,future_fee=0,slip=0)
        self.assertAlmostEqual(r['net_return'],0)

    def test_funding_signs_are_preserved(self):
        b=bars([100]*3)
        r=simulate(b,b,b,{3600000:-.01},0,7200000,spot_fee=0,future_fee=0,slip=0)
        self.assertAlmostEqual(r['net_return'],-.005)

    def test_futures_sale_does_not_create_cash(self):
        b=bars([100]*3)
        r=simulate(b,b,b,{},0,7200000,spot_fee=.001,future_fee=.0005,slip=0)
        q=10000/200.15
        self.assertAlmostEqual(r['quantity'],q)
        self.assertAlmostEqual(r['final_equity'],10000-q*.3)

    def test_intrahour_collateral_failure_is_retained(self):
        b=bars([100]*3);mark=bars([100]*3);mark[3600000]=[100,210,100,100]
        r=simulate(b,b,mark,{},0,7200000,spot_fee=0,future_fee=0,slip=0)
        self.assertTrue(r['unsafe'])
        self.assertEqual(r['first_margin_failure'],3600000)

    def test_missing_prices_are_rejected(self):
        b=bars([100]*3);m=bars([100]*3);del m[3600000]
        with self.assertRaises(ValueError): simulate(b,b,m,{},0,7200000)

    def test_pessimistic_funding_reduces_cashflow(self):
        b=bars([100]*3);m=bars([100]*3);m[3600000]=[100,110,90,100]
        a=simulate(b,b,m,{3600000:.01},0,7200000)
        z=simulate(b,b,m,{3600000:.01},0,7200000,pessimistic=True)
        self.assertLess(z['funding'],a['funding'])
