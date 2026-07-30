# Live Kraken Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real Kraken Spot order execution while preserving the current safe training mode and blocking live orders unless every risk gate is deliberately enabled.

**Architecture:** Keep market-data collection separate from order execution by adding a dedicated execution adapter. The loop will continue producing decisions for all assets, but only a validated trade signal can become a live order, and every order attempt will be journaled. Live execution must start with Kraken validation-only requests before any real order submission is allowed.

**Tech Stack:** Python 3.11, `ccxt.async_support`, `unittest`, Railway env vars, Kraken Spot REST via CCXT, JSONL state journals.

## Global Constraints

- Default execution remains simulated: `paper`, `training`, `train`, and `demo` must never place orders.
- `live` must still require `HERMES_TRADING_I_ACCEPT_RISK=true`.
- `live` must still require `HERMES_TRADING_LIVE_CONFIRM=I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS`.
- A new validation gate must require `HERMES_TRADING_LIVE_VALIDATE_ONLY=true` until manual promotion.
- A real order gate must require `HERMES_TRADING_ALLOW_REAL_ORDERS=true`.
- Never enable withdrawals or funding operations.
- Never log API keys, API secrets, or full Railway variable payloads.
- Use Kraken Spot only for this implementation; Futures sandbox/live is out of scope.
- Use Kraken/CCXT market precision and limits before order sizing.
- Only support small market buy entries in the first live version; sells/exits can be added later after position accounting exists.

---

## File Structure

- Create `hermes_trading/adapters/execution.py`: execution adapter interface, simulated executor, Kraken live executor, risk gate checks, order journal payloads.
- Modify `hermes_trading/loop.py`: split signal generation from execution, call the selected executor, write `orders.jsonl`, keep `decisions.jsonl` and `trades.jsonl`.
- Modify `hermes_trading/run.py`: seed `orders.jsonl` and any required live config defaults.
- Modify `tests/test_score_reflect.py` or split new tests into `tests/test_execution.py`: test live gates, validation-only order path, sizing caps, and simulated compatibility.
- Modify `docs/SAFETY.md`: document the new live gating sequence.
- Modify `docs/KRAKEN.md`: document Kraken permissions, validation-only dry run, and minimum order requirements.
- Modify `docs/OPERATIONS.md`: add live readiness checklist and rollback steps.
- Modify `scripts/build_docs_graph.py`: include any new docs or `orders.jsonl` concept if needed.

---

### Task 1: Add Execution Configuration And Gates

**Files:**
- Create: `hermes_trading/adapters/execution.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: environment variables through `os.environ` or an injected `dict[str, str]`.
- Produces: `ExecutionSettings`, `ExecutionSafetyError`, `resolve_execution_settings(env: dict[str, str] | None = None) -> ExecutionSettings`.

- [ ] **Step 1: Write the failing tests**

```python
import unittest

from hermes_trading.adapters.execution import ExecutionSafetyError, resolve_execution_settings
from hermes_trading.loop import LIVE_CONFIRM_PHRASE


