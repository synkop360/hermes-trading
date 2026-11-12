from copy import deepcopy
from datetime import UTC,datetime,timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock,patch
import yaml

from hermes_trading.entry_contracts import EntryContractResolver, EntryContractError
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.positions import PositionEngine
from hermes_trading.strategy import StrategySignal
from test_positions import candle,entry_strategy

class EntryContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.repo=StateRepository.open(self.root/'trading.db'); self.repo.migrate()
        self.v1={**entry_strategy(),'version':1,'exit':{'take_profit_pct':'20'},
                 'simulation':{'taker_fee_rate':'0','maker_fee_rate':'0','slippage_rate':'0','minimum_expected_edge_pct':'0'}}
        self.v2=deepcopy(self.v1); self.v2['version']=2; self.v2['exit']['take_profit_pct']='1'
        self.start=datetime(2026,10,10,tzinfo=UTC)
        self.snapshot=candle(self.start,open='80',low='80',close='80')
        PositionEngine(self.repo).process_candle(self.snapshot,self.v1,
            signal_override=StrategySignal('enter','BTC/USDT','test_entry',Decimal('80'),self.snapshot.candle_identity.candle_id))
        (self.root/'history').mkdir()
        self.archive=self.root/'history/v0001.yaml'; self.archive.write_text(yaml.safe_dump(self.v1))
    def tearDown(self):
        self.repo.close(); self.tmp.cleanup()
    def engine(self): return PositionEngine(self.repo,entry_contract_resolver=EntryContractResolver(self.root,self.v2))
    def test_original_exit_rule_and_version_survive_restart_and_new_entries_use_current(self):
        self.repo.close(); self.repo=StateRepository.open(self.root/'trading.db'); self.repo.migrate()
        second=candle(self.start+timedelta(hours=1),open='81',low='81',close='81')
        result=self.engine().process_candle(second,self.v2)
        self.assertEqual(result.position.status.value,'open')
        self.assertEqual(result.decision.strategy_version,'1')
        third=candle(self.start+timedelta(hours=2),open='97',low='96',close='97')
        result=self.engine().process_candle(third,self.v2)
        self.assertEqual(result.reason,'take_profit'); self.assertEqual(result.position.status.value,'closed')
        self.assertEqual(result.position.strategy_version,'1')
        fourth=candle(self.start+timedelta(hours=3),open='80',low='80',close='80')
        result=self.engine().process_candle(fourth,self.v2,
            signal_override=StrategySignal('enter','BTC/USDT','test_entry',Decimal('80'),fourth.candle_identity.candle_id))
        self.assertEqual(result.position.strategy_version,'2')
    def test_missing_archive_forces_explicit_paper_recovery_exit(self):
        self.archive.unlink()
        snapshot=candle(self.start+timedelta(hours=1),open='81',low='81',close='81')
        engine=self.engine(); result=engine.process_candle(snapshot,self.v2)
        self.assertEqual(result.reason,'entry_contract_recovery_exit')
        self.assertEqual(result.position.status.value,'closed')
        self.assertIsNotNone(result.contract_error)
        self.assertFalse(engine.entries_allowed)
        self.assertEqual(result.decision.strategy_version,'1')
    def test_bad_archives_and_version_paths_are_rejected(self):
        resolver=EntryContractResolver(self.root,self.v2)
        for content in ('[]','{version: 9}','{version: 1, symbols: {BTC/USDT: {version: 2}}}'):
            self.archive.write_text(content)
            with self.subTest(content=content), self.assertRaises(EntryContractError): resolver('BTC/USDT','1')
        with self.assertRaises(EntryContractError): resolver('BTC/USDT','../1')
    def test_resolver_retains_original_asset_override_and_returns_detached_contract(self):
        original=deepcopy(self.v1); original['symbols']={'BTC/USDT':{'exit':{'take_profit_pct':'25'}}}
        self.archive.write_text(yaml.safe_dump(original)); resolver=EntryContractResolver(self.root,self.v2)
        first=resolver('BTC/USDT','1'); self.assertEqual(first['exit']['take_profit_pct'],'25')
        first['exit']['take_profit_pct']='99'
        self.assertEqual(resolver('BTC/USDT','1')['exit']['take_profit_pct'],'25')


    def test_worker_preflight_blocks_other_entries_and_marks_recovery_degraded(self):
        from hermes_trading.loop import run_cycle
        from hermes_trading.persistence.artifacts import ArtifactProjector
        self.archive.write_text('{version: 1, exit: {partial_close_pct: 0.4}}')
        snapshot=candle(self.start+timedelta(hours=1),open='81',low='81',close='81')
        market=AsyncMock(); market.collect.return_value={'ETH/USDT':replace(snapshot,asset='ETH/USDT'),'BTC/USDT':snapshot}
        valuation={'net_equity':'100','starting_balance':'100','known_accrued_costs':'0','activated_at':self.start.isoformat()}
        with patch('hermes_trading.wallet_observer.value_current_wallet',return_value=valuation):
            result=asyncio.run(run_cycle(['ETH/USDT','BTC/USDT'],self.repo,ArtifactProjector(self.root),market,self.v2,'training',wallet_state_dir=self.root))
        self.assertIsNone(self.repo.open_position('ETH/USDT'))
        self.assertTrue(result.degraded)
        self.assertEqual(result.results[1].reason,'entry_contract_recovery_exit')


    def test_numeric_yaml_fees_preserve_decimal_contract_and_original_exit_costs(self):
        original=deepcopy(self.v1); original['simulation']['taker_fee_rate']=0.008
        original['simulation']['slippage_rate']=0.01
        self.archive.write_text(yaml.safe_dump(original))
        contract=EntryContractResolver(self.root,self.v2)('BTC/USDT','1')
        self.assertEqual(contract['simulation']['taker_fee_rate'],Decimal('.008'))
        snapshot=candle(self.start+timedelta(hours=1),open='97',low='96',close='97')
        result=self.engine().process_candle(snapshot,self.v2)
        self.assertEqual(result.position.status.value,'closed')
        self.assertEqual(result.orders[0].average_fill_price,Decimal('97')*Decimal('.99'))
        self.assertGreater(result.position.fees,Decimal('0'))
    def test_malformed_execution_settings_fail_preflight(self):
        self.archive.write_text('{version: 1, simulation: []}')
        with self.assertRaises(EntryContractError): EntryContractResolver(self.root,self.v2)('BTC/USDT','1')


    def test_original_partial_fraction_lineage_and_trailing_rule_survive_change(self):
        original=deepcopy(self.v1); original['exit']={'indicator':'rsi','threshold':'50','partial_close_pct':'.4','trailing_atr':'1.5'}
        self.archive.write_text(yaml.safe_dump(original))
        changed=deepcopy(self.v2); changed['exit']={'indicator':'rsi','threshold':'50','partial_close_pct':'.9','trailing_atr':'.5'}
        engine=PositionEngine(self.repo,entry_contract_resolver=EntryContractResolver(self.root,changed))
        snapshot=candle(self.start+timedelta(hours=1),open='85',low='84',close='85')
        original_quantity=self.repo.open_position('BTC/USDT').quantity
        result=engine.process_candle(snapshot,changed,signal_override=StrategySignal('exit','BTC/USDT','partial_exit',Decimal('85'),snapshot.candle_identity.candle_id))
        self.assertEqual(result.closed_positions[0].quantity,original_quantity*Decimal('.4'))
        self.assertEqual(result.closed_positions[0].strategy_version,'1')
        self.assertEqual(result.position.partial_exit_count,1)
        self.assertEqual(result.position.quantity,original_quantity*Decimal('.6'))
        history=tuple({'high':Decimal('82'),'low':Decimal('78'),'close':Decimal('80')} for _ in range(15))
        snapshot=candle(self.start+timedelta(hours=2),open='88',low='85',high='91',close='90',history=history)
        result=engine.process_candle(snapshot,changed,signal_override=StrategySignal('hold','BTC/USDT','position_open',Decimal('90'),snapshot.candle_identity.candle_id))
        self.assertEqual(result.position.stop_price,Decimal('84'))
        self.assertEqual(result.position.strategy_version,'1')


    def test_exit_cross_fields_and_numeric_parameters_are_validated_before_processing(self):
        for rule in ({'partial_close_pct':'.4'},{'partial_close_pct':'.4','trailing_atr':'1','trailing_overrides_explicit_exit':True},
                     {'indicator':'rsi','threshold':'bad'},{'trailing_atr':'1','trailing_activation_atr':'bad'}):
            original=deepcopy(self.v1); original['exit']=rule; self.archive.write_text(yaml.safe_dump(original))
            with self.subTest(rule=rule), self.assertRaises(EntryContractError):
                EntryContractResolver(self.root,self.v2)('BTC/USDT','1')


    def test_recovery_trade_classification_survives_projection_restart_and_rebuild(self):
        from hermes_trading.persistence.artifacts import ArtifactProjector
        self.archive.unlink(); projector=ArtifactProjector(self.root)
        engine=PositionEngine(self.repo,projector,entry_contract_resolver=EntryContractResolver(self.root,self.v2))
        snapshot=candle(self.start+timedelta(hours=1),open='81',low='81',close='81')
        result=engine.process_candle(snapshot,self.v2)
        self.assertIsNone(result.projection_error)
        target=self.root/'trades.jsonl'; record=json.loads(target.read_text().strip())
        self.assertEqual(record['evidence_classification'],'operational_recovery')
        self.assertEqual(record['exit_reason'],'entry_contract_recovery_exit')
        self.repo.close(); self.repo=StateRepository.open(self.root/'trading.db'); self.repo.migrate()
        target.unlink(); projector.reconcile(self.repo)
        rebuilt=json.loads(target.read_text().strip())
        self.assertEqual(rebuilt['evidence_classification'],'operational_recovery')
        self.assertEqual(rebuilt['exit_decision_id'],record['exit_decision_id'])

if __name__=='__main__': unittest.main()
