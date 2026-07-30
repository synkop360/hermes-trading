# State And Simulation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer les trades instantanement fermes par un cycle de position simule fidele, persiste dans SQLite WAL et toujours inspectable par JSON/JSONL.

**Architecture:** Des types de domaine bases sur `Decimal` separent decisions, ordres, fills et positions. Un repository SQLite synchrone a transactions courtes est la source canonique; un projecteur ecrit apres chaque transaction les journaux append-only et les instantanes atomiques. Le worker traite uniquement les bougies closes et dedupliquees.

**Tech Stack:** Python 3.11, `sqlite3`, `decimal.Decimal`, `dataclasses`, `asyncio`, `unittest`, PyYAML.

## Global Constraints

- SQLite WAL est la source canonique.
- Chaque transition financiere est aussi lisible dans JSONL.
- Les instantanes JSON sont ecrits atomiquement.
- Aucun montant financier n'est calcule avec un flottant binaire.
- Une seule decision est autorisee par symbole et bougie close.
- Le mode Railway reste `training`.
- Les tests utilisent `uv run python -m unittest`.

---

## File Structure

- Create `hermes_trading/domain.py`: types immuables et conversions Decimal/JSON.
- Create `hermes_trading/persistence/__init__.py`: exports publics du stockage.
- Create `hermes_trading/persistence/migrations.py`: schema SQLite versionne.
- Create `hermes_trading/persistence/repository.py`: transactions et requetes metier.
- Create `hermes_trading/persistence/artifacts.py`: JSONL et instantanes atomiques.
- Create `hermes_trading/market_data.py`: collecte partagee et identite des bougies.
- Create `hermes_trading/strategy.py`: signal champion pur et validation de strategie.
- Create `hermes_trading/positions.py`: machine d'etat des positions simulees.
- Modify `hermes_trading/loop.py`: orchestration des nouveaux composants.
- Modify `hermes_trading/run.py`: bootstrap SQLite et fichiers humains.
- Create `scripts/inspect_state.py`: inspection sans mutation.
- Create `tests/test_persistence.py`, `tests/test_market_data.py`,
  `tests/test_positions.py`, `tests/test_training_loop.py`.

### Task 1: Define Stable Domain Types

**Files:**
- Create: `hermes_trading/domain.py`
- Test: `tests/test_domain.py`

**Interfaces:**
- Consumes: aucune interface nouvelle.
- Produces: `Decision`, `OrderIntent`, `OrderRecord`, `Fill`, `Position`,
  `PositionStatus`, `OrderStatus`, `decimal_from()`, `json_value()`.

- [ ] **Step 1: Write the failing domain serialization tests**

```python
from decimal import Decimal
import unittest

from hermes_trading.domain import Position, PositionStatus, decimal_from, json_value


class DomainTests(unittest.TestCase):
    def test_decimal_from_rejects_binary_float(self):
        with self.assertRaises(TypeError):
            decimal_from(0.1)

    def test_position_serializes_decimals_as_strings(self):
        position = Position(
            position_id="pos-btc-1",
            asset="BTC/USDT",
            status=PositionStatus.OPEN,
            quantity=Decimal("0.00010"),
            entry_price=Decimal("65000.0"),
            stop_price=Decimal("64675.0"),
            strategy_version="69",
            opened_at="2026-07-30T20:00:00+00:00",
        )
        payload = json_value(position)
        self.assertEqual(payload["quantity"], "0.00010")
        self.assertEqual(payload["status"], "open")
```

- [ ] **Step 2: Run the domain tests and verify the import fails**

Run: `uv run python -m unittest tests.test_domain -v`

Expected: `ModuleNotFoundError: No module named 'hermes_trading.domain'`.

- [ ] **Step 3: Implement the domain enums, dataclasses and strict conversion**

```python
class PositionStatus(str, Enum):
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


def decimal_from(value: str | int | Decimal) -> Decimal:
    if isinstance(value, float):
        raise TypeError("binary float is forbidden for financial values")
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class Position:
    position_id: str
    asset: str
    status: PositionStatus
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    strategy_version: str
    opened_at: str
    closed_at: str | None = None
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
```

Add equivalent explicit dataclasses for decisions, intents, orders and fills.
`json_value()` must recursively convert dataclasses, enums and `Decimal` to
plain JSON values while preserving decimals as strings.

- [ ] **Step 4: Run the focused tests**

Run: `uv run python -m unittest tests.test_domain -v`

Expected: all domain tests pass.

