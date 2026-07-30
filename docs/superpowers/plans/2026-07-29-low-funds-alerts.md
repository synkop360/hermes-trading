# Low Funds Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable low-funds Discord alerts with aligned `WARNING`, `CRITICAL`, and `RESOLVED` severity levels.

**Architecture:** Create a focused `hermes_trading/funds.py` module for settings, balance extraction, severity calculation, anti-spam state, and message formatting. Reuse the existing Discord notification path by returning alert messages from the worker loop; alerts bypass `HERMES_NOTIFY_MODE` filtering because they are safety notifications. Persist alert state in `state/funds_alert.json`.

**Tech Stack:** Python 3.11, `ccxt.async_support`, existing Discord webhook notifications, `unittest`, Railway environment variables.

## Global Constraints

- Do not place orders or change trading behavior from the funds-alert feature.
- Alerts are disabled by default with `HERMES_FUNDS_ALERT_ENABLED=false`.
- Use quote currency `USDT` by default.
- Severity levels must be mechanical:
  - `OK`: `available > low_limit`.
  - `WARNING`: `critical_limit < available <= low_limit`.
  - `CRITICAL`: `available <= critical_limit`.
  - `RESOLVED`: previous state was `WARNING` or `CRITICAL`, and `available >= low_limit * resolve_multiple`.
- Low limit is `max(HERMES_FUNDS_MIN_AVAILABLE_USDT, HERMES_TRADING_MAX_ORDER_USDT * HERMES_FUNDS_MIN_ORDER_MULTIPLE * (1 + HERMES_FUNDS_BUFFER_PCT))`.
- Critical limit is `max(HERMES_TRADING_MAX_ORDER_USDT, exchange_min_cost)`; when `exchange_min_cost` is unknown, use `HERMES_TRADING_MAX_ORDER_USDT`.
- Default values:
  - `HERMES_FUNDS_MIN_AVAILABLE_USDT=20`
  - `HERMES_FUNDS_MIN_ORDER_MULTIPLE=3`
  - `HERMES_FUNDS_BUFFER_PCT=0.10`
  - `HERMES_FUNDS_ALERT_COOLDOWN_SECONDS=21600`
  - `HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE=1.20`
  - `HERMES_FUNDS_ALERT_MENTION=` empty by default
- Alert messages must be short, multi-line, and immediately recognizable with `[HERMES WARNING]`, `[HERMES CRITICAL]`, or `[HERMES RESOLVED]`.
- Never print or persist API secrets.

---

## File Structure

- Create `hermes_trading/funds.py`: funds alert settings, balance extraction, severity logic, persistent alert state, Kraken/CCXT balance fetching, and alert message formatting.
- Create `tests/test_funds.py`: focused unit tests for configuration parsing, severity boundaries, state/cooldown behavior, balance extraction, and message formatting.
- Modify `hermes_trading/loop.py`: initialize funds settings and call funds alert evaluation once per healthy worker cycle.
- Modify `hermes_trading/run.py`: seed `funds_alert.json`.
- Modify `docs/NOTIFICATIONS.md`: document low-funds alert variables, severity rules, examples, and cooldown.
- Modify `docs/SAFETY.md`: add low-funds alert as a live-readiness guard.
- Modify `docs/STATE_FILES.md`: document `funds_alert.json`.
- Modify `docs/OPERATIONS.md` and `docs/COMMANDS.md`: add activation and verification commands.
- Modify `scripts/build_docs_graph.py`: add a graph concept for funds alerts and link it to Discord notifications, Kraken account data, and safety.

---

### Task 1: Funds Alert Settings and Thresholds

**Files:**
- Create: `hermes_trading/funds.py`
- Test: `tests/test_funds.py`

**Interfaces:**
- Produces: `FundsAlertSettings`
- Produces: `resolve_funds_alert_settings(env: dict[str, str] | None = None) -> FundsAlertSettings`
- Produces: `calculate_low_limit(settings: FundsAlertSettings, order_budget: float | None = None) -> float`
- Produces: `calculate_critical_limit(settings: FundsAlertSettings, exchange_min_cost: float | None = None, order_budget: float | None = None) -> float`

- [ ] **Step 1: Write the failing settings tests**

Add to `tests/test_funds.py`:

