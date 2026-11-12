# Final Qualification Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the three remaining qualification evidence gates from Hermes state without enabling real orders or promotion.

**Architecture:** Add one safe operator script that writes only evidence artifacts and SQLite incidents/risk decisions. Cost evidence is derived from filled canonical orders plus order intents. Duplicate-candle evidence uses the existing processed-candle guard. Risk evidence uses `RiskEngine.authorize_entry()` to create a controlled denied decision.

**Tech Stack:** Python 3.11, `unittest`, SQLite/WAL via `StateRepository`, JSONL state artifacts, `CostLedger`, `RiskEngine`, `uv`, Railway training-only deployment.

## Global Constraints

- Repository path: `E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading`.
- Branch: `feat/live-hardening`.
- Railway service: `hermes-trading`, production environment.
- Railway state directory: `/app/state`.
- Keep `HERMES_TRADING_MODE=training`.
- Keep `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- Never enable real Kraken orders.
- Never auto-promote a qualification phase.
- Do not print or store Discord token values.
- Use TDD for code changes.
- Use `apply_patch` for edits.

---

## File Structure

- Create: `scripts/generate_final_qualification_evidence.py`
  - Generates fee/slippage, duplicate-candle, and risk-limit evidence.
- Modify: `tests/test_operational_scripts.py`
  - Adds script import and behavior coverage.
- Modify: `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`
  - Documents the command.
- Modify: `docs/SESSION_HANDOFF_2026-08-01.md`
  - Records deployment and remote verification after execution.

---

### Task 1: Generate Fee And Slippage Evidence

**Files:**
- Create: `scripts/generate_final_qualification_evidence.py`
- Modify: `tests/test_operational_scripts.py`

**Interfaces:**
- Consumes: `StateRepository.export_canonical_tables()`.
- Produces: `state/cost_events.jsonl` entries with categories `kraken_fee` and `slippage`.

- [ ] **Step 1: Write failing script test**

Add a test that creates a migrated SQLite state with one decision, intent, filled order, fill, and closed position, then runs:

```powershell
uv run python scripts/generate_final_qualification_evidence.py --state-dir <tmp>
```

Expected output includes `fees_and_slippage=generated`.

- [ ] **Step 2: Implement minimal generation**

Open `StateRepository`, find filled orders with fees and matching intents, then record `CostEvent("kraken_fee", ...)` and `CostEvent("slippage", ...)`.

- [ ] **Step 3: Verify**

Run:

```powershell
uv run python -m unittest tests.test_operational_scripts.OperationalScriptTests.test_generate_final_qualification_evidence_marks_remaining_gates
```

Expected: PASS.

---

### Task 2: Generate Duplicate-Candle And Risk Evidence

**Files:**
- Modify: `scripts/generate_final_qualification_evidence.py`
- Modify: `tests/test_operational_scripts.py`

**Interfaces:**
- Consumes: `StateRepository.mark_candle_processed()` and `StateRepository.is_candle_processed()`.
- Consumes: `RiskEngine.authorize_entry()`.
- Produces: incidents matching `qualification:duplicate-candle:*` and `qualification:risk-limit:*`.

- [ ] **Step 1: Extend failing script test**

The same script test must assert:

```python
self.assertIn("duplicate_candle=generated", result.stdout)
self.assertIn("risk_limit=generated", result.stdout)
```

- [ ] **Step 2: Implement duplicate-candle exercise**

Mark a deterministic candle as processed, verify the second read is already processed, and open incident `qualification:duplicate-candle:<asset>:<timestamp>`.

- [ ] **Step 3: Implement risk exercise**

Create a `RiskContext` that breaches daily loss, call `RiskEngine.authorize_entry()`, assert the decision is denied, and open incident `qualification:risk-limit:<decision_id>`.

- [ ] **Step 4: Verify collector status**

Run:

```powershell
uv run python scripts/qualification_evidence_status.py --state-dir <tmp>
```

Expected all three remaining gates pass when replay evidence already exists.

---

### Task 3: Deploy And Verify

**Files:**
- Modify: `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`
- Modify: `docs/SESSION_HANDOFF_2026-08-01.md`

**Interfaces:**
- Consumes: Railway CLI.
- Produces: deployed script and remote evidence in `/app/state`.

- [ ] **Step 1: Full local verification**

Run:

```powershell
uv run python -m unittest discover -s tests
git diff --check
graphify update .
```

- [ ] **Step 2: Deploy**

Run:

```powershell
railway.cmd up --service hermes-trading --environment production --message "Deploy final qualification evidence generation"
```

- [ ] **Step 3: Post-deploy safety**

Run:

```powershell
railway.cmd ssh "printenv HERMES_TRADING_MODE"
railway.cmd ssh "printenv HERMES_TRADING_ALLOW_REAL_ORDERS"
```

Expected: `training` and `false`.

- [ ] **Step 4: Generate remote evidence and verify**

Run:

```powershell
railway.cmd ssh "cd /app && uv run python scripts/generate_final_qualification_evidence.py --state-dir /app/state"
railway.cmd ssh "cd /app && uv run python scripts/qualification_evidence_status.py --state-dir /app/state"
railway.cmd ssh "cd /app && uv run python scripts/control_command.py qualification"
```

Expected: the three remaining gates become `pass`; qualification remains `promoted=false`.

## Self-Review

- Spec coverage: covers fee/slippage, duplicate-candle, and risk-limit evidence.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: uses existing `StateRepository`, `CostEvent`, `CostLedger`, and `RiskEngine` APIs.
