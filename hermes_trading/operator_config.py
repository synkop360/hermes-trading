from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from hermes_trading.market_data import timeframe_interval


class OperatorConfigError(ValueError):
    """Raised when an operator configuration change is not allowed."""


@dataclass(frozen=True)
class OperatorSetting:
    name: str
    kind: str
    restart_required: bool = True


_SETTINGS: tuple[OperatorSetting, ...] = (
    OperatorSetting("HERMES_TRADING_ASSETS", "assets"),
    OperatorSetting("HERMES_TRADING_TIMEFRAME", "timeframe"),
    OperatorSetting("HERMES_TRADING_MAX_ORDER_USDT", "decimal"),
    OperatorSetting("HERMES_TRADING_RISK_PER_TRADE_PCT", "decimal"),
    OperatorSetting("HERMES_TRADING_MAX_SYMBOL_EXPOSURE_PCT", "decimal"),
    OperatorSetting("HERMES_TRADING_MAX_TOTAL_EXPOSURE_PCT", "decimal"),
    OperatorSetting("HERMES_TRADING_MAX_DAILY_LOSS_PCT", "decimal"),
    OperatorSetting("HERMES_TRADING_MAX_DRAWDOWN_PCT", "decimal"),
    OperatorSetting("HERMES_TRADING_MAX_SPREAD_BPS", "decimal"),
    OperatorSetting("HERMES_TRADING_MODELED_SLIPPAGE_BPS", "decimal"),
    OperatorSetting("HERMES_NOTIFY_ENABLED", "bool"),
    OperatorSetting("HERMES_NOTIFY_SERVICE", "notify_service"),
    OperatorSetting("HERMES_NOTIFY_MODE", "notify_mode"),
    OperatorSetting("HERMES_NOTIFY_ON_TRADES", "bool"),
    OperatorSetting("HERMES_NOTIFY_ON_ORDERS", "bool"),
    OperatorSetting("HERMES_NOTIFY_ON_ERRORS", "bool"),
    OperatorSetting("HERMES_NOTIFY_ON_HEARTBEAT", "bool"),
    OperatorSetting("HERMES_NOTIFY_SUMMARY_EVERY_CYCLES", "nonnegative_int"),
    OperatorSetting("HERMES_NOTIFY_SUMMARY_FREQUENCIES", "text"),
    OperatorSetting("HERMES_NOTIFY_PROFITABILITY_FREQUENCIES", "text"),
    OperatorSetting("HERMES_FUNDS_ALERT_ENABLED", "bool"),
    OperatorSetting("HERMES_FUNDS_ALERT_QUOTE", "symbol"),
    OperatorSetting("HERMES_FUNDS_MIN_AVAILABLE_USDT", "decimal"),
    OperatorSetting("HERMES_FUNDS_MIN_ORDER_MULTIPLE", "decimal"),
    OperatorSetting("HERMES_FUNDS_BUFFER_PCT", "decimal"),
    OperatorSetting("HERMES_FUNDS_ALERT_COOLDOWN_SECONDS", "nonnegative_int"),
    OperatorSetting("HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE", "decimal"),
    OperatorSetting("HERMES_FUNDS_ALERT_MENTION", "text"),
    OperatorSetting("HERMES_QUALIFICATION_EVERY_CYCLES", "nonnegative_int"),
    OperatorSetting("HERMES_WORKER_FEATURE_UPDATES", "text"),
    OperatorSetting("HERMES_REFLECT_MIN_CLOSED_TRADES", "nonnegative_int"),
    OperatorSetting("HERMES_REFLECT_MIN_NEW_TRADES", "nonnegative_int"),
    OperatorSetting("HERMES_REFLECT_MIN_NEW_DECISIONS", "nonnegative_int"),
    OperatorSetting("HERMES_REFLECT_COOLDOWN_HOURS", "decimal"),
)

_BY_NAME = {setting.name: setting for setting in _SETTINGS}


def operator_config_path(state_dir: Path) -> Path:
    return state_dir / "operator_config.json"


