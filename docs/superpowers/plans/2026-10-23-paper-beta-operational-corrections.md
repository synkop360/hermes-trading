# Paper beta operational corrections implementation plan

> **For agentic workers:** Use test-driven development, an isolated checkout and independent review. Track task receipts in `research/runs/paper-operational-corrections-v1/progress.md`.

**Goal:** Correct daily backup handling of `lost+found` and prove a checked release-binding transition on synthetic copies without restarting the real experiment.

**Architecture:** Keep the runtime change to literal plus acceptance in snapshot path components. A separate, disabled-by-default operator script prepares and applies an exact, audited transition to closed copies; native consumers verify the resulting bindings. Preserve all historical evidence and require separate approval before any live transition or successor deployment.

**Tech stack:** Existing Python/SQLite/unittest and native paper APIs; no new dependency, provider request, inference, backtest or training.

**Spec:** `docs/superpowers/specs/2026-10-23-paper-beta-operational-corrections.md`, approved October 23: “Approve design and disabled implementation.”

## Global constraints

- Base `6c9d520da5f8473b51c912d1eb0e269ee94d4b1b`; exclude `3e785e13d53923e067009c463e6e9dfa3fec4ff0`.
- Preserve experiment `paper-beta-v1-20261021-6c9d520`, original account T0/opening, raw manifest, eight accounts, policy, strategies, risk limits, fees, costs, journals, counters and deadlines.
- Only snapshot component-name acceptance changes in the runtime. Keep all other guards and archive/checkpoint enablement unchanged.
- Operator CLI defaults to inspection; explicit apply and an exact plan/approval reference are required for writes. It never deploys, invokes a provider, enables a scheduler, initializes an experiment or edits economics.
- Local implementation and synthetic copies only under this approval. Capture exact candidate commit and transition/rollback evidence for later live approval.

## Review focus

- A plus-containing directory may contain evidence: include every original regular file and verify bytes; never silently exclude it (Task 1).
- An importer can run outside the worker cycle: acquire actual SQLite writer leases and reject active owners/importers (Task 2).
- An interruption may commit a component before its receipt: reconcile exact before/after states under a durable intent; never reset history (Task 2).
- New post-transition work makes an old rollback destructive: reject rollback on any non-binding state change, including new renewal/usage rows (Task 2).
- PC's initial model-import ledger is historical: leave it untouched; the actual watcher probes locally and installs on the worker (Tasks 2–3).

### Task 1: Portable plus-containing evidence and daily copy regression

**Files:** Modify `hermes_trading/archive_snapshot.py`; add `tests/test_snapshot_plus_names.py`.

**Interface:** Existing `snapshot`, `create_daily_backup` and `scripts.pull_paper_backup.pull_completed` retain signatures and bounds. Accept literal `+` in components without normalization.

- [x] Run existing snapshot/daily-backup/pull focused baseline using the existing main interpreter from this checkout; record actual terminal results.
- [x] Add tests for empty/populated `lost+found`, nested plus names and exact original bytes through native daily creation and synthetic pull. Observe the plus cases fail before editing runtime source.
- [x] Change only the character class to admit `+`. Keep links/nonregular/trailing-dot/collision/reserved-name/sidecar/source-change/budget guards intact; retain or extend meaningful negative coverage.
- [x] Run new tests and affected native suites with ResourceWarnings treated as errors; preserve platform skips. Commit only this task's files.

### Task 2: Exact, resumable binding transition on closed copies

**Files:** Add `scripts/paper_release_transition.py`, `tests/test_paper_release_transition.py`, and synthetic fixture support if needed.

**Interfaces:** `prepare_plan(worker_root, supervisor_root, launch_file, *, release_plan, expected_identity)` returns a serializable immutable transition plan. `inspect_transition(plan, *, worker_root, supervisor_root, launch_file, audit_dir)` reads without mutation. `apply_transition(..., approval_ref, apply=False, fault=None)` requires explicit apply, freezes native writer/import leases and records intent/rollback/receipts. `rollback_transition(...)` restores only the checked binding changes before any new work. Roots are explicit local closed copies; transport/deployment is outside the script.

