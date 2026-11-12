from dataclasses import replace
from decimal import Decimal as D
import unittest

from hermes_trading import research
from tests.test_return_validation import bars


class ProfitabilityExperimentTests(unittest.TestCase):
    def policy(self, options=None, data=None):
        from hermes_trading.research_experiments import ExperimentPolicy
        return ExperimentPolicy(options or {}, data or {}, '15m')

    def test_research_policy_is_available(self):
        self.assertIn('policy', __import__('inspect').signature(research.run_backtest).parameters)

    def test_maker_requires_penetration_and_rejects_crossing_open(self):
        policy = self.policy({'maker': True})
        prior = list(research.snapshots({'BTC/USDT': bars([100])}, '15m'))[0]
        candle = bars([100, 100])[1]
        self.assertIsNone(policy.entry_price(prior, replace(candle, low=D('99.9')), {}))
        self.assertIsNone(policy.entry_price(prior, replace(candle, open=D('99.8'), low=D('99')), {}))
        self.assertEqual(policy.entry_price(prior, replace(candle, low=D('99.7')), {}), D('99.9'))

    def test_higher_timeframe_never_uses_unclosed_bar(self):
        data = {'BTC/USDT': bars(range(100, 1720))}
        policy = self.policy({'higher_trend': True}, data)
        stream = list(research.snapshots(data, '15m'))
        self.assertFalse(policy.allow_entry(stream[1598]))
        self.assertFalse(policy.allow_entry(stream[1599]))  # only 100 complete bars
        self.assertTrue(policy.allow_entry(stream[1615]))
        changed = {'BTC/USDT': data['BTC/USDT'][:1616] + bars([1] * 4)}
        # A future price change cannot alter an already closed trend decision.
        changed['BTC/USDT'][1616:] = [replace(c, close=D('1')) for c in data['BTC/USDT'][1616:]]
        self.assertEqual(self.policy({'higher_trend': True}, changed).allow_entry(stream[1615]), True)

    def test_balance_cap_retains_hard_ceiling(self):
        policy = self.policy({'scalable_cap': True})
        self.assertEqual(policy.order_cap(D('300')), D('15'))
        self.assertEqual(policy.order_cap(D('1000')), D('25'))

    def test_unfilled_maker_reserves_only_slot_until_expiry(self):
        strategy = research.conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100','fee':'maker'},'stop_loss_pct':'10'})
        strategy['entry']['fee'] = 'maker'
        strategy['simulation']['max_concurrent_positions'] = 1
        a, b = bars([100]*17), bars([100]*17)
        b[-1] = replace(b[-1], low=D('99'))
        data = {'A': a, 'B': b}
        result = research.run_backtest(data, strategy, '15m', initial_equity=D(300), policy=self.policy({'maker':True}, data))
        self.assertEqual(result['open_positions'], 0)

    def test_discretionary_exit_uses_next_open_and_gap_stop_wins(self):
        strategy = research.conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100'},'exit':{'max_bars':1},'stop_loss_pct':'10'})
        data = {'BTC/USDT': bars([100]*17+[80])}
        result = research.run_backtest(data, strategy, '15m', initial_equity=D(300), policy=self.policy({}, data))
        self.assertEqual(result['reasons'].get('stop_loss'), 1)
        self.assertLess(D(result['expectancy']), D('-.3'))

    def test_delayed_trailing_stays_active_after_price_retreats(self):
        from hermes_trading.domain import Position, PositionStatus
        policy = self.policy({'delayed_trailing':True})
        engine = policy.engine(None, None)
        p = Position('p','BTC/USDT',PositionStatus.OPEN,D(1),D(100),D(90),'v','2025-01-01')
        engine.initial_risk[p.opened_at,p.asset] = D(10)
        data = bars([100]*15+[105,111,106])
        data = [replace(c,high=c.close+D(1),low=c.close-D(1)) for c in data]
        stream = list(research.snapshots({'BTC/USDT':data},'15m'))
        self.assertIsNone(engine._trail_stop(stream[15], {'exit':{'trailing_atr':'2'}},p))
        self.assertIsNotNone(engine._trail_stop(stream[16], {'exit':{'trailing_atr':'2'}},p))
        self.assertIsNotNone(engine._trail_stop(stream[17], {'exit':{'trailing_atr':'2'}},p))

    def test_fixed_target_does_not_take_implicit_rsi_exit(self):
        from hermes_trading.strategy import StrategySignal
        snapshot = list(research.snapshots({'BTC/USDT':bars([100])},'15m'))[0]
        signal = StrategySignal('exit','BTC/USDT','explicit_exit',D(100),'test')
        result = self.policy({'fixed_target_only':True}).exit_signal(snapshot,None,{},signal,False)
        self.assertEqual(result.action,'hold')

    def test_partial_fallback_rechecked_after_execution_gap(self):
        strategy = research.conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100'},
            'exit':{'indicator':'rsi','threshold':'55','partial_close_pct':'.5','trailing_atr':'100'},'stop_loss_pct':'10'})
        strategy['simulation']['minimum_order_quote'] = '1.65'
        data = {'BTC/USDT': [replace(c,high=c.close+D(1),low=c.close-D(1)) for c in bars([100]*16+[110,100])]}
        result = research.run_backtest(data,strategy,'15m',initial_equity=D(300),policy=self.policy({'full_exit_fallback':True},data))
        self.assertEqual(result['experiment']['counts'].get('partial_converted_to_full'),1)
        self.assertEqual(result['reasons'].get('explicit_exit'),1)
        self.assertEqual(result['open_positions'],0)

    def test_halt_overrides_queued_partial_after_missing_bar(self):
        strategy = research.conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100'},
            'exit':{'indicator':'rsi','threshold':'55','partial_close_pct':'.5','trailing_atr':'100'},
            'stop_loss_pct':'50','position_size_r':'100'})
        strategy['simulation'].update(max_symbol_exposure_pct='.5',max_total_exposure_pct='1',max_order_quote='300')
        a = [replace(c,high=c.close+D(1),low=c.close-D(1)) for c in bars([100]*16+[110]*3)]
        b = [replace(c,high=c.close+D(1),low=c.close-D(1)) for c in bars([100]*17+[50])]
        data = {'A':a[:17]+a[18:], 'B':b}
        result = research.run_backtest(data,strategy,'15m',initial_equity=D(300),policy=self.policy({},data))
        self.assertTrue(result['halted'])
        self.assertEqual(result['open_positions'],0)


if __name__ == '__main__':
    unittest.main()
