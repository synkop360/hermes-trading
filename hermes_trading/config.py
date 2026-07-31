from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from typing import Mapping


LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS"


@dataclass(frozen=True)
class ExecutionQualityLimits:
    """Per-symbol market-quality limits used before an entry is authorized."""

    max_spread_bps: Decimal
    modeled_slippage_bps: Decimal

    def __post_init__(self) -> None:
        _finite_decimal(self.max_spread_bps, "max_spread_bps")
        _finite_decimal(self.modeled_slippage_bps, "modeled_slippage_bps")
        if not self.max_spread_bps.is_finite() or self.max_spread_bps <= 0:
            raise ValueError("max_spread_bps must be a positive finite Decimal")
        if not self.modeled_slippage_bps.is_finite() or self.modeled_slippage_bps < 0:
            raise ValueError("modeled_slippage_bps must be a non-negative finite Decimal")


def _finite_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


_MAJOR_QUALITY_LIMITS = ExecutionQualityLimits(
    max_spread_bps=Decimal("50"),
    modeled_slippage_bps=Decimal("10"),
)
_SHIB_QUALITY_LIMITS = ExecutionQualityLimits(
    max_spread_bps=Decimal("30"),
    modeled_slippage_bps=Decimal("20"),
)


def resolve_execution_quality_limits(
    env: Mapping[str, str] | None = None,
    symbol: str = "BTC/USDT",
) -> ExecutionQualityLimits:
    """Resolve strict, auditable spread and slippage limits for a symbol."""
    values = os.environ if env is None else env
    normalized_symbol = symbol.upper()
    defaults = _SHIB_QUALITY_LIMITS if normalized_symbol == "SHIB/USDT" else _MAJOR_QUALITY_LIMITS
    asset = normalized_symbol.split("/", maxsplit=1)[0]
    candidate = ExecutionQualityLimits(
        max_spread_bps=_first_decimal_env(
            values,
            (f"HERMES_TRADING_{asset}_MAX_SPREAD_BPS", "HERMES_TRADING_MAX_SPREAD_BPS"),
            defaults.max_spread_bps,
        ),
        modeled_slippage_bps=_first_decimal_env(
            values,
            (f"HERMES_TRADING_{asset}_MODELED_SLIPPAGE_BPS", "HERMES_TRADING_MODELED_SLIPPAGE_BPS"),
            defaults.modeled_slippage_bps,
        ),
    )
    if normalized_symbol != "SHIB/USDT":
        return candidate
    major = ExecutionQualityLimits(
        max_spread_bps=_decimal_env(
            values, "HERMES_TRADING_MAX_SPREAD_BPS", _MAJOR_QUALITY_LIMITS.max_spread_bps
        ),
        modeled_slippage_bps=_decimal_env(
            values,
            "HERMES_TRADING_MODELED_SLIPPAGE_BPS",
            _MAJOR_QUALITY_LIMITS.modeled_slippage_bps,
        ),
    )
    return ExecutionQualityLimits(
        max_spread_bps=min(candidate.max_spread_bps, _SHIB_QUALITY_LIMITS.max_spread_bps, major.max_spread_bps),
        modeled_slippage_bps=max(candidate.modeled_slippage_bps, _SHIB_QUALITY_LIMITS.modeled_slippage_bps, major.modeled_slippage_bps),
    )


def _decimal_env(values: Mapping[str, str], name: str, default: Decimal) -> Decimal:
    raw = values.get(name)
    if raw is None:
        return default
    if isinstance(raw, float):
        raise ValueError(f"{name} must not be a binary float")
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite Decimal") from error
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _first_decimal_env(
    values: Mapping[str, str], names: tuple[str, ...], default: Decimal
) -> Decimal:
    for name in names:
        if name in values:
            return _decimal_env(values, name, default)
    return default


__all__ = [
    "ExecutionQualityLimits",
    "LIVE_CONFIRM_PHRASE",
    "resolve_execution_quality_limits",
]