- [x] Write failing tests for default read-only inspection, exact old/new release delta, identity checks and byte/logical preservation of all non-binding state.
- [x] Bind the plan to source commits/runtime maps, deployment IDs, original opening/raw manifest, all eight account hashes and policy. Restrict runtime delta to `archive_snapshot.py`; preserve watcher/native launcher hashes and all economics.
- [x] Prepare six exact components: worker/PC supervisor limits; PC launch JSON; PC lifecycle binding; worker renewal binding; worker usage binding. Preserve original JSON fields/SQLite historical rows. Leave PC historical model ledger and local review/usage audit untouched.
- [x] Refuse active worker/cycle/review/import leases, pending common/publication work, pending reviews/publications/health deliveries and unresolved inference. Never clear pending records or latches.
- [x] Under native leases, capture a consistent rollback set and durable intent outside the inputs. Atomically/transactionally update each component with exact before/after fingerprints and one transition ID. Completed identical retry returns original receipt; mixed states require explicit reconciliation from intent and verified component state.
- [x] Exercise interruption at every component boundary, including commit-before-receipt, identical/conflicting retries, tampered inputs, unknown components and active importers. Checked rollback preserves history; any newly created financial/usage/renewal row or changed non-binding file forbids old-state restoration.
- [x] Run focused transition, renewal, usage, lifecycle and launch suites. Commit the operator script and tests with no live action.

### Task 3: Synthetic successor composition and reviewable release

**Files:** Add `tests/test_paper_release_transition_integration.py`; record evidence under `research/runs/paper-operational-corrections-v1/`; add `docs/PAPER_OPERATIONAL_CORRECTION_CUTOVER.md` with disabled exact-plan/rollback procedure.

**Consumes:** Task 1 unchanged native APIs and Task 2 plan/inspection/apply/rollback interfaces.

- [x] Build native eight-account synthetic state with an authoritative opening, common batch, existing cost/renewal/usage history, PC supervisor lifecycle and review audit. Never copy live state or call a provider/model.
- [x] Apply old-to-new bindings; resume native paper boot under the successor release, preserving opening/T0/deadline/counters and creating a later common batch. Renew model access and import usage with synthetic metadata under the new binding; validate native supervisor snapshot/launch/admission. Preserve actual historical rows and all eight reports.
- [x] Demonstrate native daily publication and synthetic off-host pull with populated `lost+found`; verify original bytes. Preserve safety latches; do not forge healthy receipts or extend proof timestamps.
- [x] Run appropriate full correctness discovery on this candidate, preserving known fixture/platform limitations and exact terminals. Use cached local Linux only if needed for meaningful POSIX guards; no downloads or network.
- [x] Run AST-only graph update, independently review the complete diff and fix demonstrated issues. Commit the concrete local candidate; publish exact hashes, test scope, transition/rollback receipts and remaining live acceptance steps.
- [x] Ask separately before any deployment or live binding change. Keep the active worker's original clocks and entry blocks untouched meanwhile.

## Completion record — local scope only

Code candidate 49c851315b98bb33b1eb78180fb789afe2a49f3d implements all three tasks. Final full correctness discovery ran 1,645 tests with 1,639 passes,5 platform skips and1 existing disabled-archive capacity deadline error; the exact approved 6c9baseline reproduces that error. No full-run pass is claimed. Five platform IDs pass cached Linux; the exact raw export passes two native composition cases and the same five POSIX guards. Independent review findings R1–R3 are fixed and re-reviewed. AST-only graph update is complete.

See `research/runs/paper-operational-corrections-v1/VERIFICATION.md` and `docs/PAPER_OPERATIONAL_CORRECTION_CUTOVER.md` for exact source, tests, limitations and reviewed migration/rollback procedure. Source candidate 49c8513 and its byte-preserving 522-file export are concrete; only archive_snapshot.py differs in the actual 129-file runtime map. Existing main/live source remains 6c9d520.

The final checkbox means the separate approval boundary has been prepared and preserved, not that successor deployment or live binding changes occurred. Request that approval only after this local record is finalized. Original experiment and long forward qualification remain active/incomplete.
