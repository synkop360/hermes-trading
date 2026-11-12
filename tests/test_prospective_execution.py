from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from hermes_trading.positions import PositionEngine
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.market_data import ClosedCandle,MarketSnapshot
from hermes_trading.strategy import StrategySignal
from venue_fixtures import venue_record


class ProspectiveExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'trading.db'
        self.repo=StateRepository.open(self.path);self.repo.migrate()
        self.at=datetime(2026,10,10,10,tzinfo=timezone.utc)
        self.rules={'version':'1','entry':{'indicator':'rsi','threshold':'30','direction':'long'},'stop_loss_pct':'2',
                    'simulation':{'taker_fee_rate':'.008','slippage_rate':'.001','minimum_expected_edge_pct':'0'}}
    def tearDown(self):self.repo.close();self.tmp.cleanup()
    def snapshot(self,at=None,**price):
        at=at or self.at
        return MarketSnapshot('ETH/EUR','1h','available','evaluate','test',
            ClosedCandle(at-timedelta(hours=1),at,Decimal('80'),Decimal('100'),Decimal('79'),Decimal('80'),Decimal('1')),
            price={'bid':'89','ask':'90','received_at':(at+timedelta(seconds=1)).isoformat(),**price},
            venue_rules=venue_record('ETH/EUR',at))
    def engine(self,at):return PositionEngine(self.repo,prospective_at=at)
    def enter(self,snapshot=None,at=None):
        snapshot=snapshot or self.snapshot();at=at or self.at+timedelta(seconds=2)
        signal=StrategySignal('enter','ETH/EUR','test_entry',Decimal('80'),snapshot.candle_identity.candle_id)
        return self.engine(at).process_candle(snapshot,self.rules,signal_override=signal)

    def test_stop_uses_fresh_bid_within_already_processed_entry_bar(self):
        first=self.enter()
        snap=self.snapshot(bid='70',ask='71',received_at=(self.at+timedelta(seconds=3)).isoformat())
        result=self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'stop_loss')
        self.assertEqual(result.orders[0].average_fill_price,Decimal('69.930'))
        self.assertEqual(result.position.position_id,first.position.position_id)
        self.repo.close();self.repo=StateRepository.open(self.path);self.repo.migrate()
        replay=self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules)
        self.assertEqual(replay.orders,())
        self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),2)

    def test_target_uses_fresh_bid_before_next_closed_bar(self):
        self.rules['exit']={'take_profit_pct':'5'};self.enter()
        snap=self.snapshot(bid='95',ask='96',received_at=(self.at+timedelta(seconds=3)).isoformat())
        result=self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'take_profit')
        self.assertEqual(result.orders[0].average_fill_price,Decimal('94.905'))

    def test_multiple_intrabar_holds_then_stop_do_not_duplicate_decisions(self):
        self.enter()
        for offset in (4,6):
            snap=self.snapshot(bid='91',ask='92',received_at=(self.at+timedelta(seconds=offset-1)).isoformat())
            result=self.engine(self.at+timedelta(seconds=offset)).process_candle(snap,self.rules)
            self.assertTrue(result.processed)
            self.assertEqual(result.reason,'position_open')
            replay=self.engine(self.at+timedelta(seconds=offset)).process_candle(snap,self.rules)
            self.assertFalse(replay.processed)
        snap=self.snapshot(bid='70',ask='71',received_at=(self.at+timedelta(seconds=7)).isoformat())
        result=self.engine(self.at+timedelta(seconds=8)).process_candle(snap,self.rules)
        self.assertEqual(result.position.status.value,'closed')
        self.assertEqual(len(self.repo.export_canonical_tables()['decisions']),4)

    def test_exit_on_new_bar_reserves_signal_and_cannot_reenter_that_bar(self):
        from hermes_trading.strategy import evaluate_strategy
        self.enter();bar=self.at+timedelta(hours=1)
        snap=self.snapshot(bar,bid='70',ask='71',closes=[str(value) for value in range(100,85,-1)])
        exited=self.engine(bar+timedelta(seconds=2)).process_candle(snap,self.rules)
        self.assertEqual(exited.position.status.value,'closed')
        # The signal would permit an entry if the original bar were not reserved.
        self.assertEqual(evaluate_strategy(snap,self.rules,None).action,'enter')
        next_quote=replace(snap,price=dict(snap.price,received_at=(bar+timedelta(seconds=3)).isoformat()))
        replay=self.engine(bar+timedelta(seconds=4)).process_candle(next_quote,self.rules)
        self.assertEqual(replay.orders,())
        self.assertIsNone(self.repo.open_position('ETH/EUR'))
        self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),2)

    def test_missing_intrabar_quote_does_not_invent_stop_from_consumed_close(self):
        first=self.enter()
        # Original close80 predates the actual90.09entry and is below its stop.
        missing=self.snapshot(bid=None,received_at=(self.at+timedelta(seconds=3)).isoformat())
        result=self.engine(self.at+timedelta(seconds=4)).process_candle(missing,self.rules)
        self.assertEqual(result.orders,())
        self.assertIsNone(self.repo.pending_paper_exit(first.position.position_id))
        recovered=self.snapshot(bid='95',ask='96',received_at=(self.at+timedelta(seconds=5)).isoformat())
        result=self.engine(self.at+timedelta(seconds=6)).process_candle(recovered,self.rules)
        self.assertEqual(result.orders,())
        self.assertEqual(result.position.status.value,'open')

    def test_partial_remainder_is_protected_within_the_same_closed_bar(self):
        self.rules['exit']={'partial_close_pct':'.5','trailing_atr':'2'};self.enter()
        snap=self.snapshot(bid='95',ask='96',received_at=(self.at+timedelta(seconds=3)).isoformat())
        signal=StrategySignal('exit','ETH/EUR','partial_exit',Decimal('95'),snap.candle_identity.candle_id)
        first=self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules,signal_override=signal)
        self.assertEqual(first.position.partial_exit_count,1)
        replay=self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules,signal_override=signal)
        self.assertEqual(replay.orders,())
        stopped=self.snapshot(bid='70',ask='71',received_at=(self.at+timedelta(seconds=5)).isoformat())
        last=self.engine(self.at+timedelta(seconds=6)).process_candle(stopped,self.rules)
        self.assertEqual(last.reason,'stop_loss');self.assertEqual(last.position.status.value,'closed')
        self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),3)

    def test_signal_reservation_failure_rolls_back_exit_and_all_financial_rows(self):
        first=self.enter();connection=self.repo._connection
        class FailReservation:
            def __getattr__(self,name):return getattr(connection,name)
            def execute(self,sql,*args):
                if 'INSERT OR IGNORE INTO candles_processed' in sql:raise RuntimeError('reservation failed')
                return connection.execute(sql,*args)
        self.repo._connection=FailReservation()
        snap=self.snapshot(bid='70',ask='71',received_at=(self.at+timedelta(seconds=3)).isoformat())
        try:
            with self.assertRaisesRegex(RuntimeError,'reservation'):self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules)
        finally:self.repo._connection=connection
        self.assertEqual(self.repo.open_position('ETH/EUR'),first.position)
        self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),1)
        result=self.engine(self.at+timedelta(seconds=4)).process_candle(snap,self.rules)
        self.assertEqual(result.position.status.value,'closed')
        self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),2)

    def test_entry_commit_reopen_and_identical_replay_preserves_canonical_history(self):
        self.enter();before=self.repo.export_canonical_tables()
        payloads=self.repo._connection.execute('SELECT decision_id,payload FROM projection_payloads ORDER BY decision_id').fetchall()
        self.repo.close();self.repo=StateRepository.open(self.path);self.repo.migrate()
        replay=self.enter()
        self.assertFalse(replay.processed)
        self.assertEqual(replay.reason,'duplicate_candle')
        self.assertEqual(self.repo.export_canonical_tables(),before)
        self.assertEqual(self.repo._connection.execute('SELECT decision_id,payload FROM projection_payloads ORDER BY decision_id').fetchall(),payloads)
    def test_buy_ask_sell_bid_and_actual_decision_time(self):
        first=self.enter();self.assertEqual(first.position.entry_price,Decimal('90.090'))
        self.assertEqual(first.position.opened_at,(self.at+timedelta(seconds=2)).isoformat())
        snap=self.snapshot(self.at+timedelta(hours=1),bid='70',ask='71')
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules)
        self.assertEqual(result.orders[0].average_fill_price,Decimal('69.930'))
        expected=first.position.quantity*(Decimal('69.930')-Decimal('90.090'))-first.position.fees-result.orders[0].fees
        self.assertEqual(result.position.realized_pnl,expected)
    def test_invalid_quote_never_creates_entry(self):
        for price in ({'ask':None},{'ask':'NaN'},{'bid':'91'},{'ask':'-1'},{'bid':True},{'received_at':None},
                      {'received_at':(self.at-timedelta(minutes=6)).isoformat()},
                      {'received_at':(self.at+timedelta(minutes=1)).isoformat()}):
            with self.subTest(price=price):
                result=self.enter(self.snapshot(**price));self.assertEqual(result.orders,())
                self.assertIsNone(self.repo.open_position('ETH/EUR'))
                # Each case is a separate experiment; duplicate-candle protection must not mask validation.
                self.repo.close();self.tmp.cleanup();self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'trading.db'
                self.repo=StateRepository.open(self.path);self.repo.migrate()
    def test_pending_exit_survives_restart_and_same_candle_quote(self):
        self.enter();snap=self.snapshot(self.at+timedelta(hours=1),bid=None)
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'execution_quote_pending');self.assertEqual(result.orders,())
        self.repo.close();self.repo=StateRepository.open(self.path);self.repo.migrate()
        snap=replace(snap,price={'bid':'70','ask':'71','received_at':(self.at+timedelta(hours=1,seconds=3)).isoformat()})
        engine=self.engine(self.at+timedelta(hours=1,seconds=4));result=engine.process_candle(snap,self.rules)
        self.assertEqual(result.position.status.value,'closed');self.assertEqual(len(result.orders),1)
        duplicate=engine.process_candle(snap,self.rules);self.assertEqual(duplicate.orders,())
        self.assertEqual(len(self.repo.export_canonical_tables()['order_fills']),2)
    def test_minimum_quantity_uses_actual_ask(self):
        self.rules['simulation']['minimum_order_quantity']='.06'
        result=self.enter();self.assertEqual(result.reason,'below_exchange_minimum');self.assertEqual(result.orders,())
    def test_maker_contract_is_rejected(self):
        self.rules['entry']['fee']='maker'
        with self.assertRaisesRegex(ValueError,'maker'):self.enter()

    def test_partial_exit_uses_bid_and_preserves_entry_fee_total(self):
        self.rules['exit']={'partial_close_pct':'.5','trailing_atr':'2'}
        initial=self.enter().position;snap=self.snapshot(self.at+timedelta(hours=1),bid='95',ask='96')
        signal=StrategySignal('exit','ETH/EUR','partial_exit',Decimal('100'),snap.candle_identity.candle_id)
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules,signal_override=signal)
        self.assertEqual(result.orders[0].average_fill_price,Decimal('94.905'))
        self.assertEqual(result.closed_positions[0].fees+result.position.fees,initial.fees+result.orders[0].fees)

    def test_quote_decision_cannot_precede_signal_candle(self):
        with self.assertRaisesRegex(ValueError,'precedes'):self.enter(at=self.at-timedelta(seconds=1))

    def test_historical_high_does_not_fill_unplaced_profit_order(self):
        self.rules['exit']={'take_profit_pct':'5'}
        self.enter();snap=self.snapshot(self.at+timedelta(hours=1),bid='91',ask='92')
        snap=replace(snap,candle=replace(snap.candle,open=Decimal('91'),low=Decimal('90'),close=Decimal('91'),high=Decimal('110')))
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules)
        self.assertEqual(result.orders,());self.assertEqual(result.position.status.value,'open')

    def test_full_exit_below_venue_minimum_remains_pending(self):
        self.rules['simulation']['minimum_order_quote']='4'
        self.enter();snap=self.snapshot(self.at+timedelta(hours=1),bid='60',ask='61')
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules)
        self.assertEqual(result.orders,());self.assertEqual(result.reason,'execution_minimum_pending')
        self.assertIsNotNone(self.repo.pending_paper_exit(result.position.position_id))

    def test_quote_before_signal_is_not_an_executable_price(self):
        result=self.enter(self.snapshot(received_at=(self.at-timedelta(seconds=1)).isoformat()))
        self.assertEqual(result.orders,());self.assertIsNone(result.position)

    def test_max_bars_counts_from_entry_signal_not_delayed_fill(self):
        self.rules['exit']={'max_bars':'1'};self.enter()
        snap=self.snapshot(self.at+timedelta(hours=1),bid='91',ask='92')
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'max_bars');self.assertEqual(len(result.orders),1)

    def test_pending_partial_cannot_override_current_stop(self):
        self.rules['exit']={'partial_close_pct':'.5','trailing_atr':'2'};self.enter()
        snap=self.snapshot(self.at+timedelta(hours=1),bid=None)
        signal=StrategySignal('exit','ETH/EUR','partial_exit',Decimal('100'),snap.candle_identity.candle_id)
        result=self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules,signal_override=signal)
        self.assertEqual(result.reason,'execution_quote_pending')
        snap=replace(snap,price={'bid':'70','ask':'71','received_at':(self.at+timedelta(hours=1,seconds=3)).isoformat()})
        result=self.engine(self.at+timedelta(hours=1,seconds=4)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'stop_loss');self.assertEqual(result.position.status.value,'closed')

    def test_promoted_full_stop_survives_minimum_and_price_recovery(self):
        self.rules['simulation']['minimum_order_quote']='4'
        self.rules['exit']={'partial_close_pct':'.5','trailing_atr':'2'};self.enter()
        snap=self.snapshot(self.at+timedelta(hours=1),bid=None)
        signal=StrategySignal('exit','ETH/EUR','partial_exit',Decimal('100'),snap.candle_identity.candle_id)
        self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules,signal_override=signal)
        snap=replace(snap,price={'bid':'60','ask':'61','received_at':(self.at+timedelta(hours=1,seconds=3)).isoformat()})
        result=self.engine(self.at+timedelta(hours=1,seconds=4)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'execution_minimum_pending')
        self.assertEqual(self.repo.pending_paper_exit(result.position.position_id)['reason'],'stop_loss')
        snap=replace(snap,price={'bid':'95','ask':'96','received_at':(self.at+timedelta(hours=1,seconds=5)).isoformat()})
        result=self.engine(self.at+timedelta(hours=1,seconds=6)).process_candle(snap,self.rules)
        self.assertEqual(result.reason,'stop_loss');self.assertEqual(result.position.status.value,'closed')

    def test_missing_contract_overrides_pending_partial_with_recovery(self):
        from hermes_trading.entry_contracts import EntryContractError
        self.rules['exit']={'partial_close_pct':'.5','trailing_atr':'2'};self.enter()
        snap=self.snapshot(self.at+timedelta(hours=1),bid=None)
        signal=StrategySignal('exit','ETH/EUR','partial_exit',Decimal('100'),snap.candle_identity.candle_id)
        self.engine(self.at+timedelta(hours=1,seconds=2)).process_candle(snap,self.rules,signal_override=signal)
        snap=replace(snap,price={'bid':'95','ask':'96','received_at':(self.at+timedelta(hours=1,seconds=3)).isoformat()})
        def missing(*args):raise EntryContractError('entry archive missing')
        engine=self.engine(self.at+timedelta(hours=1,seconds=4));engine.entry_contract_resolver=missing
        result=engine.process_candle(snap,self.rules)
        self.assertEqual(result.reason,'entry_contract_recovery_exit');self.assertEqual(result.position.status.value,'closed')

if __name__=='__main__':unittest.main()
