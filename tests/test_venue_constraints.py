"""Literal executable limits, independent of strategy configuration."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import unittest

from hermes_trading.venue_constraints import (
    VenueConstraintsError, constraint_record, parse_venue_constraints,
)


class VenueConstraintsTests(unittest.TestCase):
    def setUp(self):
        self.at = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.pair = {'altname': 'ETHEUR', 'wsname': 'ETH/EUR', 'base': 'XETH',
                     'quote': 'ZEUR', 'aclass_base': 'currency', 'aclass_quote': 'currency',
                     'lot': 'unit', 'lot_multiplier': 1, 'lot_decimals': 8,
                     'ordermin': '0.002', 'costmin': '0.45', 'status': 'online',
                     'fees': [], 'fees_maker': []}
        self.url = 'https://api.kraken.com/0/public/AssetPairs?pair=ETHEUR&country_code=FR'

    def record(self):
        return constraint_record(self.pair, asset='ETH/EUR', received_at=self.at, source_url=self.url)

    def parse(self, record=None, **kwargs):
        return parse_venue_constraints(record if record is not None else self.record(),
                                       asset='ETH/EUR', at=kwargs.pop('at', self.at), **kwargs)

    @staticmethod
    def resign(record):
        record['source_sha256'] = hashlib.sha256(json.dumps(record['source_pair'], sort_keys=True,
            separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        return record

    def test_literal_order_minimum_precision_and_quote_cost(self):
        rules = self.parse()
        self.assertEqual((rules.minimum_quantity, rules.minimum_quote, rules.quantity_decimals),
                         (Decimal('0.002'), Decimal('0.45'), 8))
        for quantity, price, valid in [('0.0015', '2000', False), ('0.002', '2000', True),
                                       ('0.002', '200', False), ('0.002000001', '2000', False),
                                       ('0.003', '150', True), ('0', '2000', False)]:
            with self.subTest(quantity=quantity, price=price):
                if valid:rules.validate_order(Decimal(quantity), Decimal(price))
                else:
                    with self.assertRaises(VenueConstraintsError):
                        rules.validate_order(Decimal(quantity), Decimal(price))
        self.assertEqual(rules.quantize_entry(Decimal('0.002000009')), Decimal('0.00200000'))
        self.assertEqual(rules.quantize_entry(Decimal('0.001999999')), Decimal('0.00199999'))

    def test_age_boundary_future_and_naive_dates_rejected(self):
        self.parse(at=self.at+timedelta(seconds=299, microseconds=999999))
        for at in [self.at+timedelta(seconds=300), self.at-timedelta(microseconds=1),
                   self.at.replace(tzinfo=None), 'not a timestamp']:
            with self.subTest(at=at), self.assertRaises(VenueConstraintsError):self.parse(at=at)
        for bad_age in [True, 0, -1, float('inf'), '300']:
            with self.subTest(age=bad_age), self.assertRaises(VenueConstraintsError):
                self.parse(max_age_seconds=bad_age)

    def test_digest_purpose_scope_and_exact_record_fields_bound(self):
        changes = [('purpose','private_account_permission'), ('asset','BTC/EUR'),
                   ('country_code','US'), ('quote_currency','USD'), ('exchange','other'),
                   ('schema_version',True), ('source_sha256','0'*64), ('unexpected',0),
                   ('received_at',self.at.replace(tzinfo=None).isoformat()),
                   ('source_url','http://api.kraken.com/0/public/AssetPairs?pair=ETHEUR&country_code=FR'),
                   ('source_url','https://api.kraken.com.evil.test/0/public/AssetPairs?pair=ETHEUR&country_code=FR'),
                   ('source_url','https://api.kraken.com/0/public/AssetPairs?pair=XBTEUR&country_code=FR'),
                   ('source_url',self.url+'&country_code=US')]
        for key,value in changes:
            record = self.record();record[key] = value
            with self.subTest(key=key,value=value), self.assertRaises(VenueConstraintsError):self.parse(record)
        record=self.record();record['source_pair']['ordermin']='0.0001'
        with self.assertRaises(VenueConstraintsError):self.parse(record)

    def test_original_pair_derived_fields_are_strict(self):
        changes=[('ordermin',True),('ordermin',0.002),('ordermin','NaN'),('ordermin','Infinity'),
                 ('ordermin','0'),('costmin','-1'),('lot_decimals',True),('lot_decimals',8.0),
                 ('lot_decimals',19),('lot','contract'),('lot_multiplier',True),('lot_multiplier',2),
                 ('status','cancel_only'),('base','XXBT'),('wsname','ETH/USD'),('quote','ZUSD'),
                 ('aclass_base','tokenized_asset')]
        for key,value in changes:
            record=self.record();record['source_pair'][key]=value;self.resign(record)
            with self.subTest(key=key,value=value), self.assertRaises(VenueConstraintsError):self.parse(record)

    def test_btc_alias_and_full_multi_pair_public_scope(self):
        pair=deepcopy(self.pair);pair.update(altname='XBTEUR',wsname='XBT/EUR',base='XXBT',ordermin='0.00006')
        record=constraint_record(pair,asset='BTC/EUR',received_at=self.at,
            source_url='https://api.kraken.com/0/public/AssetPairs?pair=XBTEUR%2CETHEUR%2CXRPEUR&country_code=FR')
        self.assertEqual(parse_venue_constraints(record,asset='BTC/EUR',at=self.at).minimum_quantity,Decimal('.00006'))

    def test_record_owns_source_copy_and_has_bounded_encoding(self):
        record=self.record();self.pair['ordermin']='9'
        self.assertEqual(self.parse(record).minimum_quantity,Decimal('.002'))
        record['source_pair']['padding']='x'*8192;self.resign(record)
        with self.assertRaises(VenueConstraintsError):self.parse(record)

    def test_order_numbers_require_positive_finite_decimal(self):
        rules=self.parse()
        for value in [True,0.002,'0.002',Decimal('NaN'),Decimal('Infinity'),Decimal('-1'),Decimal('1e-100000')]:
            with self.subTest(value=value), self.assertRaises(VenueConstraintsError):rules.validate_order(value,Decimal('2000'))
            with self.subTest(price=value), self.assertRaises(VenueConstraintsError):rules.validate_order(Decimal('.002'),value)


if __name__ == '__main__':unittest.main()
