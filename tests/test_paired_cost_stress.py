from datetime import datetime,timedelta,timezone
from decimal import Decimal
import unittest

from hermes_trading.paired_cost_stress import FixedOrderCostStress, completed_entry_lineages

START=datetime(2026,1,1,tzinfo=timezone.utc)


class FixedCostStressTests(unittest.TestCase):
    def order(self,side,price,fee,quantity='1'):
        return {'at':START.isoformat(),'asset':'ETH/EUR','side':side,'quantity':quantity,
                'fill_price':price,'fee':fee,'fee_rate':'.01','slippage_rate':'.01'}

    def observation(self,cash,liquidation,cost,net,quantity='1'):
        return {'at':START.isoformat(),'currency':'EUR','valuation_model':'long_spot_net_v1',
            'starting_balance':'100','external_flow':'0','trading_cash':cash,
            'open_liquidation_value':liquidation,'known_accrued_costs':cost,'net_equity':net,'missing':[],
            'cost_provenance':{name:{'amount':cost if name=='hosting' else '0','quality':'actual','source':'test'}
                               for name in ('hosting','ai','tools','funding')},
            'mark_provenance':{'ETH/EUR':{'bid':'10','observed_at':START.isoformat(),
                                 'exit_fee_rate':'.01','slippage_rate':'.01'}} if quantity!='0' else {}}

    def test_remaining_inventory_uses_doubled_fee_slippage_cross_terms(self):
        stress=FixedOrderCostStress('100')
        stress.apply_order(self.order('buy','10.1','.101'))
        row=stress.observe(self.observation('89.799','9.801','.5','99.1'))
        self.assertEqual(Decimal(row['net_equity']),Decimal('98.2'))
        self.assertEqual(Decimal(row['trading_cash']),Decimal('89.596'))
        self.assertEqual(Decimal(row['open_liquidation_value']),Decimal('9.604'))

    def test_full_round_trip_and_operating_cost_are_doubled_once(self):
        stress=FixedOrderCostStress('100')
        stress.apply_order(self.order('buy','10.1','.101'))
        stress.apply_order(self.order('sell','10.89','.1089'))
        row=stress.observe(self.observation('100.5801','0','1','99.5801',quantity='0'))
        self.assertEqual(Decimal(row['net_equity']),Decimal('98.1604'))
        self.assertEqual(Decimal(row['known_accrued_costs']),Decimal('2'))

    def test_extra_cost_can_make_an_originally_affordable_order_infeasible(self):
        stress=FixedOrderCostStress('100')
        stress.apply_order(self.order('buy','10.1','.9898','9.8'))
        self.assertFalse(stress.cash_feasible)
        self.assertEqual(Decimal(stress.stressed_cash),Decimal('-1.9592'))

    def test_original_fee_and_quantity_must_reconcile(self):
        stress=FixedOrderCostStress('100')
        with self.assertRaisesRegex(ValueError,'fee'):stress.apply_order(self.order('buy','10.1','0'))
        with self.assertRaisesRegex(ValueError,'inventory'):stress.apply_order(self.order('sell','10.89','.1089'))

    def test_accrued_operating_costs_reserve_standalone_cash(self):
        stress=FixedOrderCostStress('100')
        order=self.order('buy','10','0','9')
        order.update(fee_rate='0',slippage_rate='0')
        stress.apply_order(order)
        observation=self.observation('10','90','6','94')
        observation['mark_provenance']['ETH/EUR'].update(exit_fee_rate='0',slippage_rate='0')
        row=stress.observe(observation)
        self.assertEqual(Decimal(row['net_equity']),Decimal('88'))
        self.assertFalse(row['stressed_cash_feasible'])
        self.assertEqual(row['stressed_spendable_cash'],'-2')
        self.assertEqual(stress.cash_failures[-1]['spendable_cash'],'-2')

    def test_subsequent_orders_reserve_already_known_operating_costs(self):
        stress=FixedOrderCostStress('100')
        stress.observe(self.observation('100','0','6','94',quantity='0'))
        order=self.order('buy','10','0','9')
        order.update(fee_rate='0',slippage_rate='0')
        stress.apply_order(order)
        self.assertFalse(stress.cash_feasible)
        self.assertEqual(stress.cash_failures[-1]['spendable_cash'],'-2')

    def test_observation_cannot_hide_a_cash_mismatch(self):
        stress=FixedOrderCostStress('100')
        with self.assertRaisesRegex(ValueError,'cash'):
            stress.observe(self.observation('99','0','0','99',quantity='0'))


class EntryLineageTests(unittest.TestCase):
    def inputs(self):
        entry={'position_id':'root','asset':'ETH/EUR','opened_at':START.isoformat(),
               'entry_price':'10','strategy_version':'1','quantity':'1'}
        partial={**entry,'position_id':'leg','quantity':'.5','status':'closed',
                 'closed_at':(START+timedelta(days=20)).isoformat(),'exit_reason':'partial_exit'}
        root={**entry,'quantity':'.5','status':'closed','closed_at':(START+timedelta(days=100)).isoformat(),
              'exit_reason':'stop_loss'}
        return [partial,root],[entry]

    def test_partial_legs_are_one_completed_original_entry(self):
        positions,entries=self.inputs()
        report=completed_entry_lineages(positions,entries,start=START,end=START+timedelta(days=180))
        self.assertEqual(report['completed_by_90day_block'],[0,1])
        self.assertEqual(report['completed'],1)

    def test_open_remainder_cannot_count_banked_partial_as_completed_trade(self):
        positions,entries=self.inputs();positions[1].update(status='open',closed_at=None)
        report=completed_entry_lineages(positions,entries,start=START,end=START+timedelta(days=180))
        self.assertEqual(report['completed'],0)

    def test_ambiguous_entry_and_missing_quantity_are_rejected(self):
        positions,entries=self.inputs()
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            completed_entry_lineages(positions,entries+[dict(entries[0],position_id='other')],start=START,end=START+timedelta(days=180))
        positions[0]['quantity']='.4'
        with self.assertRaisesRegex(ValueError,'quantity'):
            completed_entry_lineages(positions,entries,start=START,end=START+timedelta(days=180))

    def test_recovery_closes_are_excluded_and_reported(self):
        for reason in ('entry_contract_recovery_exit','paired_drawdown_stop','paired_storage_stop'):
            with self.subTest(reason=reason):
                positions,entries=self.inputs();positions[1]['exit_reason']=reason
                report=completed_entry_lineages(positions,entries,start=START,end=START+timedelta(days=180))
                self.assertEqual(report['completed'],0)
                self.assertEqual(report['excluded_recovery_lineages'],['root'])
