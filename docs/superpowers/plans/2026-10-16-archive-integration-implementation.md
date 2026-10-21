# Archive integration implementation

Approved by user `approve` on October16 for the scope of the October15 production archive integration proposal. Implementation/correctness testing only; no deployment, live-data retirement, activation or paid service.

Execution: inline with TDD. Work in the existing feat/live-hardening checkout, preserving its dirty candidate baseline. Ruling: a clean worktree would omit extensive uncommitted/untracked candidate code; keep the approved candidate in place and fingerprint before edits rather than silently implement against HEAD. No commits/pushes requested.

## Tasks

1. Durable archive operation request/receipt/checkpoint protocol. Typed bounded identities, canonical purpose-bound HMAC, persisted operations, replay/lost-ack restart idempotency, atomic shared retirement/checkpoint and fail-closed corruption/rollback checks. Pure API, no endpoint or live operation.
2. Consistent bounded snapshot/export under writer ownership, immutable object/manifests, restore verification, resumable authenticated transport interface and independent-copy evidence. Disabled configuration; no installation if target/backup/transport is unverified.
3. Native worker archive-aware lifetime counts and durable external/worker high-water reconciliation. Preserve cycle/deadline limits after multiple synthetic retirements and restarts without monkeypatching stats.
4. Cohort account/history continuation, archive-aware accounting/reserve/report/review readers and recovery state machine. Protect open-entry rules/cash/expense/risk/publication state; preserve all unresolved review IDs independently of latest25. Unavailable history blocks entries/qualification while protective exits work from retained contracts.
5. Enforced total footprint/peak-space and export deadlines, outage admission, crash/disk-full/partial-cohort recovery. Source evidence retained on failed export/ack. No reduction in wallet scope/cadence/risk accounting.
6. Eight-account uninterrupted-versus-restored synthetic comparison including partial positions, original fees, both cost books, review/attempt state and risk. Full Windows regression, Linux if Docker available, independent review, source fingerprints and disabled artifact.

Interfaces: snapshot binds exact source/cohort/release/cut/prefix; receipt authenticates that request; durable operation state and checkpoint retirement share one DB transaction. Account transitions must be durable and collection gated until the entire cohort reconciles. Archive-aware readers consume the same immutable original evidence, not reconstructed economic summaries. No step alone proves whole-state capacity or production readiness.

Verification uses unittest and invented correctness inputs only, never strategy backtesting or provider inference. Keep incomplete tasks explicit and continue within this approval; do not ask again for successive local implementation tasks.
