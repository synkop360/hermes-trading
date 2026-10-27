# Local review and verification limits

Scope: approved disabled implementation in the correction worktree. No deployment, actual halt reset, private API call, model call, market replay or training. The review is by the implementer; independent review was unavailable because the delegated reviewer reached its account usage limit.

## Reviewed transitions

- The retry commit 9029e44 changes only the approved typed-BUSY attempt/wait bounds. Full recollection, original validators and process timeouts remain.
- Recovery is opt-in and separate from ordinary health delivery. Normal callers only gain the pending-entry blocker; there is no automatic reset.
- Intent precedes marker, which precedes the two-field state transition, completion receipt and marker removal. Every write is bounded and synchronized; intent and receipts are immutable. Fault tests retain a halt or pending blocker at each durable boundary.
- New health remains recordable. Exact full-state comparison rejects stale apply. Cancellation sets the two halt fields on current health and records that current transition; it never rewinds to the intent's old health. Historical completed retries do not clear a newer halt.
- Preflight holds the original publication lease before acquiring safety state, preserving worker lock order. The native test proves another SQLite writer cannot acquire the publication lease during safety replacement. Account WAL readers depend on that single-writer exclusion; no journal modes are changed.
- The actual PC evidence builder verifies the source set, native wrapper, owner, account, launch limits and fresh running lifecycle, including explicit operator stop and budgets. Evidence is an authenticated-operator attestation, not remote authentication by itself.
- Worker preflight checks the native snapshot, original opening/eight accounts, original startup configuration hash, current settled goals/strategies, publication/risk state, flat positions, terminal orders, resolved incidents, free model readiness, fresh market/health data, volume and daily backup. It uses read-only SQLite connections and retains all financial records.

## Failures retained and corrections

- `retry-red.log` reproduces old retry insufficiency; `retry-green.log` and `sync-green.log` retain the initial 16+7 passes.
- `recovery-health-race-red.log` exposed the rejected freeze-health approach. Blocking health could disrupt the worker's error handler and protective exits. The replacement permits health and invalidates stale apply.
- `preflight-red.log` proves the native receiver was initially absent. Intermediate `preflight-v1` through `v3` preserve fixture/model readiness, native WAL-journal and backup-directory integration mistakes. No live settings were changed to resolve them.
- `affected-suite-first.log`: 205 tests, one asynchronous notification-fixture `KeyError`, one Windows symlink skip. The poll accessed `cadences` before the reporter's background load. It now waits for that field without weakening the expected delivery/cadence assertions.
- `affected-suite-second.log`: 206 tests, one new fixture assertion failure, one Windows symlink skip. The test compared the last recorded worker time with a clock advanced by the subsequent synthetic sleep. It now checks the actual last cycle time; all-eight exits remain asserted.
- `final-review-red.log` exposed the missing explicit PC operator-stop check. Review also added original worker configuration and settled strategy/goal checks. `final-review-green.log` records all 15 recovery tests passing, including those rejection cases.

The final affected run executes **206 tests: 205 passes, one Windows symlink check skipped, zero failures/errors**, in 153.840 seconds. Captured source pins are unchanged through the run. Results and hashes are recorded in `local-verification.json` and `affected-suite.log`. Neither historical failures nor platform skips are converted to passes. This is not a full repository suite or Linux qualification.

## Remaining release work

Keep the current live halt and original experiment clocks. Prepare a byte-pinned release package and checked two-host transition from actual installed 20eb6af, with an exact finite-timeout cutover. Obtain separate deployment and live-recovery approval, then observe ordinary scheduled health before preparing any short-lived recovery request. These local tests establish correctness on synthetic state, not installed reliability or profitability.

The existing 667-file frozen package and 131 installed runtime/supervisor sources are compared read-only by `verify_untouched.py`. The unrelated dirty operational-cutover document remains unchanged. The runtime module set is preserved. The graph update is AST-only; semantic/paid extraction was not run.
