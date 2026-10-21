from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from dataclasses import replace
import unittest

from hermes_trading.market_data import ClosedCandle
from hermes_trading.domain import Position, PositionStatus
from hermes_trading.research import snapshots, aggregate_candles, whole_position_stats, rolling_90, run_backtest, conservative_strategy
from hermes_trading.trend import trend_pullback_matches


def bars(prices):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [ClosedCandle(start+timedelta(minutes=15*i), start+timedelta(minutes=15*(i+1)), D(str(p)), D(str(p)), D(str(p)), D(str(p)), D('1')) for i,p in enumerate(prices)]


class ReturnValidationTests(unittest.TestCase):
    def test_snapshots_never_expose_future_and_keep_ohlc(self):
        data = bars([100, 101, 999])
        stream = list(snapshots({'BTC/USDT': data}, '15m'))
        self.assertEqual(stream[1].price['closes'], (D('100'), D('101')))
        self.assertEqual(len(stream[1].price['candles']), 2)
        self.assertTrue(all(c['timestamp'] <= stream[1].candle.close_timestamp.isoformat() for c in stream[1].price['candles']))

    def test_aggregation_drops_incomplete_bars(self):
        result = aggregate_candles(bars(range(100,119)), '15m', '4h')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].close, D('115'))
        self.assertEqual(result[0].volume, D('16'))

    def test_missing_candle_resets_indicator_history(self):
        data=bars([100,101,102,103])
        stream=list(snapshots({'BTC/USDT':[data[0],data[2],data[3]]},'15m'))
        self.assertEqual(stream[1].price['closes'],(D('102'),))

    def test_partial_legs_are_one_position_and_open_remainder_excluded(self):
        p = Position('leg', 'BTC/USDT', PositionStatus.CLOSED, D('1'), D('10'), D('9'), '1', '2025-01-01', '2025-01-02', D('2'), D('.2'))
        q = replace(p, position_id='remainder', realized_pnl=D('-1'))
        self.assertEqual(whole_position_stats([p,q], [])['completed_positions'], 1)
        self.assertEqual(whole_position_stats([p,q], [])['net_pnl'], '1')
        self.assertEqual(whole_position_stats([p], [replace(q, status=PositionStatus.OPEN)])['completed_positions'], 0)

    def test_rolling_uses_full_90_calendar_days(self):
        start = datetime(2025,1,1,tzinfo=UTC)
        curve = [(start+timedelta(days=i), D('50') if i<90 else D('100')) for i in range(91)]
        self.assertEqual(rolling_90(curve)['windows'], 1)
        self.assertEqual(rolling_90(curve)['doubling_frequency'], 1.0)
        self.assertEqual(rolling_90(curve[:90])['windows'], 0)

    def test_fixed_inputs_not_mutated_and_market_costs_enforced(self):
        source = {'entry': {'indicator':'rsi','threshold':'100','fee':'maker'},'exit':{'fee':'maker'},'stop_loss_pct':'10'}
        fixed = conservative_strategy(source)
        self.assertEqual(source['entry']['fee'],'maker')
        self.assertEqual(fixed['entry']['fee'],'taker')
        self.assertEqual(fixed['simulation']['taker_fee_rate'],'0.008')

    def test_yaml_float_settings_are_normalized_before_production_evaluation(self):
        fixed = conservative_strategy({'entry': {'threshold': 32.5}, 'position_size_r': 0.5})
        self.assertEqual(fixed['entry']['threshold'], '32.5')
        self.assertIs(fixed['research']['frozen'], True)

    def test_entry_fills_at_next_open_after_gap_not_signal_close(self):
        strategy = conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100'},'stop_loss_pct':'50','position_size_r':'100'})
        strategy['simulation'].update(max_symbol_exposure_pct='0.5',max_total_exposure_pct='1',max_order_quote='50')
        result = run_backtest({'BTC/USDT':bars([100]*15+[50]*3)},strategy,'15m')
        self.assertFalse(result['halted'])
        self.assertGreater(D(result['ending_equity']),D('49'))

    def test_trend_requires_uptrend_and_recovery_not_simple_rise(self):
        prices=[100]*81+list(range(101,119))+[108,120]
        snapshot=list(snapshots({'BTC/USDT':bars(prices)},'15m'))[-1]
        self.assertTrue(trend_pullback_matches(snapshot,{}))
        rising=list(snapshots({'BTC/USDT':bars(range(100,220))},'15m'))[-1]
        self.assertFalse(trend_pullback_matches(rising,{}))

    def test_deterministic_no_borrowing_cap_and_mtm_halt(self):
        strategy = conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100'},'stop_loss_pct':'50','position_size_r':'100'})
        strategy['simulation'].update(max_symbol_exposure_pct='0.5',max_total_exposure_pct='1',max_order_quote='50')
        data = {asset: bars([100]*16+[80]*3) for asset in ['BTC/USDT','ETH/USDT','SOL/USDT']}
        first = run_backtest(data, strategy, '15m')
        second = run_backtest(data, strategy, '15m')
        self.assertEqual(first, second)
        self.assertLessEqual(first['max_open_positions'],2)
        self.assertGreaterEqual(D(first['minimum_cash']),0)
        self.assertTrue(first['halted'])
        self.assertLess(D(first['ending_equity']),D('46'))

    def test_halted_position_with_missing_bar_exits_at_next_observation(self):
        strategy = conservative_strategy({'version':'t','entry':{'indicator':'rsi','threshold':'100'},'stop_loss_pct':'50','position_size_r':'100'})
        strategy['simulation'].update(max_symbol_exposure_pct='0.5',max_total_exposure_pct='1',max_order_quote='50')
        eth = bars([100]*20)
        data = {'BTC/USDT': bars([100]*16+[50]*4), 'ETH/USDT': eth[:16]+eth[17:]}
        result = run_backtest(data, strategy, '15m')
        self.assertTrue(result['halted'])
        self.assertEqual(result['open_positions'], 0)

if __name__ == '__main__':
    unittest.main()
