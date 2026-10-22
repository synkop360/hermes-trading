from decimal import Decimal
import unittest

from hermes_trading.execution_policy import initial_stop_price, initial_take_profit_price
from hermes_trading.market_data import MarketSnapshot
from hermes_trading.paper_strategy_proposal import apply_paper_strategy_proposal


class PaperStrategyProposalTests(unittest.TestCase):
    def test_percentage_stop_changes_are_rejected_when_atr_stop_key_is_present(self):
        for multiple in ('2.5', None, 0, False):
            for asset in (None, 'BTC/EUR'):
                strategy={'stop_loss_pct':2,'exit':{'stop_atr':multiple},
                          'symbols':{'BTC/EUR':{},'ETH/EUR':{}}}
                with self.subTest(multiple=multiple,asset=asset),self.assertRaisesRegex(ValueError,'effective'):
                    apply_paper_strategy_proposal(strategy,
                        {'asset':asset,'variable':'stop_loss_pct','value':2.2})
                self.assertEqual(strategy['stop_loss_pct'],2)
        with self.assertRaisesRegex(ValueError,'effective'):
            apply_paper_strategy_proposal({'stop_loss_pct':2,'exit':{'stop_atr':'2'}},
                {'variable':'stop_loss_pct','value':2.2})

    def test_global_percentage_stop_change_requires_an_affected_active_percentage_asset(self):
        strategy={'stop_loss_pct':2,'symbols':{
            'BTC/EUR':{'exit':{'stop_atr':'2'}},'ETH/EUR':{}}}
        proposal={'variable':'stop_loss_pct','value':2.2}
        with self.assertRaisesRegex(ValueError,'effective'):
            apply_paper_strategy_proposal(strategy,proposal,assets=['BTC/EUR'])
        with self.assertRaisesRegex(ValueError,'effective'):
            apply_paper_strategy_proposal(strategy,dict(proposal,asset='BTC/EUR'))
        strategy['symbols']['ETH/EUR']['stop_loss_pct']=2.2
        with self.assertRaisesRegex(ValueError,'effective'):
            apply_paper_strategy_proposal(strategy,proposal)
        strategy['symbols']['ETH/EUR'].clear()
        updated,_,_=apply_paper_strategy_proposal(strategy,proposal)
        from hermes_trading.loop import resolve_asset_strategy
        snapshot=MarketSnapshot('ETH/EUR','1h','available','evaluate','test',
            price={'candles':[{'high':'101','low':'99','close':'100'} for _ in range(15)]})
        self.assertEqual(initial_stop_price(snapshot,resolve_asset_strategy(updated,'ETH/EUR'),Decimal('100')),Decimal('97.8'))
        self.assertEqual(initial_stop_price(snapshot,resolve_asset_strategy(updated,'BTC/EUR'),Decimal('100')),Decimal('96'))
        scoped,_,_=apply_paper_strategy_proposal(strategy,dict(proposal,asset='ETH/EUR'))
        self.assertEqual(initial_stop_price(snapshot,resolve_asset_strategy(scoped,'ETH/EUR'),Decimal('100')),Decimal('97.8'))
        self.assertEqual(strategy['stop_loss_pct'],2)

    def test_null_override_does_not_make_a_shadowed_global_change_effective(self):
        for override in ({'max_bars':None}, None):
            with self.subTest(override=override),self.assertRaisesRegex(ValueError,'effective'):
                apply_paper_strategy_proposal(
                    {'exit':{'max_bars':20},'symbols':{'ETH/EUR':{'exit':override}}},
                    {'variable':'exit.max_bars','value':21})

    def test_global_change_shadowed_on_every_asset_is_rejected(self):
        strategy={'entry':{'threshold':26},'symbols':{
            'ETH/EUR':{'entry':{'threshold':31}},
            'XRP/EUR':{'entry':{'threshold':'35.0'}}}}
        with self.assertRaisesRegex(ValueError,'effective'):
            apply_paper_strategy_proposal(strategy,{'variable':'entry.threshold','value':27})
        self.assertEqual(strategy['entry']['threshold'],26)

    def test_global_change_can_affect_one_asset_without_overwriting_other_overrides(self):
        strategy={'entry':{'threshold':26},'symbols':{
            'ETH/EUR':{'entry':{'threshold':31}},'XRP/EUR':{}}}
        updated,_,_=apply_paper_strategy_proposal(strategy,{'variable':'entry.threshold','value':27})
        from hermes_trading.loop import resolve_asset_strategy
        self.assertEqual(resolve_asset_strategy(updated,'XRP/EUR')['entry']['threshold'],27)
        self.assertEqual(resolve_asset_strategy(updated,'ETH/EUR')['entry']['threshold'],31)

    def test_unused_symbol_cannot_make_an_inert_global_change_appear_effective(self):
        strategy={'entry':{'threshold':26},'symbols':{
            'ETH/EUR':{'entry':{'threshold':31}},'XRP/EUR':{}}}
        with self.assertRaisesRegex(ValueError,'effective'):
            apply_paper_strategy_proposal(strategy,{'variable':'entry.threshold','value':27},
                                          assets=['ETH/EUR'])

    def test_configured_asset_without_an_override_inherits_global_change(self):
        strategy={'entry':{'threshold':26},'symbols':{
            'ETH/EUR':{'entry':{'threshold':31}}}}
        updated,_,_=apply_paper_strategy_proposal(strategy,{'variable':'entry.threshold','value':27},
                                                 assets=['ETH/EUR','XRP/EUR'])
        self.assertEqual(updated['entry']['threshold'],27)

    def test_symbol_proposal_must_target_an_active_asset(self):
        strategy={'entry':{'threshold':26},'symbols':{'ETH/EUR':{},'XRP/EUR':{}}}
        with self.assertRaisesRegex(ValueError,'active'):
            apply_paper_strategy_proposal(strategy,{'asset':'XRP/EUR','variable':'entry.threshold','value':27},
                                          assets=['ETH/EUR'])

    def test_fractional_atr_settings_drive_actual_price_policy(self):
        strategy={'exit':{'stop_atr':'2','take_profit_atr':'4'}}
        # Constant H/L range of 2 makes the 14-period ATR exactly 2.
        snapshot=MarketSnapshot('ETH/EUR','1h','available','evaluate','test',
            price={'candles':[{'high':'101','low':'99','close':'100'} for _ in range(15)]})
        for variable,value,expected,consumer in (
            ('exit.stop_atr',2.5,Decimal('95'),initial_stop_price),
            ('exit.take_profit_atr',4.5,Decimal('109'),initial_take_profit_price),
        ):
            with self.subTest(variable=variable):
                updated,_,_=apply_paper_strategy_proposal(strategy,{'variable':variable,'value':value})
                self.assertEqual(consumer(snapshot,updated,Decimal('100')),expected)
                self.assertEqual(strategy,{'exit':{'stop_atr':'2','take_profit_atr':'4'}})

    def test_fractional_stop_percent_drives_actual_price_policy(self):
        updated,_,_=apply_paper_strategy_proposal({'stop_loss_pct':2},
            {'variable':'stop_loss_pct','value':2.2})
        snapshot=MarketSnapshot('ETH/EUR','1h','available','evaluate','test')
        self.assertEqual(initial_stop_price(snapshot,updated,Decimal('100')),Decimal('97.8'))

    def test_whole_bar_count_is_preserved(self):
        updated,_,_=apply_paper_strategy_proposal({'exit':{'max_bars':'20'}},
            {'variable':'exit.max_bars','value':21.0})
        self.assertIs(type(updated['exit']['max_bars']),int)
        self.assertEqual(updated['exit']['max_bars'],21)

    def test_invalid_and_excess_step_values_are_rejected(self):
        for value in (True,False,None,'no',float('nan'),float('inf'),'-Infinity',2.20001,0,-1):
            with self.subTest(value=value),self.assertRaises(ValueError):
                apply_paper_strategy_proposal({'stop_loss_pct':2},{'variable':'stop_loss_pct','value':value})

    def test_normalized_numeric_strings_reconstruct_same_candidate(self):
        base={'entry':{'threshold':'30'}}
        first,_,_=apply_paper_strategy_proposal(base,{'variable':'entry.threshold','value':31.5})
        replay,_,_=apply_paper_strategy_proposal(base,{'variable':'entry.threshold','value':'31.500'})
        self.assertEqual(first,replay)
        self.assertEqual(first['entry']['threshold'],'31.5')