```python
import unittest

from hermes_trading.funds import (
    FundsAlertConfigError,
    calculate_critical_limit,
    calculate_low_limit,
    resolve_funds_alert_settings,
)


class FundsAlertSettingsTests(unittest.TestCase):
    def test_funds_alerts_are_disabled_by_default(self):
        settings = resolve_funds_alert_settings({})

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.quote, "USDT")
        self.assertEqual(settings.min_available, 20.0)
        self.assertEqual(settings.min_order_multiple, 3.0)
        self.assertEqual(settings.buffer_pct, 0.10)
        self.assertEqual(settings.cooldown_seconds, 21600)
        self.assertEqual(settings.resolve_multiple, 1.20)
        self.assertEqual(settings.mention, "")

    def test_low_limit_uses_absolute_or_dynamic_limit_whichever_is_higher(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_ALERT_ENABLED": "true",
                "HERMES_FUNDS_MIN_AVAILABLE_USDT": "20",
                "HERMES_FUNDS_MIN_ORDER_MULTIPLE": "3",
                "HERMES_FUNDS_BUFFER_PCT": "0.10",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )

        self.assertEqual(calculate_low_limit(settings), 20.0)

    def test_dynamic_low_limit_can_exceed_absolute_floor(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_MIN_AVAILABLE_USDT": "10",
                "HERMES_FUNDS_MIN_ORDER_MULTIPLE": "5",
                "HERMES_FUNDS_BUFFER_PCT": "0.20",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )

        self.assertEqual(calculate_low_limit(settings), 30.0)

    def test_critical_limit_uses_order_budget_or_exchange_min_cost(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        self.assertEqual(calculate_critical_limit(settings, exchange_min_cost=1.0), 5.0)
        self.assertEqual(calculate_critical_limit(settings, exchange_min_cost=12.0), 12.0)

    def test_invalid_numeric_settings_raise_clear_error(self):
        with self.assertRaises(FundsAlertConfigError):
            resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_COOLDOWN_SECONDS": "-1"})
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsAlertSettingsTests -v
```

Expected: FAIL because `hermes_trading.funds` does not exist.

- [ ] **Step 3: Implement minimal settings code**

Create `hermes_trading/funds.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


class FundsAlertConfigError(RuntimeError):
    """Raised when low-funds alert settings are invalid."""


@dataclass(frozen=True)
class FundsAlertSettings:
    enabled: bool
    quote: str
    min_available: float
    min_order_multiple: float
    buffer_pct: float
    cooldown_seconds: int
    resolve_multiple: float
    mention: str


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _float_at_least(name: str, value: str | None, default: str, minimum: float) -> float:
    try:
        parsed = float(value if value not in {None, ""} else default)
    except ValueError as exc:
        raise FundsAlertConfigError(f"{name} must be numeric") from exc
    if parsed < minimum:
        raise FundsAlertConfigError(f"{name} must be at least {minimum}")
    return parsed


def _int_at_least(name: str, value: str | None, default: str, minimum: int) -> int:
    try:
        parsed = int(value if value not in {None, ""} else default)
    except ValueError as exc:
        raise FundsAlertConfigError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise FundsAlertConfigError(f"{name} must be at least {minimum}")
    return parsed


def resolve_funds_alert_settings(env: dict[str, str] | None = None) -> FundsAlertSettings:
    values = env if env is not None else os.environ
    quote = (values.get("HERMES_FUNDS_ALERT_QUOTE", "USDT").strip().upper() or "USDT")
    return FundsAlertSettings(
        enabled=_truthy(values.get("HERMES_FUNDS_ALERT_ENABLED")),
        quote=quote,
        min_available=_float_at_least(
            f"HERMES_FUNDS_MIN_AVAILABLE_{quote}",
            values.get(f"HERMES_FUNDS_MIN_AVAILABLE_{quote}"),
            "20",
            0.0,
        ),
        min_order_multiple=_float_at_least(
            "HERMES_FUNDS_MIN_ORDER_MULTIPLE",
            values.get("HERMES_FUNDS_MIN_ORDER_MULTIPLE"),
            "3",
            0.0,
        ),
        buffer_pct=_float_at_least(
            "HERMES_FUNDS_BUFFER_PCT",
            values.get("HERMES_FUNDS_BUFFER_PCT"),
            "0.10",
            0.0,
        ),
        cooldown_seconds=_int_at_least(
            "HERMES_FUNDS_ALERT_COOLDOWN_SECONDS",
            values.get("HERMES_FUNDS_ALERT_COOLDOWN_SECONDS"),
            "21600",
            0,
        ),
        resolve_multiple=_float_at_least(
            "HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE",
            values.get("HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE"),
            "1.20",
            1.0,
        ),
        mention=values.get("HERMES_FUNDS_ALERT_MENTION", "").strip(),
    )


def _order_budget(order_budget: float | None = None) -> float:
    if order_budget is not None:
        return float(order_budget)
    return _float_at_least("HERMES_TRADING_MAX_ORDER_USDT", os.getenv("HERMES_TRADING_MAX_ORDER_USDT"), "5", 0.01)


def calculate_low_limit(settings: FundsAlertSettings, order_budget: float | None = None) -> float:
    budget = _order_budget(order_budget)
    dynamic_limit = budget * settings.min_order_multiple * (1.0 + settings.buffer_pct)
    return max(settings.min_available, dynamic_limit)


def calculate_critical_limit(
    settings: FundsAlertSettings,
    exchange_min_cost: float | None = None,
    order_budget: float | None = None,
) -> float:
    budget = _order_budget(order_budget)
    if exchange_min_cost is None:
        return budget
    return max(budget, float(exchange_min_cost))
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsAlertSettingsTests -v
```

