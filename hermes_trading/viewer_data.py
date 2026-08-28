from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hermes_trading.loop import resolve_asset_strategy
from hermes_trading.strategy import _atr, _decimal_setting, _rsi

RSI_WARMUP = 14
ATR_WARMUP = 14


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class IndicatorSeries:
    rsi: tuple[Decimal | None, ...]
    atr: tuple[Decimal | None, ...]
    entry_indicator: str | None
    entry_threshold: Decimal | None
    exit_indicator: str | None
    exit_threshold: Decimal | None


@dataclass(frozen=True)
class OpenPosition:
    position_id: str
    entry_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None
    quantity: Decimal
    opened_at: datetime


def compute_indicator_series(candles: tuple[Candle, ...], asset_strategy: dict[str, Any]) -> IndicatorSeries:
    """Recompute RSI/ATR over ``candles`` using the exact functions the live strategy evaluates with.

    Aligned index-for-index with ``candles``; the first ``RSI_WARMUP``/``ATR_WARMUP``
    entries are ``None`` because those indicators need that many prior candles.
    """
    closes = [candle.close for candle in candles]
    ohlc_rows = [{"high": candle.high, "low": candle.low, "close": candle.close} for candle in candles]

    rsi_values: list[Decimal | None] = [None] * len(candles)
    atr_values: list[Decimal | None] = [None] * len(candles)
    for index in range(len(candles)):
        if index >= RSI_WARMUP:
            rsi_values[index] = _rsi(closes[index - RSI_WARMUP : index + 1])
        if index >= ATR_WARMUP:
            window = SimpleNamespace(price={"candles": ohlc_rows[index - ATR_WARMUP : index + 1]})
            atr_values[index] = _atr(window)

    entry = asset_strategy.get("entry")
    exit_rule = asset_strategy.get("exit")
    entry_indicator = str(entry["indicator"]) if isinstance(entry, dict) and entry.get("indicator") else None
    exit_indicator = str(exit_rule["indicator"]) if isinstance(exit_rule, dict) and exit_rule.get("indicator") else None
    return IndicatorSeries(
        rsi=tuple(rsi_values),
        atr=tuple(atr_values),
        entry_indicator=entry_indicator,
        entry_threshold=_optional_decimal(entry.get("threshold")) if isinstance(entry, dict) else None,
        exit_indicator=exit_indicator,
        exit_threshold=_optional_decimal(exit_rule.get("threshold")) if isinstance(exit_rule, dict) else None,
    )


def asset_strategy_config(strategy: dict[str, Any], asset: str) -> dict[str, Any]:
    """Resolve the same per-asset strategy view the live worker trades with."""
    return resolve_asset_strategy(strategy, asset)


def load_closed_trades(state_dir: Path, asset: str, since: datetime) -> list[dict[str, Any]]:
    """Closed trades for ``asset`` with ``closed_at >= since``, oldest first."""
    path = state_dir / "trades.jsonl"
    if not path.exists():
        return []
    trades: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("asset") != asset:
            continue
        closed_at = row.get("closed_at")
        if not closed_at:
            continue
        if datetime.fromisoformat(closed_at).astimezone(UTC) < since:
            continue
        trades.append(row)
    trades.sort(key=lambda row: row["closed_at"])
    return trades


def load_open_position(state_dir: Path, asset: str) -> OpenPosition | None:
    """The live worker's current open position for ``asset``, from ``positions.json``."""
    path = state_dir / "positions.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for row in payload.get("positions", []):
        if row.get("asset") != asset or row.get("status") not in {"opening", "open", "closing"}:
            continue
        take_profit_price = row.get("take_profit_price")
        return OpenPosition(
            position_id=str(row["position_id"]),
            entry_price=Decimal(str(row["entry_price"])),
            stop_price=Decimal(str(row["stop_price"])),
            take_profit_price=Decimal(str(take_profit_price)) if take_profit_price is not None else None,
            quantity=Decimal(str(row["quantity"])),
            opened_at=datetime.fromisoformat(row["opened_at"]).astimezone(UTC),
        )
    return None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal_setting(value, "threshold")


__all__ = [
    "ATR_WARMUP",
    "RSI_WARMUP",
    "Candle",
    "IndicatorSeries",
    "OpenPosition",
    "asset_strategy_config",
    "compute_indicator_series",
    "load_closed_trades",
    "load_open_position",
]
