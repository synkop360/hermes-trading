from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.adapters.execution import ExecutionSafetyError, _default_exchange, resolve_execution_settings
from hermes_trading.exchange import ApiKeyInfo, KrakenGateway, UnsafeApiKeyPermissions, validate_api_key_permissions
from hermes_trading.persistence import StateRepository
from hermes_trading.risk import resolve_risk_limits


@dataclass(frozen=True)
class ReadinessContext:
    mode: str
    validate_only: bool
    allow_real_orders: bool
    api_permissions: frozenset[str] | None
    sqlite_integrity: str
    reconciliation_fresh: bool
    kill_switch: bool
    risk_configured: bool
    market_supported: bool
    market_minimum: Decimal | None
    max_order_quote: Decimal
    qualification_approved: bool = False


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    failures: tuple[str, ...]


def check_readiness(context: ReadinessContext) -> ReadinessReport:
    failures: list[str] = []
    if context.mode != "live":
        failures.append("invalid mode gates: readiness requires live validation-only mode")
    if not context.validate_only or context.allow_real_orders:
        failures.append(
            "real orders remain blocked: qualification and explicit promotion are not implemented"
        )
    if context.api_permissions is None:
        failures.append("API permissions are missing")
    else:
        permissions = frozenset(
            permission.strip().lower() for permission in context.api_permissions
        )
        try:
            validate_api_key_permissions(ApiKeyInfo(permissions))
        except (UnsafeApiKeyPermissions, ValueError) as error:
            failures.append(f"API permissions are unsafe: {error}")
        required = {"query-funds", "query-open-trades", "modify-trades"}
        missing = sorted(required - permissions)
        if missing:
            failures.append(f"API permissions are missing: {', '.join(missing)}")
    if context.sqlite_integrity != "ok":
        failures.append(f"SQLite integrity failed: {context.sqlite_integrity}")
    if not context.reconciliation_fresh:
        failures.append("reconciliation is missing or stale")
    if context.kill_switch:
        failures.append("kill switch is active")
    if not context.risk_configured:
        failures.append("risk configuration is incomplete")
    if not context.market_supported:
        failures.append("unsupported market")
    if context.market_minimum is None or context.market_minimum <= 0:
        failures.append("exchange minimum is unavailable")
    elif context.market_minimum > context.max_order_quote:
        failures.append("exchange minimum exceeds the maximum order quote")
    return ReadinessReport(not failures, tuple(failures))


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _decimal(value: str | None, default: str) -> Decimal:
    try:
        result = Decimal(value if value not in {None, ""} else default)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("readiness decimal setting is invalid") from error
    if not result.is_finite():
        raise ValueError("readiness decimal setting must be finite")
    return result


def _context_from_environment() -> ReadinessContext:
    settings = resolve_execution_settings()
    state_dir = Path(os.getenv("HERMES_TRADING_STATE_DIR", Path(__file__).resolve().parents[1] / "state"))
    database_path = state_dir / "trading.db"
    integrity = "database missing"
    fresh = False
    kill_switch = True
    if database_path.exists():
        repository = StateRepository.open_read_only(database_path)
        try:
            integrity = repository.integrity_check()
            risk_state = repository.risk_state()
            kill_switch = risk_state.kill_switch
            fresh = repository.has_fresh_reconciliation(
                since=datetime(1970, 1, 1, tzinfo=UTC),
                now=datetime.now(UTC),
                max_age_seconds=300,
            )
        finally:
            repository.close()
    permissions = _active_kraken_permissions(settings.mode)
    permission_text = os.getenv("HERMES_KRAKEN_API_PERMISSIONS")
    if permission_text and permissions is not None:
        declared = frozenset(item.strip().lower() for item in permission_text.split(",") if item.strip())
        if declared != permissions:
            raise ValueError("declared Kraken permissions do not match the active API key")
    risk_names = (
        "HERMES_TRADING_RISK_PER_TRADE_PCT",
        "HERMES_TRADING_MAX_SYMBOL_EXPOSURE_PCT",
        "HERMES_TRADING_MAX_TOTAL_EXPOSURE_PCT",
        "HERMES_TRADING_MAX_DAILY_LOSS_PCT",
        "HERMES_TRADING_MAX_DRAWDOWN_PCT",
        "HERMES_TRADING_MAX_ORDER_USDT",
    )


def _active_kraken_permissions(mode: str) -> frozenset[str] | None:
    if mode != "live":
        return None

    async def fetch() -> frozenset[str]:
        gateway = KrakenGateway(_default_exchange, validate_only=True)
        try:
            return (await gateway.fetch_api_key_info()).permissions
        finally:
            await gateway.close()

    return asyncio.run(fetch())
    risk_configured = all(os.getenv(name, "").strip() for name in risk_names)
    limits = resolve_risk_limits()
    return ReadinessContext(
        mode=settings.mode,
        validate_only=settings.validate_only,
        allow_real_orders=settings.allow_real_orders,
        api_permissions=permissions,
        sqlite_integrity=integrity,
        reconciliation_fresh=fresh,
        kill_switch=kill_switch,
        risk_configured=risk_configured,
        market_supported=_truthy(os.getenv("HERMES_KRAKEN_MARKET_SUPPORTED")),
        market_minimum=_decimal(os.getenv("HERMES_KRAKEN_MIN_COST_USDT"), "0")
        if os.getenv("HERMES_KRAKEN_MIN_COST_USDT")
        else None,
        max_order_quote=limits.max_order_quote,
        qualification_approved=False,
    )


def main() -> int:
    try:
        report = check_readiness(_context_from_environment())
    except (ExecutionSafetyError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if not report.ready:
        for failure in report.failures:
            print(f"NOT READY: {failure}", file=sys.stderr)
        return 2
    print("READY: Kraken validation-only safety gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
