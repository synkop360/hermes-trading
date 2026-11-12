from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hermes_trading.wallet_observer import observe_wallet, value_current_wallet
import test_wallet_metrics as fixtures

D=Decimal; NOW=fixtures.NOW; START=fixtures.START


class Repository:
    def __init__(self,rows=()): self.rows=rows
    def portfolio_positions(self): return tuple(self.rows)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.state=Path(self.tmp.name)
        self.config={'schema_version':1,'experiment_id':'test','currency':'EUR','starting_balance':'100','activated_at':START.isoformat()}
        (self.state/'wallet_account.json').write_text(json.dumps(self.config))
        self.costs={'schema_version':1,'experiment_id':'test','currency':'EUR','scope_start':START.isoformat(),
                    'as_of':NOW.isoformat(),'totals':{name:{'amount':'0','source':'explicit_test_zero','quality':'actual'}
                    for name in ('hosting','ai','tools','funding')}}
        self.write_costs()
        self.strategy={'version':1,'simulation':{'taker_fee_rate':'.01','slippage_rate':'0'}}
        from venue_fixtures import venue_record
        self.snapshots={'BTC/EUR':SimpleNamespace(price={'bid':D('11'),'received_at':NOW.isoformat(),'source':'test_bid','timestamp':None},
            venue_rules=venue_record('BTC/EUR',NOW))}

    def tearDown(self): self.tmp.cleanup()
    def write_costs(self): (self.state/'wallet_costs.json').write_text(json.dumps(self.costs))
    def position(self): return {**fixtures.ValuationTests().position('open'),'strategy_version':'1'}
    def observe(self,rows=(),now=NOW): return observe_wallet(self.state,Repository(rows),self.snapshots,self.strategy,now=now)

    def test_observer_uses_open_inventory_and_records_no_synthetic_month(self):
        report=self.observe([self.position()])
        self.assertEqual(D(report['observation']['net_equity']),D('100.79'))
        self.assertEqual(report['calendar_metrics']['status'],'incomplete')
        self.assertEqual(report['gate']['status'],'blocked')
        self.assertFalse(report['gate']['eligible_for_real_trading'])
        self.assertTrue((self.state/'wallet_equity.sqlite').exists())
        self.assertTrue((self.state/'wallet_metrics.json').exists())

    def test_missing_or_stale_cumulative_costs_leave_equity_unknown(self):
        self.costs['as_of']=(NOW-timedelta(hours=1)).isoformat(); self.write_costs()
        self.assertIsNone(self.observe()['observation']['net_equity'])
        (self.state/'wallet_costs.json').unlink()
        self.assertIsNone(self.observe(now=NOW+timedelta(seconds=1))['observation']['net_equity'])

    def test_cost_amount_is_cumulative_and_not_charged_again_on_restart(self):
        self.costs['totals']['hosting']['amount']='1'; self.write_costs()
        self.assertEqual(D(self.observe()['observation']['net_equity']),D('99'))
        self.assertEqual(D(self.observe()['observation']['net_equity']),D('99'))

    def test_pre_experiment_positions_wrong_cost_scope_and_future_coverage_are_rejected(self):
        row={**self.position(),'opened_at':(START-timedelta(days=1)).isoformat()}
        with self.assertRaises(ValueError): self.observe([row])
        for changes in ({'scope_start':(START-timedelta(days=1)).isoformat()},{'as_of':(NOW+timedelta(hours=1)).isoformat()}):
            prior=dict(self.costs); self.costs.update(changes); self.write_costs()
            with self.assertRaises(ValueError): self.observe()
            self.costs=prior

    def test_bid_without_receipt_time_is_unknown_not_a_candle_proxy(self):
        self.snapshots['BTC/EUR'].price.pop('received_at')
        self.assertIsNone(self.observe([self.position()])['observation']['net_equity'])

    def test_unconfigured_account_does_not_create_measurements(self):
        (self.state/'wallet_account.json').unlink()
        self.assertIsNone(self.observe())
        self.assertFalse((self.state/'wallet_equity.sqlite').exists())

    def test_fixed_account_contract_is_validated_before_returning_entry_budget(self):
        value_current_wallet(self.state,Repository(),self.snapshots,self.strategy,now=NOW)
        for changes in ({'starting_balance':'200'},{'experiment_id':'changed'},
                        {'activated_at':(START-timedelta(days=1)).isoformat()},
                        {'activated_at':(NOW+timedelta(hours=1)).isoformat()}):
            changed={**self.config,**changes}
            (self.state/'wallet_account.json').write_text(json.dumps(changed))
            costs={**self.costs,'experiment_id':changed['experiment_id'],'scope_start':changed['activated_at']}
            (self.state/'wallet_costs.json').write_text(json.dumps(costs))
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                value_current_wallet(self.state,Repository(),self.snapshots,self.strategy,now=NOW)

    def test_repository_portfolio_includes_error_positions_for_reconciliation(self):
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.domain import Position,PositionStatus
        repo=StateRepository.open(self.state/'trading.db')
        try:
            repo.migrate()
            repo.save_position(Position('error','BTC/EUR',PositionStatus.ERROR,D('1'),D('10'),D('9'),'01',START.isoformat()))
            self.assertEqual(len(repo.portfolio_positions()),1)
            with self.assertRaises(ValueError): observe_wallet(self.state,repo,self.snapshots,self.strategy,now=NOW)
        finally: repo.close()

    def test_entry_budget_blocks_unknown_equity_but_protective_exits_continue(self):
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.positions import PositionEngine
        from hermes_trading.domain import Position,PositionStatus
        from hermes_trading.strategy import StrategySignal
        from dataclasses import replace
        import test_positions as positions
        repo=StateRepository.open(self.state/'trading.db')
        try:
            repo.migrate()
            snapshot=replace(positions.candle(NOW,open='11',low='8',close='11'),asset='BTC/EUR')
            strategy={**positions.entry_strategy(),'simulation':{'maker_fee_rate':'0','taker_fee_rate':'0','slippage_rate':'0','minimum_expected_edge_pct':'0'}}
            engine=PositionEngine(repo,equity=D('0'),entries_allowed=False)
            signal=StrategySignal('enter','BTC/EUR','test',D('11'),snapshot.candle_identity.candle_id)
            result=engine.process_candle(snapshot,strategy,signal_override=signal)
            self.assertEqual(result.decision.reason,'wallet_measurement_incomplete')
            self.assertEqual(result.orders,())
            repo.save_position(Position('open','BTC/EUR',PositionStatus.OPEN,D('1'),D('10'),D('9'),'01',START.isoformat()))
            next_snapshot=replace(snapshot,candle=replace(snapshot.candle,close_timestamp=NOW+timedelta(hours=1)))
            result=engine.process_candle(next_snapshot,strategy)
            self.assertEqual(len(result.closed_positions),1)
        finally: repo.close()

    def test_pre_experiment_candle_cannot_create_a_backdated_position(self):
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.positions import PositionEngine
        from hermes_trading.strategy import StrategySignal
        from dataclasses import replace
        import test_positions as positions
        repo=StateRepository.open(self.state/'trading.db')
        try:
            repo.migrate()
            snapshot=replace(positions.candle(NOW,open='11',low='10',close='11'),asset='BTC/EUR')
            engine=PositionEngine(repo,equity=D('100'),entry_not_before=NOW+timedelta(seconds=1))
            strategy={**positions.entry_strategy(),'simulation':{'maker_fee_rate':'0','taker_fee_rate':'0','slippage_rate':'0','minimum_expected_edge_pct':'0'}}
            result=engine.process_candle(snapshot,strategy,signal_override=StrategySignal('enter','BTC/EUR','test',D('11'),snapshot.candle_identity.candle_id))
            self.assertEqual(result.decision.reason,'pre_experiment_candle')
            self.assertEqual(repo.portfolio_positions(),())
        finally: repo.close()


    def test_initialized_wallet_cannot_fall_back_when_configuration_disappears(self):
        self.observe()
        (self.state/'wallet_account.json').unlink()
        with self.assertRaisesRegex(ValueError,'configuration'):
            self.observe()

    def test_unrealized_gains_cannot_fund_entries_and_cash_updates_after_fills(self):
        from dataclasses import replace
        from hermes_trading.domain import Position, PositionStatus
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.positions import PositionEngine, SimulationSettings
        repo=StateRepository.open(self.state/'cash.db'); repo.migrate()
        first=Position('cash-first','BTC/EUR',PositionStatus.OPEN,D('5'),D('10'),D('8'),'1',START.isoformat(),fees=D('0'))
        try:
            repo.save_position(first)
            engine=PositionEngine(repo,equity=D('200'),wallet_cash_capital=D('100'))
            settings=replace(SimulationSettings(),risk_per_trade_pct=D('1'),max_order_quote=D('1000'),
                             max_symbol_exposure_pct=D('1'),max_total_exposure_pct=D('1'),
                             taker_fee_rate=D('.01'),slippage_rate=D('0'))
            strategy={'position_size_r':'1'}
            self.assertEqual(engine._entry_notional(strategy,settings,D('10'),D('9')),D('50')/D('1.01'))
            second=replace(first,position_id='cash-second',asset='ETH/EUR',quantity=D('2'),fees=D('.2'))
            repo.save_position(second)
            self.assertEqual(engine._entry_notional(strategy,settings,D('10'),D('9')),D('29.8')/D('1.01'))
            repo.save_position(replace(second,status=PositionStatus.CLOSED,closed_at=NOW.isoformat(),realized_pnl=D('10')))
            self.assertEqual(engine._entry_notional(strategy,settings,D('10'),D('9')),D('60')/D('1.01'))
        finally: repo.close()


    def test_liquidation_mark_uses_original_entry_costs_after_strategy_change(self):
        import yaml
        old={'version':1,'simulation':{'taker_fee_rate':'.02','slippage_rate':'.01'}}
        (self.state/'history').mkdir(); (self.state/'history/v0001.yaml').write_text(yaml.safe_dump(old))
        self.strategy={'version':2,'simulation':{'taker_fee_rate':'0','slippage_rate':'0'}}
        observation=self.observe([self.position()])['observation']
        self.assertEqual(D(observation['net_equity']),D('100.5722'))
        self.assertEqual(observation['mark_provenance']['BTC/EUR']['exit_fee_rate'],'0.02')
        self.assertEqual(observation['mark_entry_versions'],{'BTC/EUR':'1'})
        (self.state/'history/v0001.yaml').unlink()
        with self.assertRaises(ValueError): self.observe([self.position()])


if __name__=='__main__': unittest.main()