Expected: PASS.

- [ ] **Step 5: Run all tests**

Run:

```powershell
uv run python -m unittest discover -s tests
```

Expected: PASS.

---

### Task 2: Severity Calculation and Alert Message Formatting

**Files:**
- Modify: `hermes_trading/funds.py`
- Modify: `tests/test_funds.py`

**Interfaces:**
- Consumes: `FundsAlertSettings`, `calculate_low_limit`, `calculate_critical_limit`
- Produces: `FundsSeverity = Literal["ok", "warning", "critical"]`
- Produces: `class FundsSnapshot`
- Produces: `build_funds_snapshot(settings: FundsAlertSettings, available: float, exchange_min_cost: float | None = None, order_budget: float | None = None) -> FundsSnapshot`
- Produces: `format_funds_alert(snapshot: FundsSnapshot, alert_type: str, mention: str = "") -> str`

- [ ] **Step 1: Write the failing severity and format tests**

Append to `tests/test_funds.py`:

```python
from hermes_trading.funds import build_funds_snapshot, format_funds_alert


class FundsAlertSeverityTests(unittest.TestCase):
    def test_warning_means_below_low_limit_but_above_critical_limit(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        self.assertEqual(snapshot.severity, "warning")
        self.assertEqual(snapshot.low_limit, 20.0)
        self.assertEqual(snapshot.critical_limit, 5.0)

    def test_critical_means_available_cannot_cover_configured_order_budget(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=3.91, exchange_min_cost=1.0)

        self.assertEqual(snapshot.severity, "critical")

    def test_ok_means_available_is_above_low_limit(self):
        settings = resolve_funds_alert_settings({"HERMES_TRADING_MAX_ORDER_USDT": "5"})

        snapshot = build_funds_snapshot(settings, available=26.40, exchange_min_cost=1.0)

        self.assertEqual(snapshot.severity, "ok")

    def test_warning_message_is_short_and_visible(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_QUOTE": "USDT"})
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, order_budget=5.0)

        message = format_funds_alert(snapshot, "warning", mention="@here")

        self.assertEqual(
            message,
            "\n".join(
                [
                    "@here",
                    "[HERMES WARNING] LOW FUNDS",
                    "",
                    "Quote: USDT",
                    "Available: 14.82",
                    "Low limit: 20.00",
                    "Critical limit: 5.00",
                    "",
                    "Action: add funds or reduce order size",
                ]
            ),
        )

    def test_critical_message_has_critical_heading_and_action(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_QUOTE": "USDT"})
        snapshot = build_funds_snapshot(settings, available=3.91, exchange_min_cost=1.0, order_budget=5.0)

        message = format_funds_alert(snapshot, "critical", mention="@here")

        self.assertIn("[HERMES CRITICAL] FUNDS TOO LOW", message)
        self.assertIn("Action: pause live trading or add funds now", message)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsAlertSeverityTests -v
```

