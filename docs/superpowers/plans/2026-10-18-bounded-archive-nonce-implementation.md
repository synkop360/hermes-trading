# Bounded archive nonce implementation

Approved disabled local archive scope, continuing the October17 nonce design. Execute inline after the current venue full regression finishes; never change its frozen source files during that run. No deployment, listener installation, trading activation, live retirement, paid service/inference or market backtest/training is authorized here.

## Contract decisions

- Add `hermes_trading/archive_nonce.py` and `tests/test_archive_nonce.py`; retain `control_auth.verify_command`, `StateRepository` and V1 behavior unchanged.
- An explicit initializer receives a caller-persisted random32lowercase-hex `expected_store_id`, original binding/identity, immutable limits and a live capacity lease. Return a purpose-bound receipt containing that ID. Existing-open always requires the externally retained expected ID; never learn the expected generation from the database being checked. The caller retains its ID through an unknown initialization outcome; an existing target is never reset or silently initialized.
- The constructor stores validated configuration and original path/file identity without opening SQLite read/write. All opening, including hot-journal recovery, happens within admitted initialization or `armed(lease, deadline=...)` work. An absent existing database must not be created by a connection.
- A store belongs to one retained `StorageOwner` and its original process/thread. `armed` requires a live `CapacityLease` whose `owner` is that exact object and whose `require_path` admits the nonce location. Arming is non-nested and request scoped. An absolute finite monotonic deadline uses the owner's clock, cannot extend the original request, and is checked along with the lease before/after SQL and commit. No private lease deadline access is needed: `lease.check()` independently enforces the lease's original deadline.
- Arming grants permission, not an indefinitely open connection. `add_once` opens/validates/commits/closes the nonce database before returning to receiver SQL. One arming permits at most one `add_once` call, including a duplicate rejection. Handles close before releasing admission or the retained owner.
- Explicit synthetic limits start at4096-byte pages, at most256main pages,256live nonce rows and a proposed4MiB additional nonce peak. These values remain unqualified until actual fault/extent checks. Combined receiver headroom must be admitted separately in the same request envelope; do not present4MiB as the total server budget.

## Task1: Separate purpose-bound storage

- [x] Write failing real-file tests for explicit initialization, mandatory expected ID, reopen, foreign binding/identity/limits, missing files and rejection of a newly initialized replacement generation. No implicit reset, migration or overwrite.
- [x] Define exact metadata/nonce schemas and expected table/index inventory, with no triggers, foreign keys, TEMP/attached databases or unrelated tables. Store schema/purpose, expected generation, original binding/identity hashes, immutable limits and nondecreasing acceptance-time floor.
- [x] Implement bounded path/header/schema validation and dedicated `sqlite3` connections with nonblocking ownership. Require DELETE journal mode, FULL synchronization,4096-byte pages, no cache spill and constrained temporary operations. Reject unexpected WAL/SHM before opening; never convert a foreign WAL database in place.
- [x] Require the live admitted owner before any read/write connect or recovery. Keep page/extent checks and actual owner/file identity checks inside this scope. Initializer failures retain committed evidence; precommit cleanup may remove only a demonstrably owned new incomplete file, never an arbitrary old target.

## Task2: Atomic replay and expiry

- [x] Write failing tests for lowercase32-hex nonces, aware datetime conversion to integer UTC microseconds, exact `expires_at = accepted_at +300seconds`, replay across reopen, exact expiry and clock regression.
- [x] Under one transaction: validate floor/configuration/lease; returnFalse only for an unexpired duplicate; prune only legitimately expired rows; enforce remaining live-row bound; insert and advance the floor atomically; enforce page/footprint bounds; commit before returningTrue.
- [x] Use `INSERT OR ROLLBACK`/`UPDATE OR ROLLBACK`; use valid DELETE syntax with no triggers/foreign keys. Do not invent `DELETE OR ROLLBACK`, nested savepoints or process-global SQLite temporary configuration. Any statement failure aborts the whole transaction.
- [x] Test row/page exhaustion and injected precommit I/O/deadline failures: nonce and time floor both roll back, including expired-row pruning. Real SQLite page exhaustion and hot-journal crash recovery are covered; physical filesystem-full and power-loss behavior are not qualified. Postcommit lost replies preserve the accepted nonce. A duplicate must not accidentally lower a floor or consume new capacity.

## Task3: Admitted request and ownership boundaries

