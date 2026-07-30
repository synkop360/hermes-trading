from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any

from hermes_trading.adapters import macro, news, onchain, price
from hermes_trading.persistence import StateRepository


_TIMEFRAME_PATTERN = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[smhdwM])$")
_TIMEFRAME_UNITS = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    # CCXT treats one month as a fixed exchange interval; use 30 days consistently.
    "M": timedelta(days=30),
}
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandleIdentity:
    asset: str
    timeframe: str
    close_timestamp: datetime

    @property
    def candle_id(self) -> str:
        return f"{self.asset}:{self.timeframe}:{self.close_timestamp.isoformat()}"


@dataclass(frozen=True)
class ClosedCandle:
    open_timestamp: datetime
    close_timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None


@dataclass(frozen=True)
class MarketSnapshot:
    asset: str
    timeframe: str
    availability: str
    decision_action: str
    reason: str
    candle: ClosedCandle | None = None
    price: Mapping[str, Decimal] | None = None
    news: Mapping[str, Any] | None = None
    macro: Mapping[str, Any] | None = None
    onchain: Mapping[str, Any] | None = None
    shared_failures: Mapping[str, str] = field(default_factory=dict)

    @property
    def candle_identity(self) -> CandleIdentity | None:
        if self.candle is None:
            return None
        return CandleIdentity(self.asset, self.timeframe, self.candle.close_timestamp)


def timeframe_interval(timeframe: str) -> timedelta:
    match = _TIMEFRAME_PATTERN.fullmatch(timeframe)
    if match is None:
        raise ValueError(f"unsupported CCXT timeframe: {timeframe!r}")
    return _TIMEFRAME_UNITS[match["unit"]] * int(match["count"])


def latest_closed_candle(
    price_payload: Mapping[str, Any], timeframe: str, *, now: datetime | None = None
) -> ClosedCandle:
    interval = timeframe_interval(timeframe)
    current_time = _utc_datetime(now or datetime.now(UTC))
    candles = price_payload.get("candles")
    if not isinstance(candles, list):
        raise ValueError("price payload does not contain candles")

    eligible: list[ClosedCandle] = []
    for raw_candle in candles:
        try:
            candle = _closed_candle(raw_candle, interval)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if candle.close_timestamp <= current_time:
            eligible.append(candle)
    if not eligible:
        raise ValueError("price payload has no closed candle")
    return max(eligible, key=lambda candle: candle.close_timestamp)