Expected: FAIL because `build_funds_snapshot` and `format_funds_alert` do not exist.

- [ ] **Step 3: Implement severity and formatting**

Append to `hermes_trading/funds.py`:

```python
from typing import Literal


FundsSeverity = Literal["ok", "warning", "critical"]


@dataclass(frozen=True)
class FundsSnapshot:
    quote: str
    available: float
    low_limit: float
    critical_limit: float
    resolve_limit: float
    order_budget: float
    severity: FundsSeverity


def build_funds_snapshot(
    settings: FundsAlertSettings,
    available: float,
    exchange_min_cost: float | None = None,
    order_budget: float | None = None,
) -> FundsSnapshot:
    budget = _order_budget(order_budget)
    low_limit = calculate_low_limit(settings, budget)
    critical_limit = calculate_critical_limit(settings, exchange_min_cost, budget)
    available_value = float(available)
    if available_value <= critical_limit:
        severity: FundsSeverity = "critical"
    elif available_value <= low_limit:
        severity = "warning"
    else:
        severity = "ok"
    return FundsSnapshot(
        quote=settings.quote,
        available=available_value,
        low_limit=low_limit,
        critical_limit=critical_limit,
        resolve_limit=low_limit * settings.resolve_multiple,
        order_budget=budget,
        severity=severity,
    )


def _money(value: float) -> str:
    return f"{value:.2f}"


def format_funds_alert(snapshot: FundsSnapshot, alert_type: str, mention: str = "") -> str:
    prefix = [mention] if mention else []
    if alert_type == "critical":
        heading = "[HERMES CRITICAL] FUNDS TOO LOW"
        action = "Action: pause live trading or add funds now"
    elif alert_type == "warning":
        heading = "[HERMES WARNING] LOW FUNDS"
        action = "Action: add funds or reduce order size"
    elif alert_type == "resolved":
        return "\n".join(
            [
                *prefix,
                "[HERMES RESOLVED] FUNDS BACK ABOVE LIMIT",
                "",
                f"Quote: {snapshot.quote}",
                f"Available: {_money(snapshot.available)}",
                f"Resolve limit: {_money(snapshot.resolve_limit)}",
            ]
        )
    else:
        raise FundsAlertConfigError(f"unsupported funds alert type={alert_type!r}")
    return "\n".join(
        [
            *prefix,
            heading,
            "",
            f"Quote: {snapshot.quote}",
            f"Available: {_money(snapshot.available)}",
            f"Low limit: {_money(snapshot.low_limit)}",
            f"Critical limit: {_money(snapshot.critical_limit)}",
            "",
            action,
        ]
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsAlertSeverityTests -v
```

Expected: PASS.

- [ ] **Step 5: Run all tests**

Run:

```powershell
uv run python -m unittest discover -s tests
```

Expected: PASS.

---

### Task 3: Persistent State, Cooldown, and Resolution

**Files:**
- Modify: `hermes_trading/funds.py`
- Modify: `tests/test_funds.py`

**Interfaces:**
- Consumes: `FundsSnapshot`, `FundsAlertSettings`, `format_funds_alert`
- Produces: `build_funds_alert_message(state_path: Path, settings: FundsAlertSettings, snapshot: FundsSnapshot, now: datetime | None = None) -> str | None`
- Produces state file shape:

```json
{
  "status": "warning",
  "last_alert_at": "2026-07-29T09:00:00+00:00",
  "last_available": 14.82,
  "low_limit": 20.0,
  "critical_limit": 5.0
}
```

- [ ] **Step 1: Write the failing state tests**

Append to `tests/test_funds.py`:

