import os
from typing import Any

import httpx

from hermes_trading.adapters import require_schema

SCHEMA_VERSION = 1


async def fetch() -> dict[str, Any]:
    api_key = os.getenv("GLASSNODE_API_KEY", "")
    if api_key:
        url = "https://api.glassnode.com/v1/metrics/addresses/active_count"
        params = {"a": "BTC", "api_key": api_key}
    else:
        url = "https://api.blockchain.info/stats"
        params = {}

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "glassnode" if api_key else "blockchain.info",
        "active_addresses": data[-1]["v"] if isinstance(data, list) and data else None,
        "market_price_usd": data.get("market_price_usd") if isinstance(data, dict) else None,
        "raw": data,
    }
    return require_schema(payload, SCHEMA_VERSION)
