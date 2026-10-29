"""Synthetic collector-to-entry checks: no external prices, orders, or model calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hermes_trading.paired_worker import PublicQuoteCollector
from hermes_trading.positions import PositionEngine
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.entry_cost_reserve import reserved_quantity
from hermes_trading.market_data import _normalized_price
from venue_fixtures import venue_record


class CollectorEntryHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.at = datetime(2026, 10, 29, 12, 0, 5, tzinfo=timezone.utc)
        self.close = self.at.replace(second=0)
        self.candles = [dict(timestamp=(self.close-timedelta(hours=31-i)).isoformat(),
            open='2000', high='2010', low='1990', close='2000', volume='1') for i in range(31)]
        self.payload = dict(last='2000', bid='2000', ask='2000', received_at=self.at.isoformat(),
            source='synthetic', candles=self.candles, closes=['2000']*31)
        self.strategy = dict(version='1', position_size_r='.5', stop_loss_pct='.6',
            entry=dict(indicator='rsi', threshold=100, fee='taker'),
            exit=dict(stop_atr='2.5', take_profit_atr='11', max_bars=24),
            simulation=dict(cost_stress_reserve=True, taker_fee_rate='.008', maker_fee_rate='.004',
                slippage_rate='.001', minimum_order_quantity='.002', minimum_order_quote='.45',
                quantity_decimals=8, max_order_quote='5', max_concurrent_positions=2))

    async def collect(self, payload=None):
        prices=AsyncMock();prices.__aenter__.return_value=prices
        prices.fetch_many.return_value={'ETH/EUR':self.payload if payload is None else payload}
        venues=AsyncMock();venues.__aenter__.return_value=venues
        venues.fetch_many.return_value={'ETH/EUR':venue_record('ETH/EUR',self.at)}
        with patch('hermes_trading.paired_worker.PriceClient',return_value=prices), \
             patch('hermes_trading.paired_worker.PublicVenueClient',return_value=venues), \
             patch('hermes_trading.paired_worker._now',return_value=self.at):
            return (await PublicQuoteCollector('1h').collect(['ETH/EUR']))['ETH/EUR']

    async def test_real_collector_output_can_open_a_minimum_compliant_paper_position(self):
        snapshot=await self.collect()
        with tempfile.TemporaryDirectory() as directory:
            import yaml
            root=Path(directory)
            (root/'strategy.yaml').write_text(yaml.safe_dump(self.strategy),encoding='utf-8')
            repo=StateRepository.open(root/'trading.db');repo.migrate()
            try:
                context=dict(status='ready',at=self.at.isoformat(),starting_balance='200',
                    equity='200',cash='200',operating_costs='0')
                engine=PositionEngine(repo,equity=D('200'),wallet_cash_capital=D('200'),
                    prospective_at=self.at,entry_cost_reserve=context,reserve_state_dir=root)
                result=engine.process_candle(snapshot,self.strategy)
                self.assertIsNotNone(result.position, result.decision.reason)
                self.assertEqual(len(result.orders),1)
                self.assertGreaterEqual(result.position.quantity,D('.002'))
                self.assertLessEqual(result.position.quantity*result.position.entry_price,D('5'))
            finally: repo.close()

    async def test_collector_history_keeps_actual_times_and_excludes_open_bar(self):
        payload=deepcopy(self.payload)
        payload['candles'].append(dict(self.candles[-1],timestamp=self.close.isoformat(),close='2001'))
        snapshot=await self.collect(payload)
        self.assertEqual(len(snapshot.price['candles']),31)
        self.assertEqual([row.get('timestamp') for row in snapshot.price['candles']],
                         [row['timestamp'] for row in self.candles])

    async def test_unknown_or_reordered_history_remains_ineligible(self):
        snapshot=await self.collect()
        from dataclasses import replace
        for mode in ('missing','reordered'):
            with self.subTest(mode=mode):
                rows=[dict(row) for row in snapshot.price['candles']]
                if mode=='missing':
                    for row in rows: row.pop('timestamp',None)
                else: rows[-2],rows[-3]=rows[-3],rows[-2]
                altered=replace(snapshot,price=dict(snapshot.price,candles=rows))
                self.assertEqual(reserved_quantity(self.strategy,altered,D('2002'),D('.0024'),
                    equity='200',cash='200',exposure='0',positions=0,at=self.at),0)

    async def test_normalization_handles_exchange_milliseconds_without_inventing_times(self):
        payload=deepcopy(self.payload)
        payload['candles'][0]['timestamp']=int(datetime.fromisoformat(self.candles[0]['timestamp']).timestamp()*1000)
        del payload['candles'][1]['timestamp']
        normalized=_normalized_price(payload)
        self.assertEqual(normalized['candles'][0].get('timestamp'),self.candles[0]['timestamp'])
        self.assertNotIn('timestamp',normalized['candles'][1])