```python
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hermes_trading.funds import build_funds_alert_message


class FundsAlertStateTests(unittest.TestCase):
    def test_first_warning_alert_is_sent_and_state_is_persisted(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_ALERT_ENABLED": "true",
                "HERMES_FUNDS_ALERT_MENTION": "@here",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            message = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIn("[HERMES WARNING] LOW FUNDS", message)
        self.assertEqual(saved["status"], "warning")
        self.assertEqual(saved["last_available"], 14.82)

    def test_warning_repeats_only_after_cooldown(self):
        settings = resolve_funds_alert_settings(
            {
                "HERMES_FUNDS_ALERT_ENABLED": "true",
                "HERMES_FUNDS_ALERT_COOLDOWN_SECONDS": "21600",
                "HERMES_TRADING_MAX_ORDER_USDT": "5",
            }
        )
        snapshot = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            first = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            second = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T10:00:00+00:00"),
            )
            third = build_funds_alert_message(
                state_path,
                settings,
                snapshot,
                datetime.fromisoformat("2026-07-29T15:00:01+00:00"),
            )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)

    def test_critical_upgrade_bypasses_warning_cooldown(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_ENABLED": "true"})
        warning = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, order_budget=5.0)
        critical = build_funds_snapshot(settings, available=3.91, exchange_min_cost=1.0, order_budget=5.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            build_funds_alert_message(
                state_path,
                settings,
                warning,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            message = build_funds_alert_message(
                state_path,
                settings,
                critical,
                datetime.fromisoformat("2026-07-29T09:05:00+00:00"),
            )

        self.assertIn("[HERMES CRITICAL] FUNDS TOO LOW", message)

    def test_resolved_message_is_sent_only_above_resolve_limit(self):
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_ENABLED": "true"})
        warning = build_funds_snapshot(settings, available=14.82, exchange_min_cost=1.0, order_budget=5.0)
        still_low = build_funds_snapshot(settings, available=22.0, exchange_min_cost=1.0, order_budget=5.0)
        resolved = build_funds_snapshot(settings, available=26.4, exchange_min_cost=1.0, order_budget=5.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "funds_alert.json"
            build_funds_alert_message(
                state_path,
                settings,
                warning,
                datetime.fromisoformat("2026-07-29T09:00:00+00:00"),
            )
            no_message = build_funds_alert_message(
                state_path,
                settings,
                still_low,
                datetime.fromisoformat("2026-07-29T10:00:00+00:00"),
            )
            message = build_funds_alert_message(
                state_path,
                settings,
                resolved,
                datetime.fromisoformat("2026-07-29T11:00:00+00:00"),
            )

        self.assertIsNone(no_message)
        self.assertIn("[HERMES RESOLVED] FUNDS BACK ABOVE LIMIT", message)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsAlertStateTests -v
```

Expected: FAIL because `build_funds_alert_message` does not exist.

- [ ] **Step 3: Implement state and cooldown logic**

Append to `hermes_trading/funds.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SEVERITY_RANK = {"ok": 0, "warning": 1, "critical": 2}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _load_alert_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _write_alert_state(path: Path, snapshot: FundsSnapshot, status: str, now: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "status": status,
                "last_alert_at": now.isoformat(),
                "last_available": snapshot.available,
                "low_limit": snapshot.low_limit,
                "critical_limit": snapshot.critical_limit,
                "resolve_limit": snapshot.resolve_limit,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _cooldown_elapsed(state: dict[str, Any], now: datetime, cooldown_seconds: int) -> bool:
    raw = state.get("last_alert_at")
    if not raw:
        return True
    try:
        last_alert_at = _ensure_utc(datetime.fromisoformat(str(raw)))
    except ValueError:
        return True
    return (now - last_alert_at).total_seconds() >= cooldown_seconds


def build_funds_alert_message(
    state_path: Path,
    settings: FundsAlertSettings,
    snapshot: FundsSnapshot,
    now: datetime | None = None,
) -> str | None:
    if not settings.enabled:
        return None
    current_time = _ensure_utc(now or _utc_now())
    state = _load_alert_state(state_path)
    previous_status = str(state.get("status") or "ok")

    if snapshot.severity == "ok":
        if previous_status in {"warning", "critical"} and snapshot.available >= snapshot.resolve_limit:
            _write_alert_state(state_path, snapshot, "ok", current_time)
            return format_funds_alert(snapshot, "resolved", mention="")
        return None

    if _SEVERITY_RANK[snapshot.severity] > _SEVERITY_RANK.get(previous_status, 0):
        _write_alert_state(state_path, snapshot, snapshot.severity, current_time)
        return format_funds_alert(snapshot, snapshot.severity, settings.mention)

    if previous_status == snapshot.severity and _cooldown_elapsed(state, current_time, settings.cooldown_seconds):
        _write_alert_state(state_path, snapshot, snapshot.severity, current_time)
        return format_funds_alert(snapshot, snapshot.severity, settings.mention)

    if previous_status == "ok":
        _write_alert_state(state_path, snapshot, snapshot.severity, current_time)
        return format_funds_alert(snapshot, snapshot.severity, settings.mention)

    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsAlertStateTests -v
```

