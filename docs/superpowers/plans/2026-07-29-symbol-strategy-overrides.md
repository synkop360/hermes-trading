# Symbol Strategy Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Hermes Trading to keep one global strategy while overriding selected strategy fields per traded symbol.

**Architecture:** Add a resolver that deep-merges `strategy.yaml` defaults with `symbols.<asset>` overrides. The worker uses the resolved strategy for each asset, and reflection can update exactly one global or symbol-specific variable while preserving backward compatibility.

**Tech Stack:** Python 3.11, PyYAML, unittest, Railway volume state files, Graphify documentation output.

## Global Constraints

- Keep existing flat `strategy.yaml` files valid.
- Support per-symbol overrides under `symbols`.
- Change one strategy variable at a time during reflection.
- Keep training simulated unless live mode is explicitly configured elsewhere.
- Do not expose API keys or secrets in logs, docs, or hypotheses.

---

### Task 1: Resolve Strategy Per Asset

**Files:**
- Modify: `hermes_trading/loop.py`
- Test: `tests/test_score_reflect.py`

**Interfaces:**
- Consumes: `strategy: dict[str, Any]`, `asset: str`
- Produces: `resolve_asset_strategy(strategy: dict[str, Any], asset: str) -> dict[str, Any]`

- [ ] **Step 1: Write failing tests**

Add tests showing:

```python
resolved = resolve_asset_strategy(strategy, "SHIB/USDT")
self.assertEqual(resolved["entry"]["threshold"], 22)
self.assertEqual(resolved["stop_loss_pct"], 0.8)
self.assertEqual(resolved["position_size_r"], 1.0)
self.assertNotIn("symbols", resolved)
```

and that `_paper_trade("SHIB/USDT", market, strategy)` uses the SHIB override threshold.

- [ ] **Step 2: Verify failure**

Run:

```powershell
uv run python -m unittest tests.test_score_reflect.MultiAssetDecisionTests -v
```

Expected: import or assertion failure because `resolve_asset_strategy` does not exist or overrides are ignored.

- [ ] **Step 3: Implement resolver**

Add a small recursive merge helper and `resolve_asset_strategy`. Use it inside `_paper_trade` before reading `entry`, `stop_loss_pct`, and `position_size_r`.

- [ ] **Step 4: Verify pass**

Run:

```powershell
uv run python -m unittest tests.test_score_reflect.MultiAssetDecisionTests -v
```

Expected: all tests pass.

---

### Task 2: Allow Reflection To Update Symbol Overrides

**Files:**
- Modify: `hermes_trading/reflect.py`
- Test: `tests/test_score_reflect.py`

**Interfaces:**
- Consumes proposal JSON with `variable`, `value`, optional `asset`
- Produces updated global variable when no asset is supplied, or updated `symbols.<asset>` override when `asset` is supplied.

- [ ] **Step 1: Write failing test**

Mock the LLM proposal:

```json
{"asset":"SHIB/USDT","variable":"entry.threshold","value":22,"reason":"symbol-specific volatility"}
```

Assert `strategy.yaml` contains `symbols.SHIB/USDT.entry.threshold: 22`, global threshold remains unchanged, and hypothesis records the asset.

- [ ] **Step 2: Verify failure**

Run:

```powershell
uv run python -m unittest tests.test_score_reflect.ReflectionTests -v
```

Expected: assertion failure because asset-specific proposals are not applied.

- [ ] **Step 3: Implement asset-aware proposal application**

Add a helper that selects either the root strategy or `symbols[asset]`, applies exactly one supported variable, and appends `asset` to the hypothesis when present.

- [ ] **Step 4: Verify pass**

Run:

```powershell
uv run python -m unittest tests.test_score_reflect.ReflectionTests -v
```

Expected: all reflection tests pass.

---

### Task 3: Seed Current Strategy With Conservative Symbol Overrides

**Files:**
- Modify: `state/strategy.yaml`
- Modify: `docs/STATE_FILES.md`
- Modify: `docs/HERMES.md`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Produces current v56 strategy with global defaults and per-symbol overrides:
  - BTC/USDT threshold 28, position_size_r 1.0
  - ETH/USDT threshold 27, position_size_r 1.0
  - XRP/USDT threshold 24, position_size_r 0.7
  - SHIB/USDT threshold 22, stop_loss_pct 0.8, position_size_r 0.4

- [ ] **Step 1: Update strategy.yaml**

Archive current strategy if needed, then add the `symbols` block and increment version.

- [ ] **Step 2: Update docs**

Document the `symbols` override shape and note that omitted fields inherit from the global strategy.

- [ ] **Step 3: Regenerate documentation graph**

Run:

```powershell
uv run python scripts\build_docs_graph.py
graphify update .
```

---

### Task 4: Full Verification

**Files:**
- No code edits expected.

**Interfaces:**
- Confirms code, docs, graph, and state are coherent.

- [ ] **Step 1: Run full test suite**

```powershell
uv run python -m unittest discover -s tests
```

- [ ] **Step 2: Verify remote sync path remains ready**

Run the cron manually after the code and strategy are ready:

```powershell
hermes cron run 3db46486d61c
```

- [ ] **Step 3: Check deployed state**

Use Railway SSH to confirm `/app/state/strategy.yaml` uses the new version after sync.

## Self-Review

- Spec coverage: global strategy plus symbol overrides, worker resolution, reflection updates, docs, and verification are covered.
- Placeholder scan: no TODO/TBD placeholders remain.
- Type consistency: `resolve_asset_strategy(strategy, asset)` is the resolver used by tests and worker code.
