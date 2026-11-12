from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from hermes_trading.positions import PositionEngine, SimulationSettings, _expected_payoff_covers_costs
from hermes_trading.persistence import StateRepository
from hermes_trading.strategy import _is_maker_exit
from hermes_trading.reflect import run_hermes_reflection, run_fallback_reflection
from tests.test_positions import candle, entry_strategy


def atr_snapshot():
    snap = candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open='100', low='99', close='100', high='101')
    return replace(snap, price={**snap.price, 'candles': [{'high': '100.1', 'low': '99.9', 'close': '100'}] * 30})


class ExecutionParityTests(unittest.TestCase):
    def test_inactive_take_profit_cannot_bypass_cost_filter(self):
        strategy = {'exit': {'trailing_atr': '2', 'take_profit_atr': '100'},
                    'simulation': {'taker_fee_rate': '.008', 'slippage_rate': '.001'}}
        self.assertFalse(_expected_payoff_covers_costs(strategy, SimulationSettings.from_strategy(strategy), atr_snapshot()))

    def test_market_fee_defaults_are_conservative(self):
        self.assertEqual(SimulationSettings.from_strategy({}).taker_fee_rate, D('.008'))
        self.assertFalse(_is_maker_exit({}))

    def test_atr_stop_and_trailing_are_shared(self):
        from hermes_trading.execution_policy import initial_stop_price, trailing_stop_price
        snap = atr_snapshot()
        self.assertEqual(initial_stop_price(snap, {'exit': {'stop_atr': '2.5'}}, D('100')), D('99.50'))
        self.assertEqual(trailing_stop_price(snap, {'exit': {'trailing_atr': '2'}}, D('99.8')), D('99.8'))

    def test_cost_inclusive_size_respects_risk_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateRepository.open(Path(directory) / 'test.db')
            state.migrate()
            try:
                engine = PositionEngine(state, equity=D('50'))
                strategy = {'position_size_r': '.5', 'entry': {'fee': 'taker'}, 'simulation': {'risk_per_trade_pct': '.006'}}
                settings = SimulationSettings.from_strategy(strategy)
                notional = engine._entry_notional(strategy, settings, D('100'), D('99'))
                self.assertLessEqual(notional * D('.028'), D('.15'))
            finally:
                state.close()

    def test_frozen_strategy_skips_every_reflection_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            original = 'version: frozen\nresearch:\n  frozen: true\n'
            (path / 'strategy.yaml').write_text(original)
            for reflect in (run_hermes_reflection, run_fallback_reflection):
                result = reflect(path)
                self.assertEqual(result['status'], 'frozen')
                self.assertEqual((path / 'strategy.yaml').read_text(), original)

    def test_quantity_is_rounded_before_checking_exchange_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateRepository.open(Path(directory) / 'test.db'); state.migrate()
            try:
                strategy = entry_strategy()
                strategy['simulation'] = {'quantity_decimals': '2', 'minimum_order_quantity': '.061'}
                result = PositionEngine(state).process_candle(candle(datetime(2026, 7, 30, 20, tzinfo=UTC), open='100', low='79', close='80'), strategy)
                self.assertEqual(result.reason, 'below_exchange_minimum')
                self.assertIsNone(result.position)
            finally:
                state.close()


class LivePartialParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_cycle_uses_atr_stop_and_preserves_ratchet_above_entry(self):
        from datetime import timedelta
        from hermes_trading.domain import OrderStatus
        from hermes_trading.loop import run_live_cycle
        from hermes_trading.persistence.artifacts import ArtifactProjector
        from tests.test_live_cycle import FilledValidationGateway, entry_snapshot, NOW
        class Gateway(FilledValidationGateway):
            async def cancel_order(self, identifier, asset):
                for index, order in enumerate(self._orders):
                    if order.exchange_order_id == identifier:
                        self._orders[index] = replace(order, status=OrderStatus.CANCELED)
                        return self._orders[index]
                raise AssertionError('unknown stop')
        class Market:
            snapshot = entry_snapshot('BTC/USDT')
            async def collect(self, assets):
                return {asset: self.snapshot for asset in assets}
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = StateRepository.open(Path(directory) / 'test.db'); state.migrate(); stack.callback(state.close)
            market = Market()
            history = [{'high':'82', 'low':'78', 'close':'80'}] * 30
            market.snapshot = replace(market.snapshot, price={**market.snapshot.price, 'candles': history})
            gateway = Gateway([]); gateway.balances['USDT'] = D('1000')
            strategy = entry_strategy(); strategy['exit'] = {'stop_atr':'2.5', 'trailing_atr':'2'}
            projector = ArtifactProjector(Path(directory))
            await run_live_cycle(['BTC/USDT'], state, projector, market, strategy, gateway, validate_only=True, now=lambda: NOW)
            position = state.open_position('BTC/USDT')
            self.assertIsNotNone(position)
            self.assertEqual(position.stop_price, D('70'))
            current = market.snapshot.candle
            market.snapshot = replace(market.snapshot, candle=replace(current, open_timestamp=current.open_timestamp+timedelta(hours=1), close_timestamp=current.close_timestamp+timedelta(hours=1), open=D('85'), low=D('85'), high=D('91'), close=D('90')))
            await run_live_cycle(['BTC/USDT'], state, projector, market, strategy, gateway, validate_only=True, now=lambda: NOW+timedelta(hours=1))
            self.assertEqual(state.open_position('BTC/USDT').stop_price, D('82'))

    async def test_stop_fill_before_fetch_requires_reconciliation_for_exit_and_replacement(self):
        from hermes_trading.execution import ExecutionService
        from hermes_trading.exchange import KrakenGateway
        from tests.fakes.kraken import FakeKrakenExchange
        from tests.test_execution_service import sample_signal, sample_position
        for filled, operation in (('.0005', 'exit'), ('.001', 'replacement')):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                state = StateRepository.open(Path(directory) / 'test.db'); state.migrate(); stack.callback(state.close)
                fake = FakeKrakenExchange(); gateway = KrakenGateway(lambda: fake)
                service = ExecutionService(state, allow_submission=True)
                position = replace(sample_position(), quantity=D('.001'))
                state.save_position(position)
                protection = replace(sample_signal(action='protective_stop'), reason='protective_stop')
                stop = await service.ensure_protective_stop(protection, position, gateway)
                fake._orders[stop.exchange_order.exchange_order_id].update(filled=filled, remaining=str(D('.001')-D(filled)), average='64675', status='closed' if filled=='.001' else 'open')
                before = fake.create_order_calls
                if operation == 'exit':
                    result = await service.submit_exit(sample_signal(action='exit'), position, gateway)
                else:
                    result = await service.ensure_protective_stop(protection, replace(position, stop_price=D('64800')), gateway)
                self.assertEqual(result.status, 'reconciliation_required')
                self.assertEqual(fake.create_order_calls, before)

    async def test_stop_fill_during_cancellation_requires_reconciliation_before_exit(self):
        from hermes_trading.execution import ExecutionService
        from hermes_trading.exchange import KrakenGateway
        from tests.fakes.kraken import FakeKrakenExchange
        from tests.test_execution_service import sample_signal, sample_position
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = StateRepository.open(Path(directory) / 'test.db'); state.migrate(); stack.callback(state.close)
            fake = FakeKrakenExchange(); gateway = KrakenGateway(lambda: fake)
            service = ExecutionService(state, allow_submission=True)
            position = replace(sample_position(), quantity=D('.001'))
            state.save_position(position)
            protection = replace(sample_signal(action='protective_stop'), reason='protective_stop')
            await service.ensure_protective_stop(protection, position, gateway)
            cancel = fake.cancel_order
            async def fill_then_cancel(order_id, symbol=None):
                fake._orders[order_id].update(filled='.0005', remaining='.0005', average='64675')
                return await cancel(order_id, symbol)
            fake.cancel_order = fill_then_cancel
            before = fake.create_order_calls
            result = await service.submit_exit(sample_signal(action='exit'), position, gateway)
            self.assertEqual(result.status, 'reconciliation_required')
            self.assertEqual(fake.create_order_calls, before)

    async def test_unavailable_snapshot_blocks_cycle_before_signal_evaluation(self):
        from hermes_trading.loop import run_live_cycle
        from hermes_trading.persistence.artifacts import ArtifactProjector
        from tests.test_live_cycle import FakeMarketData, ValidationGateway, entry_strategy, NOW
        class UnavailableMarket(FakeMarketData):
            async def collect(self, assets):
                result = await super().collect(assets)
                return {asset: replace(snap, candle=None, availability='unavailable') for asset, snap in result.items()}
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = StateRepository.open(Path(directory) / 'test.db'); state.migrate(); stack.callback(state.close)
            events = []
            result = await run_live_cycle(['BTC/USDT'], state, ArtifactProjector(Path(directory)), UnavailableMarket(events), entry_strategy(), ValidationGateway(events), validate_only=True, now=lambda: NOW)
            self.assertEqual(result.status, 'blocked_market')

    async def test_two_distinct_exit_orders_account_for_entry_fee_once_after_restart(self):
        from hermes_trading.domain import Decision
        from hermes_trading.execution import ExecutionService
        from hermes_trading.exchange import KrakenGateway
        from hermes_trading.reconciliation import ReconciliationService
        from hermes_trading.risk import RiskDecision
        from tests.fakes.kraken import FakeKrakenExchange
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / 'test.db'
            state = StateRepository.open(path); state.migrate(); stack.callback(state.close)
            fake = FakeKrakenExchange(); fake._markets['BTC/USDT']['limits']['cost']['min'] = '1'; gateway = KrakenGateway(lambda: fake)
            service = ExecutionService(state, allow_submission=True)
            signal = Decision('entry', 'BTC/USDT', 'enter', 'entry', 'bar1', 'test', D('65000'), '2026-07-30T12:00:00+00:00')
            risk = RiskDecision('r', True, 'authorized', D('6.5'), signal.decided_at)
            result = await service.submit_entry(signal, risk, gateway)
            remote = fake._orders[result.exchange_order.exchange_order_id]
            remote.update(status='closed', filled='0.0001', remaining='0', average='65000')
            remote['fee']['cost'] = '.01'
            fake._balances['free']['BTC'] = '.0001'
            await ReconciliationService(state).run(gateway)
            position_id = 'kraken:' + result.client_order_id
            position = state.position_by_id(position_id)
            partial = replace(signal, decision_id='partial', action='exit', reason='partial_exit', candle_id='bar2', reference_price=D('66000'))
            result = await service.submit_exit(partial, position, gateway, fraction=D('.5'))
            self.assertEqual(result.exchange_order.amount, D('.00005'))
            remote = fake._orders[result.exchange_order.exchange_order_id]
            remote.update(status='closed', filled='.00005', remaining='0', average='66000')
            remote['fee']['cost'] = '.01'
            fake._balances['free']['BTC'] = '.00005'
            await ReconciliationService(state).run(gateway)
            position = state.position_by_id(position_id)
            self.assertEqual(position.partial_exit_count, 1)
            self.assertEqual(position.realized_pnl, D('.035'))
            state.close(); state = StateRepository.open(path); state.migrate(); stack.callback(state.close)
            service = ExecutionService(state, allow_submission=True)
            final = replace(partial, decision_id='final', reason='stop_loss', candle_id='bar3')
            result = await service.submit_exit(final, state.position_by_id(position_id), gateway)
            remote = fake._orders[result.exchange_order.exchange_order_id]
            remote.update(status='closed', filled='.00005', remaining='0', average='66000')
            remote['fee']['cost'] = '.01'
            fake._balances['free']['BTC'] = '0'
            await ReconciliationService(state).run(gateway)
            await ReconciliationService(state).run(gateway)
            closed = state.position_by_id(position_id)
            self.assertEqual(closed.realized_pnl, D('.07'))
            self.assertEqual(closed.fees, D('.03'))
            self.assertEqual(closed.status.value, 'closed')
            state.close()