Expected: PASS.

- [ ] **Step 5: Run all funds tests**

Run:

```powershell
uv run python -m unittest tests.test_funds -v
```

Expected: PASS.

---

### Task 4: Kraken Balance Fetching

**Files:**
- Modify: `hermes_trading/funds.py`
- Modify: `tests/test_funds.py`

**Interfaces:**
- Produces: `extract_available_funds(balance: dict[str, Any], quote: str) -> float`
- Produces: `fetch_available_funds(settings: FundsAlertSettings, exchange_factory=None) -> float`

- [ ] **Step 1: Write the failing balance extraction tests**

Append to `tests/test_funds.py`:

```python
from hermes_trading.funds import extract_available_funds, fetch_available_funds


class FakeBalanceExchange:
    def __init__(self, balance):
        self.balance = balance
        self.closed = False

    async def fetch_balance(self):
        return self.balance

    async def close(self):
        self.closed = True


class FundsBalanceTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_available_funds_prefers_free_balance(self):
        balance = {
            "free": {"USDT": 14.82},
            "total": {"USDT": 30.0},
        }

        self.assertEqual(extract_available_funds(balance, "USDT"), 14.82)

    def test_extract_available_funds_supports_kraken_extended_shape(self):
        balance = {
            "info": {
                "result": {
                    "USDT": {
                        "balance": "30.00",
                        "credit": "0",
                        "credit_used": "0",
                        "hold_trade": "12.50",
                    }
                }
            }
        }

        self.assertEqual(extract_available_funds(balance, "USDT"), 17.5)

    async def test_fetch_available_funds_closes_exchange(self):
        fake = FakeBalanceExchange({"free": {"USDT": 14.82}})
        settings = resolve_funds_alert_settings({"HERMES_FUNDS_ALERT_QUOTE": "USDT"})

        available = await fetch_available_funds(settings, exchange_factory=lambda: fake)

        self.assertEqual(available, 14.82)
        self.assertTrue(fake.closed)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsBalanceTests -v
```

Expected: FAIL because balance functions do not exist.

- [ ] **Step 3: Implement balance extraction and fetch**

Append to `hermes_trading/funds.py`:

```python
import ccxt.async_support as ccxt


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


def extract_available_funds(balance: dict[str, Any], quote: str) -> float:
    free = balance.get("free") or {}
    if quote in free and free[quote] is not None:
        return float(free[quote])

    result = ((balance.get("info") or {}).get("result") or {})
    entry = result.get(quote)
    if isinstance(entry, dict):
        total = float(entry.get("balance", 0.0) or 0.0)
        credit = float(entry.get("credit", 0.0) or 0.0)
        credit_used = float(entry.get("credit_used", 0.0) or 0.0)
        hold_trade = float(entry.get("hold_trade", 0.0) or 0.0)
        return total + credit - credit_used - hold_trade

    return 0.0


async def fetch_available_funds(settings: FundsAlertSettings, exchange_factory=None) -> float:
    exchange = exchange_factory() if exchange_factory else _default_exchange()
    try:
        balance = await exchange.fetch_balance()
        return extract_available_funds(balance, settings.quote)
    finally:
        await exchange.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
uv run python -m unittest tests.test_funds.FundsBalanceTests -v
```

Expected: PASS.

- [ ] **Step 5: Run all tests**

Run:

```powershell
uv run python -m unittest discover -s tests
```

Expected: PASS.

---

### Task 5: Worker Loop Integration

**Files:**
- Modify: `hermes_trading/funds.py`
- Modify: `hermes_trading/loop.py`
- Modify: `tests/test_notifications.py` or create `tests/test_loop_funds.py`

**Interfaces:**
- Consumes: `resolve_funds_alert_settings`, `fetch_available_funds`, `build_funds_snapshot`, `build_funds_alert_message`
- Produces: `build_due_funds_alert(state_dir: Path, settings: FundsAlertSettings, now: datetime | None = None, exchange_factory=None) -> str | None`
- Worker behavior: append alert message to the same `messages` list used by `_notify_all`.

- [ ] **Step 1: Write the failing loop-level alert test**

Create `tests/test_loop_funds.py`:

