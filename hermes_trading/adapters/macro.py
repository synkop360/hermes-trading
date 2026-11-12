import asyncio
from typing import Any

import yfinance as yf

from hermes_trading.adapters import require_schema

SCHEMA_VERSION = 1


def _download_macro() -> dict[str, float | None]:
    data = yf.download(["SPY", "DX-Y.NYB"], period="5d", interval="1d", progress=False, auto_adjust=True)
    closes = data["Close"] if "Close" in data else data
    result: dict[str, float | None] = {}
    for symbol in ["SPY", "DX-Y.NYB"]:
        try:
            series = closes[symbol].dropna()
            result[symbol] = float(series.iloc[-1]) if len(series) else None
        except Exception:
            result[symbol] = None
    return result


async def fetch() -> dict[str, Any]:
    values = await asyncio.to_thread(_download_macro)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "yfinance",
        "spy_close": values.get("SPY"),
        "dxy_close": values.get("DX-Y.NYB"),
    }
    return require_schema(payload, SCHEMA_VERSION)
