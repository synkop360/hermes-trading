# Supervisor usage source implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, preserving the current worktree and existing user changes. The user authorized independent recovery work; this bounded integration adds no model request, paid service or operational activation.

**Goal:** Produce auditable prospective model-cost coverage from the actual sole-writer supervisor, rather than interpreting an empty generic ledger as zero.

**Architecture:** Bind each strict model usage event to its global experiment/review under the existing ReviewLease. Export a source snapshot while that same exclusive lease excludes new requests. Only initialized inception/owner, canonical review records and correlated append-only usage receipts establish coverage; failures and orphan/missing/duplicate evidence must never turn into zero. Keep the resulting model-only source separate from fixed subscription costs and actual provider billing adjustments.

**Tech stack:** Existing Python dataclasses, JSON, SQLite ReviewLease and Decimal; unittest fixtures only.

- [x] Add a review-scoped CostLedger wrapper around a global runner invocation. Record experiment_id, policy_hash, review_id and request_started_at in every usage event, restoring the original ledger after success/failure.
- [x] Add supervisor_usage_source.py to export a schema1 snapshot with experiment, immutable inception/owner pin, covered-through time, EUR-compatible model events and source hashes. Require exact inception match to an explicit wallet account. Use ReviewLease; do not initialize an absent supervisor or create a fake initial ledger. Snapshot time must be sampled after obtaining the lease; future or regressing event/review timestamps are invalid.
- [x] Match exactly one cost event per dispatched review; a request without a matching event blocks export for reconciliation. Pending reviews are allowed only when their durable response and usage prove the request completed; uncertain pending requests block coverage. In-scope orphan model/tool costs or mismatching pins/scopes and duplicate IDs are rejected. Historical pre-inception usage is excluded, never repriced or charged to a new account. Unpriced charges remain explicit missing values.
- [x] Verify a claimed zero Hermes receipt against the live-catalog proof already captured by transport: exact source/model, all prices zero, recent proof before the event, nonnegative integer usage and matching raw HTTP hash/response model/usage. This is recorded inference cost, not a subscription or provider-invoice certification. Nonzero foreign charges require explicit event-specific EUR conversion evidence or remain unpriced; this stage must not invent FX rates.
- [x] Atomically write the snapshot while retaining the lease. Include event IDs, original currency/amount, quality and provenance hashes; avoid copying raw prompts/responses into the small exported file. Unknown fixed subscriptions/hosting remain separate launch gates. The control receives no adaptive inference charge.
- [x] Tests first: real global runner usage attribution/restoration; empty initialized ledger coverage; absent/mismatched inception rejection; verified zero versus missing evidence; pending/missing/duplicate/orphan scope; historical exclusion; second-writer refusal during export; immutable repeatability and projection failure.
- [x] Run focused/full verification, obtain independent scoped review, update AST graph and save evidence. No deployment or new experiment in this implementation stage.

Read-only Railway preflight on October11 also found current workspace usageUSD2.6568150530772834 and estimated billUSD5.23534514815465; service usageUSD2.656771174956901, mostly memory. These describe the existing service/billing period, not the new eight-account experiment's fully measured hosting costs or EUR conversion. Preserve source snapshots; do not silently adopt them as actual new-experiment costs.

## Verified checkpoint

21source tests,10cost tests and833full-suite tests pass in153.656s. Follow-up independent scoped read-only review clear; evidence research/runs/supervisor-usage-source-v1. No deployed feed, actual invoice reconciliation, fixed subscription allocation, new model call or experiment activation is claimed. Next gating work is remote source consumption/base allocations and the measured persistent-volume capacity blocker, followed by controlled deployment and prospective observation.