```python
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_trading.loop import run_loop
from hermes_trading.run import ensure_state_files


class LoopFundsAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_loop_sends_low_funds_alert_when_enabled(self):
        messages = []

        async def fake_send(settings, content):
            messages.append(content)
            return True

        async def no_trade(asset, strategy):
            return {}, {
                "timestamp": "2026-07-29T09:00:00+00:00",
                "event": "decision",
                "asset": asset,
                "strategy_version": "47",
                "side": "long",
                "entry_indicator": "rsi",
                "entry_value": 50,
                "threshold": 17,
                "trade_closed": False,
                "reason": "entry_condition_not_met",
            }

        async def fake_funds_alert(state_dir, settings, now=None, exchange_factory=None):
            return "[HERMES WARNING] LOW FUNDS"

        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            ensure_state_files(state)
            with patch.dict(
                os.environ,
                {
                    "HERMES_TRADING_MODE": "training",
                    "HERMES_NOTIFY_ENABLED": "true",
                    "HERMES_NOTIFY_SERVICE": "discord",
                    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token",
                    "HERMES_FUNDS_ALERT_ENABLED": "true",
                },
                clear=False,
            ):
                with patch("hermes_trading.loop._evaluate_asset", no_trade):
                    with patch("hermes_trading.loop.send_notification", fake_send):
                        with patch("hermes_trading.loop.build_due_funds_alert", fake_funds_alert):
                            task = asyncio.create_task(run_loop("BTC/USDT", state, interval_seconds=60))
                            for _ in range(20):
                                if messages:
                                    break
                                await asyncio.sleep(0.01)
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task

        self.assertIn("[HERMES WARNING] LOW FUNDS", messages)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_loop_funds -v
```

Expected: FAIL because `hermes_trading.loop.build_due_funds_alert` is not imported or called.

- [ ] **Step 3: Implement `build_due_funds_alert`**

Append to `hermes_trading/funds.py`:

```python
async def build_due_funds_alert(
    state_dir: Path,
    settings: FundsAlertSettings,
    now: datetime | None = None,
    exchange_factory=None,
) -> str | None:
    if not settings.enabled:
        return None
    available = await fetch_available_funds(settings, exchange_factory=exchange_factory)
    snapshot = build_funds_snapshot(settings, available=available)
    return build_funds_alert_message(state_dir / "funds_alert.json", settings, snapshot, now)
```

- [ ] **Step 4: Integrate into `run_loop`**

Modify imports in `hermes_trading/loop.py`:

```python
from hermes_trading.funds import build_due_funds_alert, resolve_funds_alert_settings
```

Initialize settings near the existing notification settings:

```python
funds_alert_settings = resolve_funds_alert_settings()
```

After summary notifications are appended, add:

