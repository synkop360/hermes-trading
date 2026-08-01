# Qualification Evidence Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the remaining qualification gates into explicit, machine-readable evidence gathered from Hermes state without enabling real orders or automatic promotion.

**Architecture:** Keep `QualificationEvidenceCollector` as the single source that translates durable state into `EvidenceCheck` objects. Add small focused readers for cost ledger evidence and replay reports, then expose a safe operator command that reports current evidence status. Missing or malformed evidence must remain visible as `missing` or `fail`.

**Tech Stack:** Python 3.11, `unittest`, SQLite/WAL through `StateRepository`, JSON/JSONL state artifacts, `uv`, Railway training-only deployment.

## Global Constraints

- Repository path: `E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading`.
- Branch: `feat/live-hardening`.
- Railway project: `awake-abundance`, service `hermes-trading`, production environment.
- Railway must remain `HERMES_TRADING_MODE=training`.
- Railway must remain `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- Never enable real Kraken orders or promote a phase automatically.
- Do not expose the Control API publicly.
- Do not record Discord token values in docs or logs.
- Do not delete or revert untracked `.test-tmp-*` folders or `graphify-out/`.
- Use `apply_patch` for edits.
- Use TDD: write failing tests before production code.
- Run `graphify update .` after code changes.

---

## File Structure

- Modify: `hermes_trading/qualification_evidence.py`
  - Responsible for converting state artifacts and repository evidence into `EvidenceCheck` values.
- Create: `scripts/qualification_evidence_status.py`
  - Responsible for printing a safe operator summary of current evidence gates.
- Modify: `tests/test_qualification_evidence.py`
  - Regression tests for evidence collection from JSON, JSONL, and repository data.
- Modify: `tests/test_operational_scripts.py`
  - Smoke test for the new operator script.
- Modify: `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`
  - Operator instructions for evidence files and the status command.

---

### Task 1: Collect Fee And Slippage Evidence From Cost Ledger

**Files:**
- Modify: `hermes_trading/qualification_evidence.py`
- Modify: `tests/test_qualification_evidence.py`

**Interfaces:**
- Consumes: `state/cost_events.jsonl`, with events shaped like `CostLedger` writes.
- Produces: `QualificationEvidenceCollector.collect()["fees_and_slippage_charged"] -> EvidenceCheck`.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_qualification_evidence.py`:

```python
def test_cost_ledger_marks_fees_and_slippage_charged(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        (state / "cost_events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({
                        "category": "kraken_fee",
                        "amount": "0.01",
                        "currency": "USD",
                        "quality": "actual",
                        "source": "kraken",
                        "occurred_at": "2026-08-01T12:00:00+00:00",
                        "period_start": None,
                        "period_end": None,
                        "metadata": {"order_id": "order-1"},
                    }),
                    json.dumps({
                        "category": "slippage",
                        "amount": "0.10",
                        "currency": "USD",
                        "quality": "actual",
                        "source": "kraken_fill_vs_decision",
                        "occurred_at": "2026-08-01T12:00:01+00:00",
                        "period_start": None,
                        "period_end": None,
                        "metadata": {"order_id": "order-1"},
                    }),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        checks = QualificationEvidenceCollector(state).collect()

    self.assertEqual(checks["fees_and_slippage_charged"].status.value, "pass")
    self.assertEqual(checks["fees_and_slippage_charged"].observed, "kraken_fee=0.01 slippage=0.10")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_qualification_evidence.QualificationEvidenceCollectorTests.test_cost_ledger_marks_fees_and_slippage_charged
```

Expected: FAIL because `fees_and_slippage_charged` still reports `missing`.

- [ ] **Step 3: Implement minimal cost ledger collection**

In `hermes_trading/qualification_evidence.py`, add helpers that read `cost_events.jsonl`, total positive `actual` USD amounts for `kraken_fee` and `slippage`, and return:

```python
EvidenceCheck.passed(
    f"kraken_fee={fee_total} slippage={slippage_total}",
    "kraken_fee > 0 and slippage >= 0",
    "fee and slippage costs are recorded in the cost ledger",
)
```