class ExecutionSettingsTests(unittest.TestCase):
    def test_training_mode_selects_simulated_executor(self):
        settings = resolve_execution_settings({"HERMES_TRADING_MODE": "training"})

        self.assertEqual(settings.mode, "training")
        self.assertEqual(settings.executor, "simulated")
        self.assertFalse(settings.allow_real_orders)
        self.assertTrue(settings.validate_only)

    def test_live_requires_validation_only_by_default(self):
        settings = resolve_execution_settings(
            {
                "HERMES_TRADING_MODE": "live",
                "HERMES_TRADING_I_ACCEPT_RISK": "true",
                "HERMES_TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE,
                "HERMES_TRADING_LIVE_VALIDATE_ONLY": "true",
            }
        )

        self.assertEqual(settings.executor, "kraken")
        self.assertTrue(settings.validate_only)
        self.assertFalse(settings.allow_real_orders)

    def test_real_live_orders_need_separate_allow_flag(self):
        with self.assertRaises(ExecutionSafetyError):
            resolve_execution_settings(
                {
                    "HERMES_TRADING_MODE": "live",
                    "HERMES_TRADING_I_ACCEPT_RISK": "true",
                    "HERMES_TRADING_LIVE_CONFIRM": LIVE_CONFIRM_PHRASE,
                    "HERMES_TRADING_LIVE_VALIDATE_ONLY": "false",
                }
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_execution -v`

Expected: import failure for `hermes_trading.adapters.execution`.

- [ ] **Step 3: Implement settings and gates**

```python
from __future__ import annotations

import os
from dataclasses import dataclass

from hermes_trading.loop import LIVE_CONFIRM_PHRASE


class ExecutionSafetyError(RuntimeError):
    """Raised when live execution is not explicitly authorized."""


@dataclass(frozen=True)
class ExecutionSettings:
    mode: str
    executor: str
    validate_only: bool
    allow_real_orders: bool


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m unittest tests.test_execution -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hermes_trading/adapters/execution.py tests/test_execution.py
git commit -m "feat: add live execution safety gates"
```

---

### Task 2: Convert Paper Trades Into Explicit Trade Signals

**Files:**
- Modify: `hermes_trading/loop.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: current `_paper_trade(asset, market, strategy) -> dict`.
- Produces: `decision["signal"]` with `action`, `order_type`, `side`, `asset`, `reference_price`, and `reason`.

- [ ] **Step 1: Write the failing test**

```python
from hermes_trading.loop import _paper_trade


def test_trade_signal_contains_order_intent_when_entry_fires():
    market = {"price": {"last": 100.0, "closes": [100.0] * 15 + [80.0]}}
    strategy = {"version": "46", "entry": {"indicator": "rsi", "threshold": 100, "direction": "long"}}

    decision = _paper_trade("BTC/USDT", market, strategy)

    assert decision["trade_closed"] is True
    assert decision["signal"] == {
        "action": "enter",
        "order_type": "market",
        "side": "buy",
        "asset": "BTC/USDT",
        "reference_price": 80.0,
        "reason": "rsi_entry",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_execution -v`

Expected: FAIL because `signal` is absent.

- [ ] **Step 3: Add signal payload**

In `_paper_trade`, add this key only in the `should_enter` branch:

```python
"signal": {
    "action": "enter",
    "order_type": "market",
    "side": "buy",
    "asset": asset,
    "reference_price": price_data["last"],
    "reason": "rsi_entry",
},
```

- [ ] **Step 4: Run full tests**

Run: `uv run python -m unittest discover -s tests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hermes_trading/loop.py tests/test_execution.py
git commit -m "feat: expose executable trade signals"
```

---

### Task 3: Implement Simulated And Kraken Executors

**Files:**
- Modify: `hermes_trading/adapters/execution.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: `signal: dict[str, object]`, `strategy: dict[str, object]`, `settings: ExecutionSettings`.
- Produces: `execute_signal(signal, strategy, settings, exchange_factory=None) -> dict[str, object] | None`.

- [ ] **Step 1: Write the failing tests**

```python
import unittest

from hermes_trading.adapters.execution import ExecutionSettings, execute_signal


class ExecuteSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulated_executor_returns_no_order_when_no_signal(self):
        settings = ExecutionSettings("training", "simulated", validate_only=True, allow_real_orders=False)

        result = await execute_signal(None, {}, settings)

        self.assertIsNone(result)

    async def test_simulated_executor_records_virtual_order(self):
        settings = ExecutionSettings("training", "simulated", validate_only=True, allow_real_orders=False)
        signal = {"action": "enter", "order_type": "market", "side": "buy", "asset": "BTC/USDT", "reference_price": 100.0}

        result = await execute_signal(signal, {"position_size_r": 0.5}, settings)

        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["asset"], "BTC/USDT")
        self.assertEqual(result["side"], "buy")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_execution -v`

Expected: FAIL because `execute_signal` is absent.

- [ ] **Step 3: Implement simulated executor first**

```python
from datetime import datetime, timezone
from typing import Any


async def execute_signal(
    signal: dict[str, Any] | None,
    strategy: dict[str, Any],
    settings: ExecutionSettings,
    exchange_factory=None,
) -> dict[str, Any] | None:
    if not signal:
        return None
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
    raise ExecutionSafetyError(f"unsupported executor={settings.executor!r}")
```

- [ ] **Step 4: Add Kraken validation-only test with fake exchange**

```python
class FakeExchange:
    def __init__(self):
        self.closed = False
        self.orders = []

    async def load_markets(self):
        return {
            "BTC/USDT": {
                "limits": {"amount": {"min": 0.00001}, "cost": {"min": 1.0}},
                "precision": {"amount": 8},
            }
        }

    def amount_to_precision(self, asset, amount):
        return f"{float(amount):.8f}"

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.orders.append((symbol, type, side, amount, price, params))
        return {"id": "validated-order", "status": "open", "info": {"validate": params.get("validate")}}

    async def close(self):
        self.closed = True


class KrakenExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_kraken_validate_only_uses_validate_param(self):
        fake = FakeExchange()
        settings = ExecutionSettings("live", "kraken", validate_only=True, allow_real_orders=False)
        signal = {"action": "enter", "order_type": "market", "side": "buy", "asset": "BTC/USDT", "reference_price": 100.0}

        result = await execute_signal(signal, {"position_size_r": 0.5}, settings, exchange_factory=lambda: fake)

        self.assertEqual(result["status"], "validated")
        self.assertEqual(fake.orders[0][5]["validate"], True)
        self.assertTrue(fake.closed)
```

- [ ] **Step 5: Implement Kraken validation-only path**

Use CCXT `create_order(symbol, type, side, amount, price, params)` with `params={"validate": True}` while `settings.validate_only` is true. Size amount from a conservative quote risk budget, for example:

```python
quote_budget = float(os.getenv("HERMES_TRADING_MAX_ORDER_USDT", "5"))
amount = quote_budget / float(signal["reference_price"])
```

Then apply `exchange.amount_to_precision(asset, amount)` and reject if below market limits.

- [ ] **Step 6: Run full tests**

Run: `uv run python -m unittest discover -s tests`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/adapters/execution.py tests/test_execution.py
git commit -m "feat: add kraken validation-only executor"
```

---

### Task 4: Wire Executor Into The Worker Loop

**Files:**
- Modify: `hermes_trading/loop.py`
- Modify: `hermes_trading/run.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: `execute_signal(...)`.
- Produces: `state/orders.jsonl`, `heartbeat.execution`, `heartbeat.live_validate_only`, `heartbeat.order_count`.

- [ ] **Step 1: Write the failing test for state seeding**

```python
import tempfile
from pathlib import Path

from hermes_trading.run import ensure_state_files


def test_ensure_state_files_creates_orders_journal():
    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)

        ensure_state_files(state)

        assert (state / "orders.jsonl").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_execution -v`

Expected: FAIL because `orders.jsonl` is not created.

- [ ] **Step 3: Seed `orders.jsonl`**

Modify `ensure_state_files`:

```python
for name in ["trades.jsonl", "decisions.jsonl", "hypotheses.jsonl", "orders.jsonl"]:
    (state_dir / name).touch(exist_ok=True)
```

- [ ] **Step 4: Replace hard live block with executor settings**

In `run_loop`, remove this unconditional refusal:

```python
if trading.execution != "simulated":
    raise TradingModeError("live execution adapter is not implemented; refusing to place real orders")
```

Then resolve execution settings and call `execute_signal` after each decision:

```python
execution_settings = resolve_execution_settings()
order = await execute_signal(decision.get("signal"), strategy, execution_settings)
if order:
    orders.append(order)
    await _append_jsonl(state_dir / "orders.jsonl", order)
```

- [ ] **Step 5: Preserve simulated behavior**

Keep `decisions.jsonl` written for every asset and `trades.jsonl` written only when `decision["trade_closed"]` is true. Do not make live orders affect no-trade decisions.

- [ ] **Step 6: Run full tests**

Run: `uv run python -m unittest discover -s tests`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/loop.py hermes_trading/run.py tests/test_execution.py
git commit -m "feat: wire execution adapter into worker loop"
```

---

### Task 5: Add Operational Live Readiness Checks

**Files:**
- Create: `scripts/check_live_readiness.py`
- Test: `tests/test_execution.py`

**Interfaces:**
- Consumes: environment variables and `state/strategy.yaml`.
- Produces: exit code `0` when validation-only live is configured safely; non-zero with plain error messages otherwise.

- [ ] **Step 1: Write the failing tests**

