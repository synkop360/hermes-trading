from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import ccxt.async_support as ccxt

from hermes_trading.config import LIVE_CONFIRM_PHRASE
from hermes_trading.execution import ExecutionService
from hermes_trading.persistence.repository import StateRepository


class ExecutionSafetyError(RuntimeError):
    """Raised when live execution is not explicitly authorized."""


@dataclass(frozen=True)
class ExecutionSettings:
    mode: str
    executor: str
    validate_only: bool
    allow_real_orders: bool


def execution_service(repository: StateRepository) -> ExecutionService:
    """Expose the new intent-first service without changing legacy loop wiring."""
    return ExecutionService(repository)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def resolve_execution_settings(env: dict[str, str] | None = None) -> ExecutionSettings:
    values = env if env is not None else os.environ
    mode = values.get("HERMES_TRADING_MODE", "paper").strip().lower()
    if mode in {"paper", "training", "train", "demo"}:
        normalized = "training" if mode in {"training", "train", "demo"} else "paper"
        return ExecutionSettings(normalized, "simulated", validate_only=True, allow_real_orders=False)

    if mode != "live":
        raise ExecutionSafetyError(f"unsupported HERMES_TRADING_MODE={mode!r}")
    if not _truthy(values.get("HERMES_TRADING_I_ACCEPT_RISK")):
        raise ExecutionSafetyError("live mode blocked: HERMES_TRADING_I_ACCEPT_RISK must be true")
    if values.get("HERMES_TRADING_LIVE_CONFIRM", "").strip() != LIVE_CONFIRM_PHRASE:
        raise ExecutionSafetyError("live mode blocked: confirmation phrase mismatch")

    validate_only = _truthy(values.get("HERMES_TRADING_LIVE_VALIDATE_ONLY", "true"))
    allow_real = _truthy(values.get("HERMES_TRADING_ALLOW_REAL_ORDERS"))
    if not validate_only and not allow_real:
        raise ExecutionSafetyError("live mode blocked: real orders require HERMES_TRADING_ALLOW_REAL_ORDERS=true")
    return ExecutionSettings("live", "kraken", validate_only=validate_only, allow_real_orders=allow_real)


def _default_exchange():
    exchange_id = os.getenv("EXCHANGE_ID", "kraken")
    exchange_cls = getattr(ccxt, exchange_id)
    return exchange_cls(
        {
            "apiKey": os.getenv("EXCHANGE_API_KEY") or None,
            "secret": os.getenv("EXCHANGE_API_SECRET") or None,
            "enableRateLimit": True,
        }
    )


def _quote_budget() -> float:
    value = float(os.getenv("HERMES_TRADING_MAX_ORDER_USDT", "5"))
    if value <= 0:
        raise ExecutionSafetyError("HERMES_TRADING_MAX_ORDER_USDT must be greater than 0")
    return value


async def execute_signal(
    signal: dict[str, Any] | None,
    strategy: dict[str, Any],
    settings: ExecutionSettings,
    exchange_factory=None,
) -> dict[str, Any] | None:
    if not signal:
        return None
    if signal.get("action") != "enter" or signal.get("side") != "buy" or signal.get("order_type") != "market":
        raise ExecutionSafetyError("only market buy entry signals are supported for live execution")

    if settings.executor == "simulated":
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "simulated",
            "asset": signal["asset"],
            "side": signal["side"],
            "order_type": signal["order_type"],
            "reference_price": signal["reference_price"],
            "position_size_r": strategy.get("position_size_r"),
        }
    if settings.executor != "kraken":
        raise ExecutionSafetyError(f"unsupported executor={settings.executor!r}")
    return await _execute_kraken_signal(signal, strategy, settings, exchange_factory)


async def _execute_kraken_signal(
    signal: dict[str, Any],
    strategy: dict[str, Any],
    settings: ExecutionSettings,
    exchange_factory=None,
) -> dict[str, Any]:
    if not settings.validate_only and not settings.allow_real_orders:
        raise ExecutionSafetyError("real orders require HERMES_TRADING_ALLOW_REAL_ORDERS=true")

    exchange = exchange_factory() if exchange_factory else _default_exchange()
    try:
        markets = await exchange.load_markets()
        asset = str(signal["asset"])
        market = markets.get(asset)
        if market is None:
            raise ExecutionSafetyError(f"asset {asset} is not available on the configured exchange")

        reference_price = float(signal["reference_price"])
        if reference_price <= 0:
            raise ExecutionSafetyError("reference price must be greater than 0")

        quote_budget = _quote_budget()
        amount = quote_budget / reference_price
        amount_text = exchange.amount_to_precision(asset, amount)
        precise_amount = float(amount_text)
        limits = market.get("limits", {})
        min_amount = (limits.get("amount") or {}).get("min")
        min_cost = (limits.get("cost") or {}).get("min")
        if min_amount is not None and precise_amount < float(min_amount):
            raise ExecutionSafetyError(f"order amount {amount_text} is below exchange minimum {min_amount}")
        if min_cost is not None and quote_budget < float(min_cost):
            raise ExecutionSafetyError(f"order cost {quote_budget} is below exchange minimum {min_cost}")

        params = {"validate": True} if settings.validate_only else {}
        raw_order = await exchange.create_order(
            asset,
            str(signal["order_type"]),
            str(signal["side"]),
            precise_amount,
            None,
            params,
        )
        status = "validated" if settings.validate_only else "submitted"
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "asset": asset,
            "side": signal["side"],
            "order_type": signal["order_type"],
            "amount": amount_text,
            "quote_budget": quote_budget,
            "reference_price": reference_price,
            "position_size_r": strategy.get("position_size_r"),
            "validate_only": settings.validate_only,
            "exchange_order_id": raw_order.get("id"),
            "exchange_status": raw_order.get("status"),
        }
    finally:
        await exchange.close()