If the file is missing, malformed, has no actual fee, or has no actual slippage entry, leave the existing `missing` value unchanged.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_qualification_evidence
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add hermes_trading/qualification_evidence.py tests/test_qualification_evidence.py
git commit -m "feat: collect fee and slippage qualification evidence"
```

---

### Task 2: Collect Replay Without Lookahead Evidence From Replay Report

**Files:**
- Modify: `hermes_trading/qualification_evidence.py`
- Modify: `tests/test_qualification_evidence.py`

**Interfaces:**
- Consumes: `state/qualification-replay.json`.
- Produces: `QualificationEvidenceCollector.collect()["replay_without_lookahead"] -> EvidenceCheck`.

- [ ] **Step 1: Write the failing pass test**

Add:

```python
def test_replay_report_marks_no_lookahead_passed(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        (state / "qualification-replay.json").write_text(
            json.dumps({
                "run_id": "replay-2026-08-01",
                "status": "pass",
                "candles_checked": 120,
                "lookahead_violations": 0,
            }),
            encoding="utf-8",
        )

        checks = QualificationEvidenceCollector(state).collect()

    self.assertEqual(checks["replay_without_lookahead"].status.value, "pass")
    self.assertEqual(checks["replay_without_lookahead"].observed, "replay-2026-08-01 candles=120 violations=0")
```

- [ ] **Step 2: Write the failing violation test**

Add:

```python
def test_replay_report_marks_lookahead_violations_failed(self) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        (state / "qualification-replay.json").write_text(
            json.dumps({
                "run_id": "replay-2026-08-01",
                "status": "pass",
                "candles_checked": 120,
                "lookahead_violations": 2,
            }),
            encoding="utf-8",
        )

        checks = QualificationEvidenceCollector(state).collect()

    self.assertEqual(checks["replay_without_lookahead"].status.value, "fail")
    self.assertEqual(checks["replay_without_lookahead"].reason, "replay detected lookahead violations")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:

```powershell
uv run python -m unittest tests.test_qualification_evidence.QualificationEvidenceCollectorTests.test_replay_report_marks_no_lookahead_passed tests.test_qualification_evidence.QualificationEvidenceCollectorTests.test_replay_report_marks_lookahead_violations_failed
```

Expected: FAIL because `qualification-replay.json` is not yet read.

- [ ] **Step 4: Implement replay report parsing**

Read `qualification-replay.json` only when `qualification-evidence.json` has no explicit `replay_without_lookahead` value. Pass only when `status == "pass"`, `candles_checked > 0`, and `lookahead_violations == 0`. Fail when violations are positive. Otherwise leave the gate `missing`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_qualification_evidence tests.test_qualification_runner
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add hermes_trading/qualification_evidence.py tests/test_qualification_evidence.py
git commit -m "feat: collect replay qualification evidence"
```

---

### Task 3: Add Safe Operator Evidence Status Command

**Files:**
- Create: `scripts/qualification_evidence_status.py`
- Modify: `tests/test_operational_scripts.py`
- Modify: `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`

**Interfaces:**
- Consumes: `QualificationEvidenceCollector(Path(args.state_dir), repository=StateRepository.open(...))`.
- Produces CLI output:
  - `replay_without_lookahead=<status>`
  - `fees_and_slippage_charged=<status>`
  - `duplicate_candle_protected=<status>`
  - `risk_limits_exercised=<status>`

- [ ] **Step 1: Write the failing script smoke test**

Add to `tests/test_operational_scripts.py`:

```python
def test_qualification_evidence_status_script_imports_project(self) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "qualification_evidence_status.py"), "--state-dir", str(ROOT / ".test-tmp-qualification-evidence-status")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("replay_without_lookahead=", result.stdout)
    self.assertIn("fees_and_slippage_charged=", result.stdout)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_operational_scripts.OperationalScriptTests.test_qualification_evidence_status_script_imports_project
```

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement the script**

Create `scripts/qualification_evidence_status.py` with direct project import bootstrapping, read-only repository open when the database exists, and fallback collection without repository when it does not.

- [ ] **Step 4: Document the command**

In `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`, add:

```powershell
uv run python scripts/qualification_evidence_status.py --state-dir state
```

Do not include secrets or token values.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
uv run python -m unittest tests.test_operational_scripts tests.test_qualification_evidence
```

Expected: PASS.

Commit:

```powershell
git add scripts/qualification_evidence_status.py tests/test_operational_scripts.py docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md
git commit -m "feat: add qualification evidence status command"
```

---

## Final Verification Checklist

- [ ] `uv run python -m unittest discover -s tests` passes.
- [ ] `git diff --check` reports no whitespace errors; LF/CRLF warnings on Windows are acceptable.
- [ ] `graphify update .` completes after code changes.
- [ ] `railway.cmd ssh "printenv HERMES_TRADING_MODE"` returns `training` before any later deploy.
- [ ] `railway.cmd ssh "printenv HERMES_TRADING_ALLOW_REAL_ORDERS"` returns `false` before any later deploy.
- [ ] `scripts/control_command.py qualification` still returns `promoted=false` in deployed smoke tests after any later deploy.

## Self-Review

Spec coverage:

- Replay evidence is covered by Task 2 through `qualification-replay.json`.
- Fee/slippage evidence is covered by Task 1 through `cost_events.jsonl`.
- Existing durable duplicate-candle and risk-limit evidence remains in `StateRepository`.
- Operator visibility is covered by Task 3.
- No task enables real orders or automatic promotion.

Placeholder scan:

- No step uses TBD/TODO/fill-in placeholders.
- Each task contains exact files, interfaces, test snippets, commands, and commit messages.

Type consistency:

- `QualificationEvidenceCollector.collect()` remains the public collector interface.
- `EvidenceCheck` and `GateStatus` names match existing production code.
- CLI output uses evidence keys from `EVIDENCE_KEYS`.