```python
import os
import subprocess
import sys
import unittest


class LiveReadinessScriptTests(unittest.TestCase):
    def test_readiness_rejects_real_orders_without_allow_flag(self):
        env = os.environ.copy()
        env.update(
            {
                "HERMES_TRADING_MODE": "live",
                "HERMES_TRADING_I_ACCEPT_RISK": "true",
                "HERMES_TRADING_LIVE_CONFIRM": "I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS",
                "HERMES_TRADING_LIVE_VALIDATE_ONLY": "false",
                "HERMES_TRADING_ALLOW_REAL_ORDERS": "false",
            }
        )

        result = subprocess.run([sys.executable, "scripts/check_live_readiness.py"], env=env, text=True, capture_output=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("real orders require", result.stderr)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m unittest tests.test_execution -v`

Expected: FAIL because script is absent.

- [ ] **Step 3: Implement readiness script**

```python
from __future__ import annotations

import sys

from hermes_trading.adapters.execution import ExecutionSafetyError, resolve_execution_settings


def main() -> int:
    try:
        settings = resolve_execution_settings()
    except ExecutionSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"mode={settings.mode} executor={settings.executor} validate_only={settings.validate_only}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run readiness locally in validation-only mode**

Run:

```powershell
$env:HERMES_TRADING_MODE = "live"
$env:HERMES_TRADING_I_ACCEPT_RISK = "true"
$env:HERMES_TRADING_LIVE_CONFIRM = "I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS"
$env:HERMES_TRADING_LIVE_VALIDATE_ONLY = "true"
uv run python scripts\check_live_readiness.py
```

Expected: exit `0`, output includes `validate_only=True`.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_live_readiness.py tests/test_execution.py
git commit -m "feat: add live readiness check"
```

---

### Task 6: Document Promotion From Training To Live

**Files:**
- Modify: `docs/SAFETY.md`
- Modify: `docs/KRAKEN.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `scripts/build_docs_graph.py`

**Interfaces:**
- Consumes: implemented env flags and `orders.jsonl`.
- Produces: documentation and regenerated Graphify/Obsidian outputs.

- [ ] **Step 1: Update docs with exact promotion order**

Document this sequence:

```text
1. Keep HERMES_TRADING_MODE=training.
2. Deploy code and verify tests.
3. Configure Kraken key with Orders and trades - Create & modify orders, no withdrawals.
4. Set HERMES_TRADING_MODE=live.
5. Set HERMES_TRADING_I_ACCEPT_RISK=true.
6. Set HERMES_TRADING_LIVE_CONFIRM=I_UNDERSTAND_THIS_PLACES_REAL_KRAKEN_ORDERS.
7. Set HERMES_TRADING_LIVE_VALIDATE_ONLY=true.
8. Run at least one validation-only cycle and inspect orders.jsonl.
9. Only after manual approval, set HERMES_TRADING_ALLOW_REAL_ORDERS=true and HERMES_TRADING_LIVE_VALIDATE_ONLY=false.
```

- [ ] **Step 2: Document rollback**

Document this immediate rollback:

```powershell
railway.cmd variables --set "HERMES_TRADING_MODE=training"
railway.cmd variables --set "HERMES_TRADING_I_ACCEPT_RISK=false"
railway.cmd variables --set "HERMES_TRADING_LIVE_CONFIRM=disabled"
railway.cmd variables --set "HERMES_TRADING_LIVE_VALIDATE_ONLY=true"
railway.cmd variables --set "HERMES_TRADING_ALLOW_REAL_ORDERS=false"
railway.cmd up
```

- [ ] **Step 3: Regenerate Graphify/Obsidian**

Run: `uv run python scripts\build_docs_graph.py`

Expected: `graphify-out/graph.json`, `graphify-out/GRAPH_REPORT.md`, and `graphify-out/obsidian/graph.canvas` update.

- [ ] **Step 4: Run full tests**

Run: `uv run python -m unittest discover -s tests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs scripts graphify-out
git commit -m "docs: document live trading promotion"
```

---

## Self-Review

Spec coverage:

- Verifies current live status: covered before this plan and in Task 1.
- Adds Kraken execution: Task 3.
- Preserves simulated modes: Tasks 1, 3, and 4.
- Adds operational journals: Task 4.
- Adds readiness and rollback: Tasks 5 and 6.
- Updates local docs, Graphify and Obsidian: Task 6.

Placeholder scan:

- No `TBD` or unspecified implementation steps remain.
- Every task has concrete files, commands, and expected results.

Type consistency:

- `ExecutionSettings`, `ExecutionSafetyError`, `resolve_execution_settings`, and `execute_signal` are introduced before use.
- `orders.jsonl` is seeded before loop writes.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-28-live-kraken-trading.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints
