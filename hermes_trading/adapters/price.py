from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
import os
from typing import Any

import ccxt.async_support as ccxt

from hermes_trading.adapters import require_schema

SCHEMA_VERSION = 1
DEFAULT_CANDLE_LIMIT = 256


class PriceClient:
    """One cycle-scoped CCXT exchange with bounded multi-asset fetching."""

    def __init__(
        self,
        *,
        exchange_id: str | None = None,
        exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.exchange_id = exchange_id or os.getenv("EXCHANGE_ID", "kraken")
        factory = exchange_factory or getattr(ccxt, self.exchange_id)
        self._exchange = factory(
            {
                "apiKey": os.getenv("EXCHANGE_API_KEY") or None,
                "secret": os.getenv("EXCHANGE_API_SECRET") or None,
                "enableRateLimit": True,
            }
        )
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> PriceClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(self._exchange.close())
            self._close_task = close_task
            close_task.add_done_callback(self._finish_close)
        try:
            await asyncio.shield(close_task)
        except BaseException:
            self._finish_close(close_task)
            raise
        else:
            self._finish_close(close_task)

    def _finish_close(self, close_task: asyncio.Task[None]) -> None:
        if self._close_task is not close_task or not close_task.done():
            return
        if not close_task.cancelled() and close_task.exception() is None:
            self._closed = True
        self._close_task = None

    async def fetch(self, asset: str, timeframe: str = "1h") -> dict[str, Any]:
        ticker_task = asyncio.create_task(self._exchange.fetch_ticker(asset))
        candles_task = asyncio.create_task(
            self._exchange.fetch_ohlcv(asset, timeframe=timeframe, limit=DEFAULT_CANDLE_LIMIT)
        )
        tasks = (ticker_task, candles_task)
        try:
            ticker, candles = await asyncio.gather(*tasks)
        finally:
            await _cancel_and_await(tasks)
        return _price_payload(
            asset,
            timeframe,
            self.exchange_id,
            ticker,
            candles,
        )

    async def fetch_many(
        self,
        assets: Sequence[str],
        timeframe: str = "1h",
        *,
        concurrency: int = 4,
    ) -> dict[str, dict[str, Any] | BaseException]:
        if concurrency < 1:
            raise ValueError("concurrency must be at least one")
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_fetch(asset: str) -> dict[str, Any]:
            async with semaphore:
                last_error: Exception | None = None
                for _ in range(2):
                    try:
                        return await self.fetch(asset, timeframe)
                    except Exception as error:
                        last_error = error
                raise last_error or RuntimeError("price fetch failed")

        tasks = {
            asset: asyncio.create_task(bounded_fetch(asset))
            for asset in assets
        }
        try:
            values = await asyncio.gather(*tasks.values(), return_exceptions=True)
            return dict(zip(tasks, values))
        finally:
            await _cancel_and_await(tuple(tasks.values()))


async def fetch(
    asset: str = "BTC/USDT", timeframe: str = "1h"
) -> dict[str, Any]:
    """Compatibility wrapper for callers that fetch one asset at a time."""
    async with PriceClient() as client:
        return await client.fetch(asset, timeframe)


def _price_payload(
    asset: str,
    timeframe: str,
    source: str,
    ticker: Any,
    candles: Any,
) -> dict[str, Any]:
    if not isinstance(ticker, dict):
        raise ValueError("ticker response is not a mapping")
    if not isinstance(candles, list):
        raise ValueError("OHLCV response is not a list")

    missing_ticker_fields = [
        field
        for field in ("last", "bid", "ask")
        if field not in ticker or ticker[field] is None
    ]
    if missing_ticker_fields:
        raise ValueError(
            "ticker response is missing fields: "
            + ", ".join(missing_ticker_fields)
        )
    last = _finite_decimal(ticker["last"])
    bid = _finite_decimal(ticker["bid"])
    ask = _finite_decimal(ticker["ask"])
    if bid > ask:
        raise ValueError("ticker bid exceeds ask")

    normalized_candles = [_normalize_candle(row) for row in candles]
    if not normalized_candles:
        raise ValueError("OHLCV response contains no candles")
    # Retained while the legacy RSI evaluator still consumes float closes.
    closes = [float(item["close"]) for item in normalized_candles]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "timeframe": timeframe,
        "source": source,
        "last": last,
        "bid": bid,
        "ask": ask,
        "closes": closes,
        "candles": normalized_candles,
        "timestamp": (
            _finite_decimal(ticker["timestamp"])
            if ticker.get("timestamp") is not None
            else None
        ),
    }
    return require_schema(payload, SCHEMA_VERSION)


def _normalize_candle(row: Any) -> dict[str, Decimal | None]:
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        raise ValueError("OHLCV row must contain at least five values")
    timestamp = _finite_decimal(row[0])
    open_price = _finite_decimal(row[1])
    high = _finite_decimal(row[2])
    low = _finite_decimal(row[3])
    close = _finite_decimal(row[4])
    volume = _finite_decimal(row[5]) if len(row) >= 6 and row[5] is not None else None
    if low > min(open_price, close) or high < max(open_price, close) or low > high:
        raise ValueError("invalid OHLC price relationship")
    if volume is not None and volume < 0:
        raise ValueError("candle volume cannot be negative")
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _finite_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("numeric value is missing or boolean")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("numeric value is invalid") from error
    if not decimal_value.is_finite():
        raise ValueError("numeric value must be finite")
    return decimal_value


async def _cancel_and_await(tasks: Sequence[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["DEFAULT_CANDLE_LIMIT", "PriceClient", "SCHEMA_VERSION", "fetch"]