- [ ] **Step 5: Commit the domain contract**

```bash
git add hermes_trading/domain.py tests/test_domain.py
git commit -m "feat: define trading domain contracts"
```

### Task 2: Add SQLite WAL And Migrations

**Files:**
- Create: `hermes_trading/persistence/__init__.py`
- Create: `hermes_trading/persistence/migrations.py`
- Create: `hermes_trading/persistence/repository.py`
- Test: `tests/test_persistence.py`

**Interfaces:**
- Consumes: domain dataclasses from Task 1.
- Produces: `StateRepository.open(path)`, `migrate()`, `integrity_check()`,
  `record_decision()`, `create_order_intent()`, `upsert_order()`,
  `record_fill()`, `save_position()`, `open_position(asset)`,
  `mark_candle_processed()`, `is_candle_processed()`.

- [ ] **Step 1: Write failing WAL, migration and idempotence tests**

```python
class PersistenceTests(unittest.TestCase):
    def test_repository_enables_wal_and_migrates_once(self):
        repo = StateRepository.open(self.db_path)
        repo.migrate()
        self.assertEqual(repo.journal_mode(), "wal")
        self.assertEqual(repo.schema_version(), 1)
        repo.migrate()
        self.assertEqual(repo.schema_version(), 1)

    def test_order_intent_client_id_is_unique(self):
        repo = StateRepository.open(self.db_path)
        repo.migrate()
        intent = sample_intent(client_order_id="ht-btc-20260730")
        repo.create_order_intent(intent)
        with self.assertRaises(DuplicateIntentError):
            repo.create_order_intent(intent)

    def test_only_one_active_position_per_asset(self):
        repo = StateRepository.open(self.db_path)
        repo.migrate()
        repo.save_position(sample_position("one", "BTC/USDT"))
        with self.assertRaises(ActivePositionExists):
            repo.save_position(sample_position("two", "BTC/USDT"))
```

- [ ] **Step 2: Run persistence tests and verify they fail**

Run: `uv run python -m unittest tests.test_persistence -v`

Expected: repository imports are missing.

- [ ] **Step 3: Implement migration version 1**

The SQL in `migrations.py` must create the 16 tables named in the design. The
first plan actively uses `schema_migrations`, `candles_processed`, `decisions`,
`order_intents`, `orders`, `order_fills`, `positions`, `risk_state` and
`incidents`; the remaining tables are created empty for later plans.

```sql
CREATE UNIQUE INDEX one_active_position_per_asset
ON positions(asset)
WHERE status IN ('opening', 'open', 'closing');

CREATE UNIQUE INDEX unique_client_order_id
ON order_intents(client_order_id);

CREATE UNIQUE INDEX unique_processed_candle
ON candles_processed(asset, timeframe, close_timestamp);
```

`StateRepository.open()` must execute:

```python
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA foreign_keys=ON")
connection.execute("PRAGMA busy_timeout=5000")
```

Every public write method uses `with self._connection:` and maps integrity
errors to named domain exceptions.

- [ ] **Step 4: Run persistence and integrity tests**

Run: `uv run python -m unittest tests.test_persistence -v`

Expected: WAL, migration, uniqueness and integrity tests pass.

- [ ] **Step 5: Commit persistence**

```bash
git add hermes_trading/persistence tests/test_persistence.py
git commit -m "feat: add durable sqlite trading state"
```

### Task 3: Project Human-Readable Audit Files

**Files:**
- Create: `hermes_trading/persistence/artifacts.py`
- Create: `scripts/inspect_state.py`
- Modify: `hermes_trading/persistence/repository.py`
- Modify: `hermes_trading/run.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: `StateRepository`, `json_value()`.
- Produces: `ArtifactProjector.append_event(name, payload)`,
  `write_snapshot(name, payload)`, `rebuild_all(repo)`, CLI commands
  `integrity`, `export`, `compare`, `rebuild`, plus read-only repository
  exports needed by those commands.

The `export` command includes all 16 canonical tables, not only open positions
and orders.

- [ ] **Step 1: Write failing atomic-write and rebuild tests**

```python
class ArtifactTests(unittest.TestCase):
    def test_snapshot_is_valid_json_after_replacement(self):
        projector = ArtifactProjector(self.state_dir)
        projector.write_snapshot("positions.json", {"positions": []})
        self.assertEqual(json.loads((self.state_dir / "positions.json").read_text()), {"positions": []})
        self.assertFalse((self.state_dir / "positions.json.tmp").exists())

    def test_rebuild_exports_open_positions_without_sqlite_reader(self):
        repo = seeded_repository(self.state_dir / "trading.db")
        ArtifactProjector(self.state_dir).rebuild_all(repo)
        payload = json.loads((self.state_dir / "positions.json").read_text())
        self.assertEqual(payload["positions"][0]["asset"], "BTC/USDT")
```

- [ ] **Step 2: Run artifact tests and verify they fail**

Run: `uv run python -m unittest tests.test_artifacts -v`

Expected: `ArtifactProjector` is missing.

- [ ] **Step 3: Implement append-only journals and atomic snapshots**

Use same-directory temporary files, `flush()`, `os.fsync()` and `os.replace()`:

```python
with temp_path.open("w", encoding="utf-8") as handle:
    json.dump(json_value(payload), handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temp_path, target_path)
```

`rebuild_all()` must produce `positions.json`, `open_orders.json`,
`system_status.json` and preserve the existing JSONL files. `inspect_state.py`
must be read-only unless `rebuild` is explicitly selected.

- [ ] **Step 4: Extend bootstrap and verify inspection commands**

Modify `ensure_state_files()` to create/migrate `trading.db`, then rebuild
missing snapshots. Run:

`uv run python scripts/inspect_state.py --state-dir state integrity`

Expected: `integrity=ok schema_version=1`.

- [ ] **Step 5: Run focused and bootstrap tests**

Run: `uv run python -m unittest tests.test_artifacts tests.test_execution.StateBootstrapExecutionTests -v`

Expected: all tests pass and legacy JSONL files still exist.

- [ ] **Step 6: Commit human-readable artifacts**

```bash
git add hermes_trading/persistence/artifacts.py hermes_trading/persistence/repository.py hermes_trading/run.py scripts/inspect_state.py tests/test_artifacts.py
git commit -m "feat: project durable state to readable artifacts"
```

### Task 4: Deduplicate Closed Candles And Share Market Context

**Files:**
- Create: `hermes_trading/market_data.py`
- Modify: `hermes_trading/adapters/price.py`
- Test: `tests/test_market_data.py`

**Interfaces:**
- Consumes: adapter functions and `StateRepository`.
- Produces: `CandleIdentity`, `MarketSnapshot`,
  `MarketDataCoordinator.collect(assets)`,
  `latest_closed_candle(price_payload, timeframe)`.

- [ ] **Step 1: Write failing closed-candle and shared-context tests**

```python
class MarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_incomplete_candle_is_excluded(self):
        payload = price_payload(candles=[closed_candle(), current_open_candle()])
        candle = latest_closed_candle(payload, "1h", now=FIXED_NOW)
        self.assertEqual(candle.close_timestamp, CLOSED_AT)

    async def test_shared_adapters_run_once_for_four_assets(self):
        coordinator = MarketDataCoordinator(
            price_fetch=AsyncMock(side_effect=prices_for_four_assets()),
            news_fetch=AsyncMock(return_value={"schema_version": 1}),
            macro_fetch=AsyncMock(return_value={"schema_version": 1}),
            onchain_fetch=AsyncMock(return_value={"schema_version": 1}),
            concurrency=2,
        )
        await coordinator.collect(["BTC/USDT", "ETH/USDT", "XRP/USDT", "SHIB/USDT"])
        coordinator.news_fetch.assert_awaited_once()
        coordinator.macro_fetch.assert_awaited_once()
        coordinator.onchain_fetch.assert_awaited_once()

    async def test_stale_closed_candle_produces_no_trade(self):
        snapshot = await coordinator_with(stale_closed_candle()).collect(["BTC/USDT"])
        self.assertEqual(snapshot["BTC/USDT"].availability, "stale")
        self.assertEqual(snapshot["BTC/USDT"].decision_action, "no_trade")
```

- [ ] **Step 2: Run market tests and verify they fail**

Run: `uv run python -m unittest tests.test_market_data -v`

Expected: coordinator imports are missing.

- [ ] **Step 3: Include raw OHLCV timestamps in the price adapter**

Return a `candles` array containing timestamp, open, high, low, close and volume.
Keep `closes` during the migration so existing RSI tests remain compatible.

- [ ] **Step 4: Implement coordinator and deduplication contract**

`collect()` starts the three shared adapter calls once and price calls under an
`asyncio.Semaphore`. It returns snapshots keyed by asset. A candle is eligible
only when `close_timestamp <= now`. A stale/invalid candle generates a
persisted `no_trade`; repeated failures cross a configurable threshold before
one warning is emitted, avoiding notification noise.

- [ ] **Step 5: Run market and existing adapter tests**

Run: `uv run python -m unittest tests.test_market_data tests.test_score_reflect.NewsAdapterTests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit the market coordinator**

```bash
git add hermes_trading/market_data.py hermes_trading/adapters/price.py tests/test_market_data.py
git commit -m "feat: evaluate each closed candle once"
```

### Task 5: Implement Realistic Simulated Positions

**Files:**
- Create: `hermes_trading/strategy.py`
- Create: `hermes_trading/positions.py`
- Modify: `hermes_trading/loop.py`
- Test: `tests/test_positions.py`
- Test: `tests/test_training_loop.py`

**Interfaces:**
- Consumes: `MarketSnapshot`, domain types, repository and artifacts.
- Produces: `StrategySignal`, `evaluate_strategy()`,
  `PositionEngine.process_candle()`, `CycleResult`,
  `run_cycle()` extracted from `run_loop()`.

- [ ] **Step 1: Write failing entry-hold-exit lifecycle tests**

```python
class PositionEngineTests(unittest.TestCase):
    def test_entry_does_not_close_on_same_candle(self):
        result = self.engine.process_candle(ENTRY_CANDLE, entry_strategy())
        self.assertEqual(result.position.status, PositionStatus.OPEN)
        self.assertEqual(result.closed_positions, ())

    def test_stop_exit_uses_low_and_charges_fees(self):
        self.engine.process_candle(ENTRY_CANDLE, entry_strategy())
        result = self.engine.process_candle(STOP_HIT_CANDLE, entry_strategy())
        closed = result.closed_positions[0]
        self.assertEqual(closed.status, PositionStatus.CLOSED)
        self.assertLess(closed.realized_pnl, Decimal("0"))
        self.assertGreater(closed.fees, Decimal("0"))

    def test_same_candle_is_not_processed_twice(self):
        first = self.engine.process_candle(ENTRY_CANDLE, entry_strategy())
        second = self.engine.process_candle(ENTRY_CANDLE, entry_strategy())
        self.assertTrue(first.processed)
        self.assertFalse(second.processed)
        self.assertEqual(second.reason, "duplicate_candle")
```

- [ ] **Step 2: Run lifecycle tests and verify they fail**

Run: `uv run python -m unittest tests.test_positions -v`

Expected: strategy and position engine imports are missing.

- [ ] **Step 3: Implement pure signal evaluation**

```python
@dataclass(frozen=True)
class StrategySignal:
    action: Literal["enter", "exit", "hold", "no_trade"]
    asset: str
    reason: str
    reference_price: Decimal
    candle_id: str


def evaluate_strategy(snapshot: MarketSnapshot, strategy: dict, position: Position | None) -> StrategySignal:
    ...
```

The function may read strategy values but cannot write state. Entry remains RSI
long-only. Exit supports stop hit first, then an explicit strategy exit; if no
exit rule exists, hold until stop for this plan.

- [ ] **Step 4: Implement transactional position processing**

`process_candle()` starts by checking `candles_processed`. It creates an open
position on entry and closes only on a later candle. Training fills use
configurable maker/taker fee and slippage assumptions, both stored as Decimal.
Every commit emits the corresponding JSONL event and snapshots.

- [ ] **Step 5: Extract and test one finite worker cycle**

Create:

```python
async def run_cycle(
    assets: list[str],
    state: StateRepository,
    projector: ArtifactProjector,
    market_data: MarketDataCoordinator,
    strategy: dict[str, Any],
    mode: str,
) -> CycleResult:
    ...
```

`run_loop()` becomes the sleep/retry shell around `run_cycle()`. Test two
consecutive calls with the same snapshots and assert the second creates no
trade or order.

- [ ] **Step 6: Run focused and complete test suites**

Run: `uv run python -m unittest tests.test_positions tests.test_training_loop -v`

Expected: lifecycle and deduplication pass.

Run: `uv run python -m unittest discover -s tests`

Expected: all legacy and new tests pass.

- [ ] **Step 7: Regenerate graph and commit**

Run: `graphify update .`

```bash
git add hermes_trading/strategy.py hermes_trading/positions.py hermes_trading/loop.py tests/test_positions.py tests/test_training_loop.py graphify-out
git commit -m "feat: model complete simulated positions"
```