def load_operator_config(state_dir: Path) -> dict[str, str]:
    path = operator_config_path(state_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as error:
        raise OperatorConfigError("operator_config.json is not valid JSON") from error
    if not isinstance(payload, dict):
        raise OperatorConfigError("operator_config.json must contain an object")
    return {str(key): str(value) for key, value in payload.items() if key in _BY_NAME}


def merged_operator_env(state_dir: Path, env: Mapping[str, str]) -> dict[str, str]:
    return {**dict(env), **load_operator_config(state_dir)}


def list_operator_config(state_dir: Path, env: Mapping[str, str]) -> dict[str, object]:
    persisted = load_operator_config(state_dir)
    settings: dict[str, dict[str, object]] = {}
    for setting in _SETTINGS:
        source = "operator" if setting.name in persisted else "environment" if setting.name in env else "default"
        settings[setting.name] = {
            "value": persisted.get(setting.name, env.get(setting.name, "")),
            "source": source,
            "restart_required": setting.restart_required,
        }
    return {"settings": settings}


def set_operator_config(state_dir: Path, key: str, value: str, env: Mapping[str, str]) -> dict[str, object]:
    normalized_key = key.strip().upper()
    if normalized_key not in _BY_NAME:
        raise OperatorConfigError(f"{normalized_key} is not an operator-configurable setting")
    setting = _BY_NAME[normalized_key]
    normalized_value = _validated_value(setting, value)
    path = operator_config_path(state_dir)
    current = load_operator_config(state_dir)
    current[normalized_key] = normalized_value
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "key": normalized_key,
        "value": normalized_value,
        "restart_required": setting.restart_required,
        "settings": list_operator_config(state_dir, env)["settings"],
    }


def _validated_value(setting: OperatorSetting, value: str) -> str:
    raw = str(value).strip()
    if setting.kind == "bool":
        lowered = raw.lower()
        if lowered not in {"true", "false"}:
            raise OperatorConfigError(f"{setting.name} must be true or false")
        return lowered
    if setting.kind == "nonnegative_int":
        try:
            parsed = int(raw)
        except ValueError as error:
            raise OperatorConfigError(f"{setting.name} must be an integer") from error
        if parsed < 0:
            raise OperatorConfigError(f"{setting.name} must be 0 or greater")
        return str(parsed)
    if setting.kind == "decimal":
        try:
            parsed = Decimal(raw)
        except (InvalidOperation, ValueError) as error:
            raise OperatorConfigError(f"{setting.name} must be numeric") from error
        if not parsed.is_finite() or parsed < 0:
            raise OperatorConfigError(f"{setting.name} must be a finite positive number")
        return format(parsed, "f")
    if setting.kind == "timeframe":
        timeframe_interval(raw)
        return raw
    if setting.kind == "assets":
        assets = [part.strip().upper() for part in raw.replace(";", ",").split(",") if part.strip()]
        if not assets:
            raise OperatorConfigError("HERMES_TRADING_ASSETS must contain at least one asset")
        return ",".join(assets)
    if setting.kind == "notify_mode":
        normalized = raw.lower().replace("-", "_")
        aliases = {"verbose", "orders_only", "pnl_only", "orders", "pnl", "all"}
        if normalized not in aliases:
            raise OperatorConfigError("HERMES_NOTIFY_MODE must be verbose, orders_only, or pnl_only")
        return {"orders": "orders_only", "pnl": "pnl_only", "all": "verbose"}.get(normalized, normalized)
    if setting.kind == "notify_service":
        if raw.lower() != "discord":
            raise OperatorConfigError("HERMES_NOTIFY_SERVICE must be discord")
        return "discord"
    if setting.kind == "symbol":
        if not raw:
            raise OperatorConfigError(f"{setting.name} must not be empty")
        return raw.upper()
    return raw


__all__ = [
    "OperatorConfigError",
    "list_operator_config",
    "load_operator_config",
    "merged_operator_env",
    "set_operator_config",
]
