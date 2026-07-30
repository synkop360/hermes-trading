# Kraken Risk And Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter une execution Kraken Spot long-only idempotente, des stops distants et un moteur de risque persistant qui bloque toute nouvelle entree lorsque l'etat est incertain.

**Architecture:** Le RiskEngine calcule un notional autorise avec `Decimal` avant toute intention. KrakenGateway encapsule CCXT, tandis que ExecutionService persiste l'intention, soumet, reconcilie et protege la position. ReconciliationService est l'unique chemin de reprise apres timeout, redemarrage ou divergence.

**Tech Stack:** Python 3.11, CCXT async, `Decimal`, SQLite WAL, `unittest`, `AsyncMock`.

## Global Constraints

- Kraken Spot, long-only, sans levier.
- Une position maximum par symbole.
- Risque par trade `0.25 %`, exposition symbole `5 %`, exposition totale `10 %`.
- Perte journaliere `0.5 %`, drawdown global `3 %`.
- Le plafond `HERMES_TRADING_MAX_ORDER_USDT` reste absolu.
- Le stop est place chez Kraken.
- Une reponse ambigue n'est jamais renvoyee sans reconciliation.
- La cle Kraken ne possede aucune permission de retrait.
- Le mode Railway reste `training` ou `validation-only`.

---

## File Structure

- Create `hermes_trading/risk.py`: configuration, taille et kill switch.
- Create `hermes_trading/exchange.py`: protocole exchange et normalisation CCXT.
- Create `hermes_trading/execution.py`: service d'intentions et soumission.
- Create `hermes_trading/reconciliation.py`: comparaison locale/distante.
- Modify `hermes_trading/adapters/execution.py`: facade de compatibilite.
- Modify `hermes_trading/funds.py`: reutiliser le solde du cycle.
- Modify `hermes_trading/loop.py`: risk gate et reconciliation.
- Modify `scripts/check_live_readiness.py`: readiness complete.
- Create `tests/fakes/kraken.py`: faux exchange a etats.
- Create `tests/test_risk.py`, `tests/test_exchange.py`,
  `tests/test_reconciliation.py`, `tests/test_live_cycle.py`.

### Task 1: Implement Risk Sizing And Persistent Stops

**Files:**
- Create: `hermes_trading/risk.py`
- Modify: `hermes_trading/config.py`
- Test: `tests/test_risk.py`

**Interfaces:**
- Consumes: `Position`, soldes et expositions `Decimal`.
- Produces: `RiskLimits`, `RiskContext`, `RiskDecision`,
  `resolve_risk_limits(env)`, `RiskEngine.authorize_entry()`,
  `RiskEngine.activate_kill_switch()`, `RiskEngine.resume()`.

- [ ] **Step 1: Write failing sizing and hard-limit tests**

```python
class RiskEngineTests(unittest.TestCase):
    def test_size_is_minimum_of_all_caps(self):
        decision = self.engine.authorize_entry(
            context=RiskContext(
                equity=Decimal("1000"),
                free_quote=Decimal("100"),
                symbol_exposure=Decimal("0"),
                total_exposure=Decimal("0"),
                daily_pnl=Decimal("0"),
                drawdown=Decimal("0"),
            ),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.notional, Decimal("5"))

    def test_daily_loss_activates_kill_switch(self):
        decision = self.engine.authorize_entry(
            context=context(daily_pnl=Decimal("-5.01")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "daily_loss_limit")
        self.assertTrue(self.repository.risk_state().kill_switch)

    def test_below_exchange_minimum_is_no_order(self):
        decision = self.engine.authorize_entry(
            context=context(free_quote=Decimal("2")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )
        self.assertEqual(decision.reason_code, "below_exchange_minimum")

    def test_shib_uses_stricter_spread_and_slippage_limits(self):
        decision = self.engine.authorize_entry(
            context=context(symbol="SHIB/USDT", spread_bps=Decimal("35")),
            stop_loss_pct=Decimal("0.01"),
            position_size_r=Decimal("1"),
            exchange_min_cost=Decimal("5"),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "spread_limit")
```

- [ ] **Step 2: Run risk tests and verify the module is missing**

