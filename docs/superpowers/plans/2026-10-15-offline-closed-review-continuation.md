# Offline Closed-Trade and Review Continuation Plan

> **For agentic workers:** Execute inline within the approved archive design/offline prototype. No production archiver integration.

**Goal:** Preserve closed-trade evidence, cadence/context and pending review/attempt state across archive restoration, including legacy partial-entry contracts.

**Architecture:** Use the existing real ReviewLease/global coordinator with a strict pinned runner whose command is an in-process mock. Populate reviewed and unreviewed synthetic JSONL history. Restore byte-exact archive copies and compare eligibility, latest25 context, consumed unique IDs, pending publication and model-attempt limits. Separately create a real canonical invented EUR position with an old partial-exit contract, archive it while partly open under a changed current contract, restore and close it using the original fees/rules; check independent original-entry lineage counting.

**Files:** new research/runs/offline-closed-review-v1/closed_history.py and test_closed_review.py only, plus evidence/docs. Reuse the existing reviewed archive codec, accounting readers and lifecycle modules unchanged.

## Constraints

- No network/model/provider request, training/economic backtest, paid action, product edit, deployment or activation.
- All log prices/returns and model responses are invented correctness inputs, not evaluated entry hypotheses or market outcomes.
- The five-close operational cadence counts unique projected closed-position IDs; profitability qualification counts fully closed original entries, including their partial legs. Do not conflate these units or silently alter cadence.
- Preserve recovery/risk-stop classifications and pending reviews; do not hide them by keeping only recent prompt context.
- Keep source identities, goal/strategy/history and review/lifecycle files bound to the original experiment.

## Tasks

- [x] Red/green semantic JSONL union: exact duplicate replay accepted, changed identity records rejected, invalid identity/time rejected, all recovery classifications retained. Sorting matches coordinator context rules.
- [x] Verify five-new-close eligibility and latest25 prompt inputs before/after restoration, and exact consumed-ID watermark after a mocked no_change response. Expose backlog/recovery failures of naive last25-only rotation.
- [x] Restore an awaiting-publication review and prove no redispatch/no active strategy mutation. Restore a reserved model-attempt limit and prove it is not reset.
- [x] Exercise real canonical partial exit -> archive -> current-version change -> original-version final close. Verify fees/quantity, idempotent projection rebuild and one original lineage across multiple closed legs.
- [x] Scope review, fix findings, run focused verification, record actual limitations and refresh AST graph.

Ruling: the source log used for cadence is full evidence; latest25 is only prompt context. A compacted log requires an archive-aware union or equivalent validated checkpoint for every unresolved ID/classification. The experiment will report a failure of naive rotation, not patch live review behavior without approval.
