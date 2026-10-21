# Recorded archive history implementation plan

> **For agentic workers:** Execute inline with test-driven development.

**Goal:** Resolve original report history from recorded retirement references rather than manually assembled source lists.

**Architecture:** A disabled context manager starts from a caller-pinned consistent current shared journal, validates its checkpoint ancestry and resolves the referenced archive_store manifest chain. It restores bounded disposable full-cohort copies, rechecks inventories/native certificates/review history and matches each recorded cut before calling the existing report-only shared-history reader. The native report selects this explicit format without treating unknown opaque hashes as supported archives.

**Tech Stack:** Python, SQLite, unittest; existing content-addressed store/coordinator/certificate/reader.

**Spec:** Approved October15 archive integration proposal, October16 implementation task4; preceding October19 shared-report history plan.

## Global constraints

- Existing October16 approval covers disabled implementation and invented correctness checks. No deployment/activation/live evidence retirement/backtest/training/paid calls/real orders.
- Trusted current snapshot pin remains the root of trust; this reader grants no newest-cut/old-host-stop/independent-backup/retirement authority. Those gates remain open.
- Explicit format is `recorded_archive_store_v1`; every reference must be a real `paired_evidence_snapshot_v1` store manifest containing the coordinated capture and exact inventories. No interpretation of arbitrary opaque digest values.
- Preserve all8 accounts, original fees/contracts/review IDs and raw shared evidence. Reject missing/corrupt/unrelated input and nonempty WAL/journal. No source writes.
- Bound store reads, aggregate decoded restored sources and disposable union separately by max_bytes (4KiB..4GiB); metadata/file/source counts inherit existing caps. Whole-domain combined peak admission remains unfinished. One absolute deadline spans resolution and reading; OS-blocked I/O is not preemptible.
- Retain dirty candidate in place; do not commit/reset or overwrite prior evidence. Pin sources and verify only relevant checks for this step; full release gate remains required before deployment.

## Review focus

Manifest coverage labels differ from retirement cuts. Validate complete parent chain and each operation's certificate fields/prefix/next input/account digests; do not use a declared range as proof of SQL contents. Reject wrong selected-profile paths, missing parent and forged certificate even with recomputed outer bytes. Never expose a restored full-history journal as runnable state. Preserve original current and archive bytes.

### Task1: Recorded resolver

Create `hermes_trading/archive_shared_resolver.py`, `tests/test_archive_shared_resolver.py`.

Interface: `recorded_shared_history(current, *, enabled=False, format, archive_root, shared_relative_path, expected_cohort_sha256, expected_experiment_id, cache_root, max_bytes, timeout_seconds, now=None, clock=...)` yields existing `SharedHistoryView`. Current contains exactly path/sha256. Resolve all referenced archives through current retired-operation ancestry; chain, complete stored files, inventories, certificate and review audit must agree. Manifest last_batch is captured head, not retired_complete; enforce both independently.

- [x] RED: absent resolver and native selected50/200 two-retirement continuation, original fees and immutable sources.
- [x] GREEN: bounded restore/verification and report-only history composition.
- [x] RED/GREEN: disabled/wrong-format/bad pin/WAL/deadline/bounds, missing/corrupt parent/object, certificate/cut/account/path conflict. Current ancestry uses the existing validated checkpoint protocol and its regressions.

### Task2: Native report

Modify `paired_performance_report.py`; extend resolver tests with real report invocation. Existing explicit caller-verified sources remain the lower-level reader contract; new native history mapping discriminates `format=recorded_archive_store_v1` with `current,archive_root,cache_root,max_bytes,timeout_seconds,enabled`.

- [x] RED/GREEN: native report reaches resolved original history; missing/unknown archive is incomplete. Latest source path must equal the selected report journal. Profit qualification remains false.

### Task3: Evidence and review

- [x] Run scoped resolver/shared/report/cohort regressions; independent review and fix important findings with RED/GREEN.
- [x] Record pins/results/bounds/provenance limitations, update graph and progress, keep full goal active and trialdays0.

Evidence: `research/runs/recorded-archive-history-v1/`.128scoped tests:126passed/2platform skips,zeroResourceWarnings,258source pins unchanged. Independent P2 malformed archived JSON finding reproduced on an actual resealed cohort, fixed and rechecked. AST-only graph refreshed. This completes this reader composition; parent archive retirement/capacity/recovery installation and final full release remain open. No production authority or profitability claim.

Ruling: implement the existing object-store format explicitly and revalidate its native coordinated cohort evidence. This closes the source-resolution seam while leaving transport installation, physical retention, capacity and recovery admission explicit. It does not narrow the parent goal.