```python
funds_alert = await build_due_funds_alert(state_dir, funds_alert_settings, started_at)
if funds_alert:
    messages.append(funds_alert)
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```powershell
uv run python -m unittest tests.test_loop_funds -v
```

Expected: PASS.

- [ ] **Step 6: Run all tests**

Run:

```powershell
uv run python -m unittest discover -s tests
```

Expected: PASS.

---

### Task 6: State File Bootstrap and Documentation

**Files:**
- Modify: `hermes_trading/run.py`
- Modify: `tests/test_execution.py`
- Modify: `docs/NOTIFICATIONS.md`
- Modify: `docs/SAFETY.md`
- Modify: `docs/STATE_FILES.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/COMMANDS.md`

**Interfaces:**
- Consumes: `state/funds_alert.json`
- Produces: documented activation and recovery flow.

- [ ] **Step 1: Write failing bootstrap test**

Add to `tests/test_execution.py`:

```python
    def test_ensure_state_files_creates_funds_alert_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)

            ensure_state_files(state)

            self.assertTrue((state / "funds_alert.json").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_execution.StateBootstrapExecutionTests.test_ensure_state_files_creates_funds_alert_state -v
```

Expected: FAIL because `funds_alert.json` is not seeded.

- [ ] **Step 3: Seed `funds_alert.json`**

Modify `hermes_trading/run.py` so the state file seeding includes `funds_alert.json`.

Expected shape:

```json
{"status":"ok"}
```

If the current implementation loops over JSONL files, keep JSONL files and JSON state files separate so `funds_alert.json` is not created as an empty JSONL journal.

- [ ] **Step 4: Run bootstrap test**

Run:

```powershell
uv run python -m unittest tests.test_execution.StateBootstrapExecutionTests.test_ensure_state_files_creates_funds_alert_state -v
```

Expected: PASS.

- [ ] **Step 5: Update docs**

Update `docs/NOTIFICATIONS.md` with:

```text
HERMES_FUNDS_ALERT_ENABLED=true
HERMES_FUNDS_ALERT_QUOTE=USDT
HERMES_FUNDS_MIN_AVAILABLE_USDT=20
HERMES_FUNDS_MIN_ORDER_MULTIPLE=3
HERMES_FUNDS_BUFFER_PCT=0.10
HERMES_FUNDS_ALERT_COOLDOWN_SECONDS=21600
HERMES_FUNDS_ALERT_RESOLVE_MULTIPLE=1.20
HERMES_FUNDS_ALERT_MENTION=@here
```

Add examples:

```text
@here
[HERMES WARNING] LOW FUNDS

Quote: USDT
Available: 14.82
Low limit: 20.00
Critical limit: 5.00

Action: add funds or reduce order size
```

```text
@here
[HERMES CRITICAL] FUNDS TOO LOW

Quote: USDT
Available: 3.91
Low limit: 20.00
Critical limit: 5.00

Action: pause live trading or add funds now
```

```text
[HERMES RESOLVED] FUNDS BACK ABOVE LIMIT

Quote: USDT
Available: 26.40
Resolve limit: 24.00
```

Update `docs/STATE_FILES.md` with `funds_alert.json` state shape and purpose.

Update `docs/SAFETY.md` with a live-readiness note: low-funds alerts should be enabled before switching to real orders.

Update `docs/OPERATIONS.md` and `docs/COMMANDS.md` with Railway activation commands.

- [ ] **Step 6: Run all tests**

Run:

```powershell
uv run python -m unittest discover -s tests
```

Expected: PASS.

---

### Task 7: Graphify and Final Verification

**Files:**
- Modify: `scripts/build_docs_graph.py`
- Generated: `docs/graph/nodes.json`
- Generated: `docs/graph/edges.json`
- Generated graphify outputs already managed by `scripts/build_docs_graph.py`

**Interfaces:**
- Consumes: docs from Task 6.
- Produces: updated local graph with a `low_funds_alerts` concept.

- [ ] **Step 1: Add graph concept**

Modify `scripts/build_docs_graph.py` concept list with:

```python
{
    "id": "low_funds_alerts",
    "label": "Low Funds Alerts",
    "source": "docs/NOTIFICATIONS.md",
    "summary": "Discord safety alerts for WARNING, CRITICAL, and RESOLVED funds levels based on available quote balance and order budget.",
    "tags": ["notifications", "safety", "funds"],
}
```

Add edges:

```python
("discord_notifications", "low_funds_alerts", "publishes"),
("live_trading_guardrails", "low_funds_alerts", "recommends"),
("low_funds_alerts", "railway_state_volume", "stores_state"),
```

- [ ] **Step 2: Regenerate graph**

Run:

```powershell
uv run python scripts\build_docs_graph.py
```

Expected output includes a generated node/edge count with no error.

- [ ] **Step 3: Run full verification**

Run:

```powershell
uv run python -m unittest discover -s tests
uv run python scripts\build_docs_graph.py
```

Expected: all tests pass and graph generation succeeds.

- [ ] **Step 4: Optional Railway deployment**

Deploy only after local verification:

```powershell
railway.cmd up
railway.cmd status
```

Expected: service returns `Online`.

Do not set `HERMES_FUNDS_ALERT_ENABLED=true` automatically unless the operator explicitly asks for activation.

---

## Self-Review

- Spec coverage: thresholds, severity alignment, message formatting, anti-spam cooldown, resolved hysteresis, state persistence, Kraken balance fetching, worker integration, docs, and graphify are covered.
- Placeholder scan: no `TBD`, `TODO`, or vague “add tests” items remain; every task includes concrete test commands and expected outcomes.
- Type consistency: `FundsAlertSettings`, `FundsSnapshot`, `build_funds_snapshot`, `format_funds_alert`, `build_funds_alert_message`, `fetch_available_funds`, and `build_due_funds_alert` are defined before use in later tasks.