Run: `uv run python -m unittest tests.test_risk -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement strict configuration and formula**

```python
@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: Decimal = Decimal("0.0025")
    max_symbol_exposure_pct: Decimal = Decimal("0.05")
    max_total_exposure_pct: Decimal = Decimal("0.10")
    max_daily_loss_pct: Decimal = Decimal("0.005")
    max_drawdown_pct: Decimal = Decimal("0.03")
    max_order_quote: Decimal = Decimal("5")


risk_budget = context.equity * limits.risk_per_trade_pct * position_size_r
risk_sized = risk_budget / stop_loss_pct
notional = min(
    risk_sized,
    limits.max_order_quote,
    context.equity * limits.max_symbol_exposure_pct - context.symbol_exposure,
    context.equity * limits.max_total_exposure_pct - context.total_exposure,
    context.free_quote,
)
```

Reject zero/negative stop, multiplier outside `0 < r <= 1`, unknown balances,
active position, kill switch and unresolved incidents. Persist every decision
and project `risk_status.json`. Add per-symbol execution-quality limits to
configuration; SHIB has stricter maximum spread and modeled slippage than BTC,
ETH and XRP. Missing or stale spread data produces `no_trade`.

- [ ] **Step 4: Test resume authorization**

Add a test proving `resume()` requires `authorized=True`, an incident-free
state and a fresh reconciliation marker; otherwise it leaves the switch active.

- [ ] **Step 5: Run and commit**

Run: `uv run python -m unittest tests.test_risk -v`

```bash
git add hermes_trading/risk.py hermes_trading/config.py tests/test_risk.py
git commit -m "feat: enforce portfolio risk limits"
```

### Task 2: Define Kraken Gateway And Stateful Fake

**Files:**
- Create: `hermes_trading/exchange.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/kraken.py`
- Test: `tests/test_exchange.py`

**Interfaces:**
- Consumes: CCXT async exchange.
- Produces: `ExchangeGateway` protocol, `KrakenGateway`,
  `ExchangeOrder`, `ExchangeFill`, `MarketRules`, `ApiKeyInfo`,
  `load_rules()`, `fetch_balances()`, `fetch_orders()`, `fetch_order_by_client_id()`,
  `create_market_order()`, `create_stop_order()`, `cancel_order()`.

- [ ] **Step 1: Write failing normalization tests**

```python
class KrakenGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_fill_is_normalized_with_decimal_values(self):
        fake = FakeKrakenExchange.partial_fill()
        order = await KrakenGateway(lambda: fake).fetch_order("KRAKEN-1", "BTC/USDT")
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(order.filled, Decimal("0.00005"))
        self.assertEqual(order.fee, Decimal("0.01"))

    async def test_permissions_reject_withdrawal_access(self):
        info = ApiKeyInfo(permissions=frozenset({"query-funds", "modify-trades", "withdraw-funds"}))
        with self.assertRaises(UnsafeApiKeyPermissions):
            validate_api_key_permissions(info)