- [ ] Test absent, expired, wrong-owner, wrong-thread/process, reused or nested arming; no SQLite write/open/recovery may precede admission. Add hot-journal recovery fault coverage under the correct owner.
- [x] Implement a single request arming context using the retained owner's clock and the caller's original deadline. Check both aggregate remaining headroom and store extents throughout the operation. A declared SQL page cap is not an OS quota.
- [ ] Verify initialization/reopen, maximum-row churn, expiry and faults against actual main/journal/temporary peak observations. Temporary-journal confinement must be demonstrated on the current SQLite build; `temp_store=MEMORY` alone is insufficient evidence.

## Task4: V2-only transport composition

- [x] Extend only the explicit V2 durable-store type admission to include the concrete new adapter; keep the existing concrete store and V1 guard unchanged. A later installed configuration must explicitly require the bounded path before claiming combined admission.
- [x] Compose one request admission after existing envelope/scope/strict-freshness checks. Arm the nonce store, call unchanged `verify_command`, finish nonce commit/close, then enter the original receiver read/apply, retaining the same request deadline and final capacity checks.
- [x] Test invalid MAC, future timestamp and exact300second age with no nonce change. Preserve the generic authentication boundary test, which deliberately has different historical semantics.
- [x] Exercise accepted nonce followed by receiver CAS/deadline failure, lost replies and retries with a fresh envelope nonce plus the original operation ID. Never erase accepted replay evidence after a receiver failure.
- [x] Extend the real prepared eight-account fixture. Acquire the retained domain owner before preparing/opening either database; close all handles before releasing it. Source read/apply calls are separate signed requests and require separate admissions.

## Task5: Evidence and remaining gates

- [x] Freeze changed runtime/tests, run meaningful affected checks with forced cleanup, update the AST graph without paid labeling and preserve all original prototype fingerprints.
- [x] Review the combined boundary at the integration milestone. Record measured peaks, exact limits and platform omissions; do not qualify a horizon from small synthetic cases or raise bounds merely to make a check pass.
- [x] Keep independent older-snapshot recovery floors, coherent V2 receiver/nonce export, all registered roots/writers, broker participation, cross-host transport and full-horizon simultaneous capacity open until separately proved. External expected generation detects replacement/reset; it does not prove that an older same-generation snapshot was not restored.

## October18 checkpoint

Disabled standalone storage and V2 request composition are implemented. Frozen237-source affected run:164checks,163pass/1Windows symlink-privilege skip,206.927runner seconds,208.703outer seconds,zero ResourceWarnings. All original15research prototypes and26draft source files match. Independent review found no remaining runtime correctness issue in scope; its superseded fixture-handle caveat is fixed and confirmed. Two reproduced review gaps (invalid-call arming reuse and initialization file replacement) were corrected with red/green tests.

The256-row/512-acceptance churn diagnostic observed1,025commit checkpoints,57,912maximum named main+journal bytes (main28,672/journal29,240) on SQLite3.45.1. This is not exhaustive temporary/VFS tracing or a production capacity certificate. Task3's complete ownership matrix remains open for a direct foreign-process/unregistered-path adapter check; the shared owner already has real cross-process exclusion/crash tests. The adapter PID guard has a simulated foreign-process check and actual foreign-thread coverage. Physical disk-full/power-loss behavior, exhaustive temporary-file confinement and full-horizon aggregate capacity are not qualified.

The V2-only budget now clamps to the original absolute request deadline, and prepared receiver/server/domain must share the same clock. Receiver headroom is rechecked after nonce commit. Generic authentication and V1 source files are unchanged. No listener, broker, deployment, activation, retirement, paid service or market experiment was installed or run.

Evidence: `research/runs/production-archive-integration-v1/bounded-nonce-verification.json`, `bounded-nonce-review.json`, `bounded-nonce-original-draft-verification.json`, `bounded-nonce-source-delta.json`. Next contract constraints: `2026-10-18-v2-export-recovery-contract-notes.md`.

References: `2026-10-17-bounded-archive-nonce.md`, `2026-10-17-combined-storage-admission.md`, `archive_capacity.StorageDomain.ownership`, `StorageOwner.admit`, `CapacityLease`, `archive_protective_transport.ProtectiveServer.handle`, `archive_protective_receiver.ProtectiveReceiver.prepare`, and unchanged `control_auth.verify_command`.
