import asyncio
import os
from typing import Any

import ccxt.async_support as ccxt

from hermes_trading.adapters import require_schema

SCHEMA_VERSION = 1


async def fetch(asset: str = "BTC/USDT") -> dict[str, Any]:
    exchange_id = os.getenv("EXCHANGE_ID", "kraken")
    exchange_cls = getattr(ccxt, exchange_id)
    exchange = exchange_cls(
        {
            "apiKey": os.getenv("EXCHANGE_API_KEY") or None,
            "secret": os.getenv("EXCHANGE_API_SECRET") or None,
            "enableRateLimit": True,
        }
    )
    try:
        ticker, candles = await asyncio.gather(
            exchange.fetch_ticker(asset),
            exchange.fetch_ohlcv(asset, timeframe="1h", limit=32),
        )
    finally:
        await exchange.close()

    closes = [float(row[4]) for row in candles if len(row) >= 5]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "source": exchange_id,
        "last": float(ticker["last"]),
        "bid": float(ticker["bid"] or ticker["last"]),
        "ask": float(ticker["ask"] or ticker["last"]),
        "closes": closes,
        "timestamp": ticker.get("timestamp"),
    }
    return require_schema(payload, SCHEMA_VERSION)