class MarketDataCoordinator:
    def __init__(
        self,
        *,
        repository: StateRepository,
        price_fetch: Callable[[str], Awaitable[dict[str, Any]]] = price.fetch,
        news_fetch: Callable[[], Awaitable[dict[str, Any]]] = news.fetch,
        macro_fetch: Callable[[], Awaitable[dict[str, Any]]] = macro.fetch,
        onchain_fetch: Callable[[], Awaitable[dict[str, Any]]] = onchain.fetch,
        timeframe: str = "1h",
        concurrency: int = 4,
        stale_after_intervals: int = 2,
        failure_threshold: int = 3,
        now: Callable[[], datetime] | None = None,
        warning: Callable[[str], None] | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least one")
        if stale_after_intervals < 1:
            raise ValueError("stale_after_intervals must be at least one")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least one")
        timeframe_interval(timeframe)
        self.repository = repository
        self.price_fetch = price_fetch
        self.news_fetch = news_fetch
        self.macro_fetch = macro_fetch
        self.onchain_fetch = onchain_fetch
        self.timeframe = timeframe
        self.concurrency = concurrency
        self.stale_after_intervals = stale_after_intervals
        self.failure_threshold = failure_threshold
        self._now = now or (lambda: datetime.now(UTC))
        self._warning = warning or _LOGGER.warning

    async def collect(self, assets: Sequence[str]) -> dict[str, MarketSnapshot]:
        current_time = _utc_datetime(self._now())
        shared_tasks = {
            "news": asyncio.create_task(self.news_fetch()),
            "macro": asyncio.create_task(self.macro_fetch()),
            "onchain": asyncio.create_task(self.onchain_fetch()),
        }
        semaphore = asyncio.Semaphore(self.concurrency)
        price_tasks = [
            asyncio.create_task(self._fetch_price(asset, semaphore))
            for asset in assets
        ]
        shared_values = await asyncio.gather(*shared_tasks.values(), return_exceptions=True)
        price_values = await asyncio.gather(*price_tasks, return_exceptions=True)

        shared_data: dict[str, Mapping[str, Any]] = {}
        shared_failures: dict[str, str] = {}
        for source, result in zip(shared_tasks, shared_values):
            if isinstance(result, BaseException):
                shared_failures[source] = _failure_reason(result)
                self._record_failure(source, shared_failures[source])
            elif isinstance(result, Mapping):
                shared_data[source] = result
                self.repository.resolve_market_data_incident(source)
            else:
                shared_failures[source] = "invalid_response"
                self._record_failure(source, "invalid_response")

        return {
            asset: self._snapshot_for_asset(
                asset,
                result,
                current_time,
                shared_data,
                shared_failures,
            )
            for asset, result in zip(assets, price_values)
        }

    async def _fetch_price(
        self, asset: str, semaphore: asyncio.Semaphore
    ) -> dict[str, Any]:
        async with semaphore:
            return await self.price_fetch(asset)

    def _snapshot_for_asset(
        self,
        asset: str,
        result: object,
        current_time: datetime,
        shared_data: Mapping[str, Mapping[str, Any]],
        shared_failures: Mapping[str, str],
    ) -> MarketSnapshot:
        price_source = f"price:{asset}"
        if isinstance(result, BaseException):
            reason = _failure_reason(result)
            self._record_failure(price_source, reason)
            return self._no_trade(asset, "invalid", "price_fetch_failed", shared_data, shared_failures)
        if not isinstance(result, Mapping):
            self._record_failure(price_source, "invalid_response")
            return self._no_trade(asset, "invalid", "invalid_price_response", shared_data, shared_failures)

        try:
            candle = latest_closed_candle(result, self.timeframe, now=current_time)
            normalized_price = _normalized_price(result)
        except (TypeError, ValueError, InvalidOperation) as error:
            self._record_failure(price_source, "invalid_candle")
            return self._no_trade(asset, "invalid", f"invalid_candle:{error}", shared_data, shared_failures)

        interval = timeframe_interval(self.timeframe)
        if current_time - candle.close_timestamp > interval * self.stale_after_intervals:
            self._record_failure(price_source, "stale_candle")
            return self._no_trade(
                asset,
                "stale",
                "stale_closed_candle",
                shared_data,
                shared_failures,
                candle=candle,
                normalized_price=normalized_price,
            )

        self.repository.resolve_market_data_incident(price_source)
        if shared_failures:
            return self._no_trade(
                asset,
                "shared_data_unavailable",
                "shared_source_failed",
                shared_data,
                shared_failures,
                candle=candle,
                normalized_price=normalized_price,
            )

        identity = CandleIdentity(asset, self.timeframe, candle.close_timestamp)
        if self.repository.is_candle_processed(
            identity.asset, identity.timeframe, identity.close_timestamp.isoformat()
        ):
            return self._no_trade(
                asset,
                "already_processed",
                "candle_already_processed",
                shared_data,
                shared_failures,
                candle=candle,
                normalized_price=normalized_price,
            )

        return MarketSnapshot(
            asset=asset,
            timeframe=self.timeframe,
            availability="available",
            decision_action="evaluate",
            reason="closed_candle_available",
            candle=candle,
            price=normalized_price,
            news=shared_data.get("news"),
            macro=shared_data.get("macro"),
            onchain=shared_data.get("onchain"),
            shared_failures=dict(shared_failures),
        )

    def _no_trade(
        self,
        asset: str,
        availability: str,
        reason: str,
        shared_data: Mapping[str, Mapping[str, Any]],
        shared_failures: Mapping[str, str],
        *,
        candle: ClosedCandle | None = None,
        normalized_price: Mapping[str, Decimal] | None = None,
    ) -> MarketSnapshot:
        return MarketSnapshot(
            asset=asset,
            timeframe=self.timeframe,
            availability=availability,
            decision_action="no_trade",
            reason=reason,
            candle=candle,
            price=normalized_price,
            news=shared_data.get("news"),
            macro=shared_data.get("macro"),
            onchain=shared_data.get("onchain"),
            shared_failures=dict(shared_failures),
        )

    def _record_failure(self, source: str, reason: str) -> None:
        failures = self.repository.record_market_data_failure(source, reason)
        if failures == self.failure_threshold:
            self._warning(
                f"market data source {source} failed {failures} consecutive times: {reason}"
            )


def _closed_candle(raw_candle: object, interval: timedelta) -> ClosedCandle:
    if not isinstance(raw_candle, Mapping):
        raise ValueError("candle is not a mapping")
    open_timestamp = _timestamp(raw_candle["timestamp"])
    return ClosedCandle(
        open_timestamp=open_timestamp,
        close_timestamp=open_timestamp + interval,
        open=_decimal(raw_candle["open"]),
        high=_decimal(raw_candle["high"]),
        low=_decimal(raw_candle["low"]),
        close=_decimal(raw_candle["close"]),
        volume=_decimal(raw_candle["volume"]) if raw_candle.get("volume") is not None else None,
    )


def _normalized_price(payload: Mapping[str, Any]) -> dict[str, Decimal]:
    return {
        field: _decimal(payload[field])
        for field in ("last", "bid", "ask")
        if payload.get(field) is not None
    }


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("numeric value is missing or boolean")
    return Decimal(str(value))


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        return _utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    if isinstance(value, bool) or value is None:
        raise ValueError("timestamp is missing or boolean")
    milliseconds = Decimal(str(value))
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + timedelta(microseconds=int(milliseconds * 1000))


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _failure_reason(error: BaseException) -> str:
    return str(error) or type(error).__name__


__all__ = [
    "CandleIdentity",
    "ClosedCandle",
    "MarketDataCoordinator",
    "MarketSnapshot",
    "latest_closed_candle",
    "timeframe_interval",
]
