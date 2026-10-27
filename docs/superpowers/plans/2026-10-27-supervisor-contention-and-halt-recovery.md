# Supervisor contention and halt recovery implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task. Continue the existing isolated correction worktree; never edit the installed PC runtime.

**Goal:** Implement the approved bounded snapshot correction and an opt-in audited paper supervisor-halt acknowledgment, using synthetic state only.

**Architecture:** Keep the native snapshot validators and publication lease. Add recovery request validation/preflight to `paper_supervisor_health.py` and locked durable transition primitives to `paper_safety.py`. A pending marker blocks admission and health replacement until checked completion or conservative abort.

**Tech stack:** Existing Python, SQLite, unittest and artifact locks; no new dependency.

**Spec:** [Approved design](../specs/2026-10-27-supervisor-contention-and-halt-recovery-design.md). Owner replied “Approve” to the request for design and disabled local implementation. Deployment and live recovery are excluded.

## Constraints

- Exactly eight snapshot attempts, waits 0.2/0.5/1/2/2/2/2 seconds, ten-second admission deadline. Existing subprocess/outer deadlines unchanged.
- Only numeric SQLite BUSY through an explicit cause is retryable; every snapshot is fully re-read and validated.
- No automatic latch clearing, new health success, risk-policy change, actual-state mutation, model call or deployment.
- Preserve the runtime module set, existing eight-wallet accounting and original clocks/history.
- Preserve unrelated dirty `docs/PAPER_OPERATIONAL_CORRECTION_CUTOVER.md`.

## Review focus

- Lost replies after state replacement must not allow a stale request to clear a newer halt.
- Crashes between intent, marker, state, completion receipt and marker removal must leave entries blocked or prove completed recovery.
- New health arriving while recovery is pending must not invalidate the frozen before/after state silently.
- Remote preflight must verify data itself, not accept caller-supplied “healthy” booleans; PC evidence must be built from checked local launch/source.
- Read-only SQLite and linked-path checks must not migrate/open new trading databases or mix settlements.

## Task 1 — Retry correction

Files: `hermes_trading/supervisor_sync.py`, `tests/test_supervisor_snapshot_retry.py`.

- [ ] Add a real SQLite-lock test releasing after the old retry window; update fixed numerical contract tests to the approved bounds and observe failures.
- [ ] Change only attempt/wait/admission values in `make_snapshot_with_retry(state_dir, *, now=None)`.
- [ ] Run discovery for snapshot-retry and supervisor-sync cases, preserving the full-source validation and rejection tests.
- [ ] Commit the independently verified retry correction.

## Task 2 — Durable safety transition

Files: `hermes_trading/paper_safety.py`, new synthetic `tests/test_paper_supervisor_recovery.py`.

Interfaces: `recover_supervisor(root, request, *, preflight, now=None, enabled=False, abort=False)`; the preflight callback returns a validated binding dictionary and is rerun under the safety lock. Recovery is not reachable from ordinary health calls.

- [ ] Test missing opt-in, expected complete-state hash, supervisor-event identity, only-two-field mutation, duplicate/conflicting IDs and linked/malformed audit files.
- [ ] Implement bounded immutable intent/completion records, atomic pending marker and atomic existing safety-state replacement under the current artifact lock. Maximum 32 request IDs; no deletion/retention expansion.
- [ ] Check the pending marker after normal admission reads; reject health-state replacement while a recovery is pending. This freezes health for crash reconciliation rather than silently losing new observations; the existing sender retains/retries its exact pending health request.
- [ ] Test each durable boundary with injected faults and an intervening health attempt. A same-request retry can complete only an exact before/after state while fresh preflight passes. Explicit abort restores only the two halt fields if needed, records cancellation, and leaves the original halt set. It cannot replace changed health.
- [ ] Run existing paper-safety and health-transport cases with the new recovery tests.

## Task 3 — Checked preflight and opt-in command

Files: `hermes_trading/paper_supervisor_health.py`, `tests/test_paper_supervisor_recovery.py`.

Interfaces: `build_recovery_request(root, *, approval_ref, pc_evidence, now=None)`, `prepare_recovery_pc_evidence(project, launch_file, *, now=None)`, `receive_recovery(root, request, *, enabled=False, env=None, now=None, abort=False)`.

- [ ] Tests reject live mode, foreign source/policy/manifest/opening/account, stale or failed health, other latches, volume/backup/market problems, pending work, non-flat accounts, unresolved orders/incidents and changed proof.
- [ ] Build PC evidence through the existing native launch/source validation; bind its full release, owner/account and observation time. A copied proof is operator-supplied evidence, not remote authentication; the eventual cutover must run this on the actual PC.
- [ ] Preflight uses the original native snapshot, authoritative paired inception and all-eight-account reads, verifies current worker backup/health/market state and strict release/identity hashes. Acquire read-only SQLite transactions in a deterministic order and retain them through the safety transition to prevent racing settlement. Never migrate a database.
- [ ] Add mutually exclusive health/recovery CLI arguments plus explicit recovery opt-in; default health delivery remains unchanged. Output only typed rejection codes on errors, without arbitrary exception text.
- [ ] Verify entry remains blocked for pending/failed recovery and ordinary valid health after acknowledgment retains the unchanged three-error policy.

## Task 4 — Final review and evidence

- [ ] Run affected native notification, worker-safety, backup, publication, lifecycle/launch and recovery checks; retain failures and final source pins. No market replay/backtest.
- [ ] Review the final diff against the spec, update the AST graph without APIs, and verify the installed runtime and frozen 20eb6af package are untouched.
- [ ] Save an operator guide explaining prepare/apply/retry/abort, trust boundaries and the separate live approval requirement. The release-transition purpose and exact both-host cutover are release preparation work; do not alter live bindings or claim deployment readiness here.
- [ ] Commit only owned source/tests/docs/evidence and report the local outcome and remaining live gate.