```

- [ ] **Step 2: Run exchange tests and verify failure**

Run: `uv run python -m unittest tests.test_exchange -v`

Expected: gateway imports are missing.

- [ ] **Step 3: Implement protocol and CCXT adapter**

The protocol methods return domain dataclasses only. Map CCXT statuses to:
`pending`, `open`, `partially_filled`, `filled`, `cancelled`, `rejected`,
`unknown`. Unknown values remain `unknown` and trigger reconciliation.

`KrakenGateway` owns one exchange instance per cycle and implements
`async close()`. It passes `cl_ord_id` in the Kraken parameters and never logs
raw credentials or complete private responses.

- [ ] **Step 4: Implement the deterministic fake**

`FakeKrakenExchange` supports scripted responses:

```python
fake.queue_create_order(TimeoutError("response lost"))
fake.seed_order(client_order_id="ht-abc", status="closed", filled="0.0001")
```

It tracks calls so tests can assert no duplicate submission occurred.

- [ ] **Step 5: Run and commit**

Run: `uv run python -m unittest tests.test_exchange -v`

```bash
git add hermes_trading/exchange.py tests/fakes tests/test_exchange.py
git commit -m "feat: normalize kraken exchange operations"
```

### Task 3: Persist Intent Before Submission

**Files:**
- Create: `hermes_trading/execution.py`
- Modify: `hermes_trading/adapters/execution.py`
- Test: `tests/test_execution_service.py`

**Interfaces:**
- Consumes: `RiskDecision`, `StateRepository`, `ExchangeGateway`.
- Produces: `ExecutionService.submit_entry()`, `submit_exit()`,
  `ensure_protective_stop()`, `client_order_id()`, `ExecutionResult`.

- [ ] **Step 1: Write failing crash-boundary tests**

```python
class ExecutionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_intent_exists_before_exchange_call(self):
        gateway = InspectingGateway(
            assert_before_call=lambda cid: self.assertIsNotNone(self.repo.intent_by_client_id(cid))
        )
        await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)

    async def test_timeout_is_not_retried_blindly(self):
        gateway = FakeKrakenExchange()
        gateway.queue_create_order(TimeoutError("lost response"))
        result = await self.service.submit_entry(sample_signal(), sample_risk_decision(), gateway)
        self.assertEqual(result.status, "reconciliation_required")
        self.assertEqual(gateway.create_order_calls, 1)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_execution_service -v`

Expected: `ExecutionService` is missing.

- [ ] **Step 3: Implement deterministic client IDs and submission**

Generate an ASCII identifier no longer than Kraken's supported client ID:

```python
digest = hashlib.blake2s(
    f"{asset}|{side}|{candle_id}|{strategy_version}".encode(),
    digest_size=8,
).hexdigest()
client_order_id = f"ht-{digest}"
```

Within one transaction, persist the decision and order intent before the
network call. A timeout sets `reconciliation_required`; it does not call
`create_order()` again.

- [ ] **Step 4: Keep the old facade compatible**

`adapters/execution.py` delegates to the service for new loop calls while
preserving `resolve_execution_settings()` and the existing validation-only
tests until the final loop migration.

- [ ] **Step 5: Run focused and legacy execution tests**

Run: `uv run python -m unittest tests.test_execution_service tests.test_execution -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add hermes_trading/execution.py hermes_trading/adapters/execution.py tests/test_execution_service.py
git commit -m "feat: persist idempotent order intents"
```

### Task 4: Reconcile Orders, Positions And Balances

**Files:**
- Create: `hermes_trading/reconciliation.py`
- Test: `tests/test_reconciliation.py`

**Interfaces:**
- Consumes: repository orders/positions, gateway remote state.
- Produces: `ReconciliationService.run()`, `ReconciliationReport`,
  `ReconciliationIssue`, `reconcile_pending_order()`.

- [ ] **Step 1: Write failing ambiguous-response and partial-fill tests**

```python
class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_response_is_found_by_client_id_without_resubmit(self):
        self.repo.create_order_intent(pending_intent("ht-known"))
        gateway = FakeKrakenExchange()
        gateway.seed_order(client_order_id="ht-known", status="closed", filled="0.0001")
        report = await ReconciliationService(self.repo).run(gateway)
        self.assertTrue(report.safe_for_new_entries)
        self.assertEqual(self.repo.order_by_client_id("ht-known").status, OrderStatus.FILLED)
        self.assertEqual(gateway.create_order_calls, 0)

    async def test_unknown_remote_order_freezes_entries(self):
        gateway = FakeKrakenExchange()
        gateway.seed_unmatched_open_order(asset="BTC/USDT")
        report = await ReconciliationService(self.repo).run(gateway)
        self.assertFalse(report.safe_for_new_entries)
        self.assertEqual(report.issues[0].code, "unknown_remote_order")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_reconciliation -v`

Expected: reconciliation module is missing.

- [ ] **Step 3: Implement comparison and issue severity**

Compare client IDs, Kraken IDs, side, quantities, status, fills, fees, balances
and expected positions. Persist issues in `incidents` and write
`system_status.json`. Only a report with no blocking issue updates the fresh
reconciliation marker used by `RiskEngine.resume()`.

- [ ] **Step 4: Add restart fixture**

Close the first repository connection after intent creation, reopen it, seed
the remote fill and prove `run()` reconstructs the position exactly once.

- [ ] **Step 5: Add adaptive REST reconciliation cadence**

Poll quickly with bounded backoff only while an order is transient or a remote
response is ambiguous. Return to the normal cycle cadence after terminal state.
Reuse the cycle exchange connection and honor Kraken rate-limit guidance.

- [ ] **Step 6: Run and commit**

Run: `uv run python -m unittest tests.test_reconciliation -v`

```bash
git add hermes_trading/reconciliation.py tests/test_reconciliation.py
git commit -m "feat: reconcile local and kraken state"
```

### Task 5: Place Stops And Emergency-Close Unprotected Fills

**Files:**
- Modify: `hermes_trading/execution.py`
- Modify: `hermes_trading/reconciliation.py`
- Test: `tests/test_protective_stops.py`

**Interfaces:**
- Consumes: filled entry and strategy stop percentage.
- Produces: `ensure_protective_stop(position, gateway)`,
  `emergency_close(position, gateway)`.

- [ ] **Step 1: Write failing stop and emergency-close tests**

```python
class ProtectiveStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_uses_actual_filled_quantity(self):
        position = position(quantity="0.00007", entry_price="65000", stop_price="64675")
        await self.service.ensure_protective_stop(position, self.gateway)
        self.assertEqual(self.gateway.last_stop.amount, Decimal("0.00007"))

    async def test_stop_failure_closes_and_activates_kill_switch(self):
        self.gateway.queue_stop_error(RuntimeError("stop rejected"))
        result = await self.service.ensure_protective_stop(sample_position(), self.gateway)
        self.assertEqual(result.status, "emergency_closed")
        self.assertTrue(self.repo.risk_state().kill_switch)
        self.assertEqual(self.gateway.market_sell_calls, 1)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_protective_stops -v`

Expected: stop workflow is missing.

- [ ] **Step 3: Implement protective stop workflow**

Calculate stop price with Decimal, normalize price and amount through market
rules, persist the stop intent, submit it and reconcile it. If placement fails
after the entry is filled, submit one idempotent market sell for the remaining
quantity, set kill switch and incident severity `critical`.

- [ ] **Step 4: Add startup stop verification**

For every local/remote open position, require a matching active stop. Missing
stops run the same emergency workflow before any new entry is considered.

- [ ] **Step 5: Run and commit**

Run: `uv run python -m unittest tests.test_protective_stops tests.test_reconciliation -v`

```bash
git add hermes_trading/execution.py hermes_trading/reconciliation.py tests/test_protective_stops.py
git commit -m "feat: protect live positions with kraken stops"
```

### Task 6: Integrate Risk And Reconciliation Into The Worker

**Files:**
- Modify: `hermes_trading/loop.py`
- Modify: `hermes_trading/funds.py`
- Modify: `scripts/check_live_readiness.py`
- Test: `tests/test_live_cycle.py`

**Interfaces:**
- Consumes: Plan 1 `run_cycle()`, RiskEngine, ExecutionService and ReconciliationService.
- Produces: live-capable `run_cycle()`, expanded readiness report.

- [ ] **Step 1: Write failing full-cycle safety tests**

```python
class LiveCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_reconciles_before_entry(self):
        result = await run_live_cycle(fixture_with_unknown_remote_order())
        self.assertEqual(result.new_orders, ())
        self.assertEqual(result.status, "blocked_reconciliation")

    async def test_validate_only_creates_no_remote_order(self):
        result = await run_live_cycle(valid_fixture(validate_only=True))
        self.assertEqual(result.orders[0].status, OrderStatus.VALIDATED)
        self.assertEqual(result.gateway.real_order_calls, 0)
```

- [ ] **Step 2: Run live-cycle tests and verify failure**

Run: `uv run python -m unittest tests.test_live_cycle -v`

Expected: live orchestration is not wired.

- [ ] **Step 3: Wire strict cycle ordering**

The order is:

```text
reconcile -> refresh balances -> manage exits/stops -> collect new candles
-> evaluate entries -> authorize risk -> persist intent -> execute -> reconcile
-> project artifacts -> notify
```

Reuse the already-fetched balance for funds alerts. A funds-alert transport
failure remains non-fatal, but an unknown balance blocks entries.

- [ ] **Step 4: Expand readiness checks**

`check_live_readiness.py` must report and exit non-zero for:

- invalid mode gates;
- missing or unsafe API permissions;
- failed SQLite integrity;
- stale reconciliation;
- active kill switch;
- missing risk configuration;
- unsupported market/minimum;
- non-validation real-order configuration before qualification.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m unittest discover -s tests`

Expected: all tests pass.

- [ ] **Step 6: Update graph and commit**

Run: `graphify update .`

```bash
git add hermes_trading/loop.py hermes_trading/funds.py scripts/check_live_readiness.py tests/test_live_cycle.py graphify-out
git commit -m "feat: gate live cycles on risk and reconciliation"
```
