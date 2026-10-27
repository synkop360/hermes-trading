# Supervisor contention and explicit paper-halt recovery

October 27, 2026. Proposed design for disabled local implementation and synthetic checks. This is not deployment, live recovery or entry-resumption approval.

## Confirmed problem

The installed 20eb6af runtime still rejects a native snapshot with numeric SQLite BUSY. The October 27 07:00:43.778912 UTC probe exhausted its existing retry path in 0.703 seconds. Thirteen preceding calls in that bounded observation passed. This reproduces an intermittent failure in the installed collector, not merely an old alert. The worker acquires its exclusive publication lease before asynchronous market collection and retains it through settlement. Three attempts separated by 0.2 and 0.5 seconds cannot span every normal occupied interval.

Separately, the safety recorder intentionally preserves the supervisor entry halt after three errors, including after later success. There is no checked acknowledgment API. Fixing collection alone therefore cannot resume the experiment. See [current evidence](../../../research/runs/paper-supervisor-recovery-v1/README.md).

The sampled scheduler history contains 59 failed and 352 completed invocations after the previous cutover. Generic child output was not retained, so the reproduced BUSY failure cannot be attributed to every historical failure. No profitability or model-review outcome is established.

## Choice and scope

Recommended: retain the current publication lease and every validator, give the existing typed-BUSY retry a larger finite opportunity, and add an explicit operator acknowledgment for this paper supervisor halt. This is an engineering choice for the user's delegated correction task.

Moving public market collection outside the exclusive lease would change batch/publication concurrency and requires wider proof. Automatically clearing the latch after a success would change the safety contract. Neither is included.

Use the existing modules (`supervisor_sync.py`, `paper_safety.py`, `paper_supervisor_health.py`) and the existing operator release helper. Preserve the runtime file set, eight wallets, €200 primary, fixed control, original inception and cost clocks, all evidence, fees and risk thresholds. The separately committed Discord presentation change may share a later reviewed release; it does not authorize one.

## A. Snapshot correction

- Allow at most eight complete attempts, with waits of 0.2, 0.5, 1, 2, 2, 2 and 2 seconds; admit no retry at or beyond ten seconds elapsed monotonic time. Cap each wait by remaining admission time. Ten seconds is a proposed engineering bound, not a measured guarantee that every lock resolves within it.
- Keep the existing 30-second snapshot subprocess timeout and outer invocation budgets unchanged. The admission budget does not interrupt an in-flight filesystem read; the original process timeout remains authoritative.
- Retry only actual `sqlite3.OperationalError` with numeric base code `SQLITE_BUSY`, directly or through the existing bounded explicit-cause chain. Do not treat pending work, an unhealthy worker, stale data, report mismatch, corrupt state or any other validation failure as retryable.
- Discard the entire failed collection and rerun every original gate/read/recheck. Exhaustion rethrows the actual final error. No fake healthy result, health event, model call or halt reset occurs in the collector.

## B. Explicit supervisor-halt acknowledgment

Add a disabled-by-default operator command, separate from ordinary health delivery. Neither the worker, scheduled supervisor, Discord sender nor a successful health event invokes it automatically.

Before a later live acknowledgment, require a separately approved release/action and a concrete, short-lived request binding the experiment, manifest/opening, selected primary/account, both-host release and policy, current complete safety-state hash, latest supervisor event ID/time, request ID and operator approval reference. A free-form approval string is audit metadata, not authentication: execution uses the already authenticated operator transport and separately authorized cutover.

At execution, reject unless all these checks still pass:

1. Explicit paper/simulated mode, exact approved source on worker and PC, original experiment and all eight account identities. No mixed release or changed policy.
2. A fully valid current snapshot, no pending common batch/publication, no global experiment/risk halt or unresolved recovery. Current supervisor health is successful and no older than 60 seconds. Worker health is fresh, successful and unlatched. The original entry-admission checks reveal only the supervisor latch as a blocker.
3. All eight accounts are flat, with no unresolved order or reconciliation incident. The ordinary safety checks for volume, backup and market health remain satisfied. Missing or racing evidence rejects; it is not assumed clear.
4. Under the existing safety-state lock, the complete expected state hash and supervisor event still match. New health, a new failure or any other state change requires fresh preparation. No stale request or mismatched retry may reset a newer halt.

Only the supervisor's `entry_halt` and latched `consecutive_errors` change to false and zero. Preserve its actual last event ID/time, success and error fields; do not manufacture a new success. Preserve the worker row, original policy and all financial/strategy/history records.

Record the authorization, exact before/after hashes and old supervisor row in a bounded immutable write-ahead audit intent before state mutation. A pending recovery marker keeps entry admission blocked until the resulting state and durable completion receipt are checked. Perform the state replacement atomically under the existing lock; never hand-edit the JSON. Normal admission checks the pending marker after reading state, so a partial operation cannot expose a cleared latch as permission to trade.

Complete only when the expected transition and audit receipt agree. Duplicate completed requests are checked acknowledgments, not additional resets. Conflicting IDs reject. A crash, ambiguous reply, failed disk write or intervening failure leaves recovery pending/blocked and requires checked reconciliation; do not delete a marker, roll back old health or retry an unknown mutation blindly. Bound artifacts and reject if bounds are exceeded; no new archive/retention project is included.

This acknowledgment does not override another blocker or guarantee that an entry will qualify. Subsequent genuine failures still use the unchanged three-error rule. No real orders or paid model calls are permitted.

## Verification and release gates

Use synthetic copies and coordinated real SQLite locks. Prove a lock lasting beyond the old 0.7-second sequence can recover only to a fully validated new snapshot; permanent BUSY still exhausts; timing/attempt bounds and every non-BUSY rejection remain intact. Preserve existing tests for new file reads, both publication gates and the final error cause.

For acknowledgment, cover stale/conflicting state, foreign policy/release/account/opening, non-paper mode, missing/failed/stale health, each independent halt, non-flat accounts, unresolved orders/incidents, pending batches, disk failure, concurrent new health, exact retry and crashes before/after every durable boundary. Verify that only the two authorized supervisor fields change, historical evidence survives, and pending recovery blocks entries throughout. Existing health-delivery and sticky-halt tests remain valid.

Before any deployment, prepare an exact candidate with the checked two-host release transition, including the Discord formatting revision if selected. Pin the actual 20eb6af predecessor runtime map `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2` and full supervisor release `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7`; verify the live deployment again rather than assuming an old instance ID.

Deployment and live acknowledgment need a separate concrete approval. Observe ordinary supervisor success after installation before preparing acknowledgment; a local test or operator snapshot cannot substitute for scheduled health. Do not clear the halt while investigating ongoing unexplained failures. Preserve the already recorded continuity gaps; no backtest, training, paid inference, forced trade, clock reset or new profitability claim is part of this work.
