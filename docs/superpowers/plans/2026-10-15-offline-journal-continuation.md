# Offline Journal Continuation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline within the approved synthetic-copy archive prototype scope.

**Goal:** Test whether a compacted disposable journal can accept a further invented batch and be reconstructed with its archived prefix without changing finance or restart limits.

**Architecture:** Clone each already verified original/reserve fixture into an uninterrupted reference and a disposable active copy. Prune selected high-growth journal prefixes only in the disposable copy, repack SQLite while preserving explicit rowids, append the same synthetic quotes to each branch with the unmodified core runner, and merge archived tables with the active continuation into a fresh full-history reconstruction. Compare finance, original batch inputs and control state. Independently test the production worker's batch count on the compacted copy; a reset is a blocking finding, not an acceptable shortcut.

**Tech Stack:** stdlib SQLite/unittest, existing archive and financial readers, invented received quotes, no network.

**Spec:** The approved offline-evidence-archive design and its README next-proof contract. This continues the user's already approved design/offline prototype; it does not authorize production compaction.

## Constraints

- Modify only new research files and disposable copies; preserve original fixtures, archive receipts and runtime source.
- No paid action, live-data deletion, deploy, economic backtest, model call or activation.
- Retain all orders, fills, positions, entry contracts, cost/owner/review/risk/limits state and pending/unprojected records. Full lineage retention is conservative and is not a lifetime space bound.
- Keep latest16 completed common batches/settlements and observations for the128-batch fixture; latest1 for the2-batch fixture. Keep recent decisions/candles/closed incidents plus linked and pending records. Preserve nonempty WAL refusal and rowid ordering.
- Keep complete JSONL projections and packet objects. Therefore sample shrinkage cannot certify future bounded growth.
- Merge is a laboratory reconstruction on known schemas, not an online archive reader. Operational deletions/tombstones and arbitrary concurrent mutations remain unsupported.

## Tasks

- [x] Red/green unit checks for logical repack preserving sparse rowids, schema/foreign keys, prefix merge plus mutable updates/new rows, and explicit rejection of unsupported tables.
- [x] Record before/after file footprint and rowcounts; append one common invented batch to compacted and reference branches using current unmodified run_paired_cycle.
- [x] Reconstruct full history using the validated archive plus active tables; compare canonical fills, positions, observations, stress reserves, reports and common input hashes. Check risk/owner/model/worker source files remain intact.
- [x] Record whether _stats still counts lifetime batches on active state; do not patch production if it fails. Verify corruption/missing archive prevents reconstruction before output publication.
- [x] Review, fix meaningful issues, verify source fingerprints and refresh AST graph. Record exactly what remains before a production storage design can be approved.

Ruling: no VACUUM-based rowid assumption. Recreate tables and insert explicit rowid values so immutable original order sequence is preserved even when rowids have gaps. Unsupported virtual/WITHOUT ROWID/internal tables fail closed.

Ruling: compare independent appended branches by finance and original input hashes, not new random batch/decision IDs or wall-clock recorded_at values. Within each reconstruction, retain the actual active branch identifiers unchanged.


Completed offline stage: finance reconstruction passed, while the current native lifetime-count invariant demonstrably fails after pruning. Eight Windows tests pass; Docker unavailable so no fresh Linux result. Reviewer pending-state finding fixed and final staged hash-bound restorations passed. This is a blocking design finding, not production readiness. Evidence: research/runs/offline-journal-continuation-v1/verification.json.
