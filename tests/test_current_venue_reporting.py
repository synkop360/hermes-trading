import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import unittest
from types import SimpleNamespace

import test_wallet_observer as observer_fixtures
import test_paired_source_validation as source_fixtures
from hermes_trading.wallet_observer import value_current_wallet
from hermes_trading.paired_paper import _decode,market_source_status,_snapshot
from hermes_trading.paired_cost_stress import FixedOrderCostStress
from hermes_trading.entry_cost_reserve import reserved_quantity
from venue_fixtures import venue_record


class CurrentVenueValuationTests(unittest.TestCase):
    def setUp(self):
        self.f=observer_fixtures.ObserverTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.at=observer_fixtures.NOW
        self.position=dict(self.f.position(),asset='ETH/EUR',quantity='0.0015',entry_price='2000',fees='0.024')

    def value(self,record):
        snapshots={'ETH/EUR':SimpleNamespace(price={'bid':'1800','received_at':self.at.isoformat(),'source':'synthetic'},venue_rules=record)}
        return value_current_wallet(self.f.state,observer_fixtures.Repository([self.position]),snapshots,self.f.strategy,now=self.at)

    def test_dust_retains_marked_value_but_has_no_complete_liquidation_equity(self):
        observation=self.value(venue_record('ETH/EUR',self.at))
        self.assertEqual(Decimal(observation['marked_inventory_value']),Decimal('2.7'))
        self.assertEqual(Decimal(observation['open_basis']),Decimal('3'))
        self.assertFalse(observation['liquidation_complete'])
        self.assertIsNone(observation['net_equity']);self.assertIsNone(observation['open_liquidation_value'])
        self.assertIn('liquidation:ETH/EUR',observation['missing'])

    def test_valid_original_fee_contract_and_missing_stale_future_records(self):
        valid=self.value(venue_record('ETH/EUR',self.at,minimum_quantity='0.001'))
        self.assertTrue(valid['liquidation_complete']);self.assertIsNotNone(valid['net_equity'])
        self.assertEqual(valid['mark_provenance']['ETH/EUR']['exit_fee_rate'],'0.01')
        for record in [None,venue_record('ETH/EUR',self.at-timedelta(seconds=300)),
                       venue_record('ETH/EUR',self.at+timedelta(seconds=1))]:
            with self.subTest(record=record):
                value=self.value(record)
                self.assertFalse(value['liquidation_complete']);self.assertIsNone(value['net_equity'])
                self.assertEqual(Decimal(value['marked_inventory_value']),Decimal('2.7'))

    def test_current_metadata_preserves_a_feasible_cost_reserved_entry(self):
        from dataclasses import replace
        from test_entry_cost_reserve import EntryCostReserveTests
        f=EntryCostReserveTests();f.setUp()
        at=f.snapshot.candle.close_timestamp
        snapshot=replace(f.snapshot,venue_rules=venue_record('ETH/EUR',at))
        quantity=reserved_quantity(f.strategy,snapshot,Decimal('10.010'),Decimal('.32861874'),
            at=at,**f.context)
        self.assertEqual(quantity,Decimal('.22276874'))


class CurrentVenueSourceTests(unittest.TestCase):
    def setUp(self):
        self.f=source_fixtures.PrimarySourceValidationTests();self.f.setUp();self.addCleanup(self.f.tearDown)

    def test_invalid_filled_entry_is_rejected_even_when_quote_cash_and_fee_are_consistent(self):
        order=deepcopy(self.f.order);order['quantity']='0.0015'
        order['fee']=str(Decimal(order['quantity'])*Decimal(order['fill_price'])*Decimal(order['fee_rate']))
        with self.assertRaisesRegex(ValueError,'venue'):
            self.f.validator.validate_fill(self.f.packet,'adaptive_100',order)

    def test_partial_sell_requires_a_valid_leg_and_executable_remainder(self):
        order=dict(self.f.order,side='sell',quantity='0.002')
        with self.assertRaisesRegex(ValueError,'venue'):
            self.f.validator.validate_fill(self.f.packet,'adaptive_100',order,
                holdings={'ETH/EUR':{'quantity':Decimal('0.003')}})

    def test_legacy_packet_remains_auditable_without_qualifying_dated_execution(self):
        packet=deepcopy(self.f.packet);packet['schema_version']=1
        for row in packet['snapshots'].values():row.pop('venue_rules',None)
        packet['market_sources']=market_source_status({asset:_snapshot(row) for asset,row in packet['snapshots'].items()},
            self.f.fixture.now,packet_schema=1)
        before=deepcopy(packet)
        self.f.validator.validate_packet(packet)
        self.assertEqual(packet,before);self.assertFalse(self.f.validator.dated_venue_complete)

    def test_cost_stress_rechecks_liquidation_constraints(self):
        engine=FixedOrderCostStress('100');engine.apply_order(self.f.order)
        observation=deepcopy(self.f.observation)
        observation['mark_provenance']['ETH/EUR']['venue_rules']=venue_record('ETH/EUR',self.f.fixture.now,minimum_quantity='1')
        with self.assertRaisesRegex(ValueError,'venue'):engine.observe(observation)


if __name__=='__main__':unittest.main()
