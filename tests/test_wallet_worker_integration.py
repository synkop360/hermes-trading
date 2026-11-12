import asyncio
from datetime import datetime,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from hermes_trading.loop import CycleResult,run_loop,run_cycle
from hermes_trading.market_data import MarketSnapshot
from hermes_trading.run import ensure_state_files


class WorkerWalletTests(unittest.IsolatedAsyncioTestCase):
    async def test_quote_receipt_time_is_not_refreshed_by_slow_candle_fetch(self):
        from datetime import timedelta
        from hermes_trading.adapters.price import PriceClient
        from types import SimpleNamespace
        stamp=datetime(2026,10,9,tzinfo=timezone.utc); clock={'now':stamp}
        async def ticker(asset): return {'last':'10','bid':'9','ask':'11','timestamp':None}
        async def candles(*args,**kwargs):
            clock['now']=stamp+timedelta(hours=1)
            return [[1,'10','11','9','10','1']]
        exchange=SimpleNamespace(fetch_ticker=ticker,fetch_ohlcv=candles)
        with patch('hermes_trading.adapters.price.datetime') as timer:
            timer.now.side_effect=lambda zone: clock['now']
            client=PriceClient(exchange_factory=lambda *args,**kwargs:exchange)
            result=await client.fetch('BTC/EUR')
        self.assertEqual(result['received_at'],stamp.isoformat())

    async def test_configured_wallet_heartbeat_cannot_inherit_legacy_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp); ensure_state_files(state)
            (state/'wallet_account.json').write_text(json.dumps({'schema_version':1,'experiment_id':'test','currency':'EUR',
                'starting_balance':'50','activated_at':datetime.now(timezone.utc).isoformat()}))
            with patch.dict(os.environ,{'HERMES_TRADING_MODE':'training','HERMES_NOTIFY_ENABLED':'false'}), \
                    patch('hermes_trading.loop.run_cycle',new=AsyncMock(return_value=CycleResult(results=(),mode='training'))), \
                    patch('hermes_trading.loop._update_profitability_best_effort',new=AsyncMock(return_value=None)), \
                    patch('hermes_trading.loop.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError): await run_loop('BTC/EUR',state)
            heartbeat=json.loads((state/'heartbeat.json').read_text())
            self.assertEqual(heartbeat['economic_gate']['basis'],'observed_wallet_metrics')
            self.assertEqual(heartbeat['economic_gate']['status'],'blocked')
            self.assertIsNone(heartbeat['wallet_account']['net_equity'])
            self.assertTrue((state/'wallet_metrics.json').exists())

    async def test_cycle_retains_quotes_and_sizes_from_configured_wallet_equity(self):
        from hermes_trading.persistence.repository import StateRepository
        from hermes_trading.persistence.artifacts import ArtifactProjector
        with tempfile.TemporaryDirectory() as tmp:
            state_dir=Path(tmp); repo=StateRepository.open(state_dir/'trading.db'); repo.migrate()
            snapshots={'BTC/EUR':MarketSnapshot('BTC/EUR','1m','missing','no_trade','test')}
            market=AsyncMock(); market.collect.return_value=snapshots
            try:
                with patch('hermes_trading.wallet_observer.value_current_wallet',return_value={'net_equity':'48','starting_balance':'50','known_accrued_costs':'2','activated_at':'2026-10-09T00:00:00+00:00'}), \
                        patch('hermes_trading.loop.PositionEngine') as engine:
                    engine.return_value.recover_pending_projections.return_value=None
                    result=await run_cycle(['BTC/EUR'],repo,ArtifactProjector(state_dir),market,{},'training',
                                           equity=Decimal('100'),wallet_state_dir=state_dir)
                self.assertEqual(engine.return_value.equity,Decimal('48'))
                self.assertTrue(engine.return_value.entries_allowed)
                self.assertEqual(engine.return_value.wallet_cash_capital,Decimal('48'))
                self.assertEqual(engine.return_value.entry_not_before,datetime(2026,10,9,tzinfo=timezone.utc))
                self.assertEqual(result.market_snapshots,snapshots)
            finally: repo.close()


if __name__=='__main__': unittest.main()
