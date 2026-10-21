# Shared report history implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, with test-driven development.

**Goal:** Preserve complete original paired report inputs after shared-journal retirement.

**Architecture:** A disabled disposable reader unions caller-verified, hash-pinned consistent SQLite snapshots in chronological order. Each source is checked against its own checkpoint; the complete union reconciles every retained retirement prefix and next-input hash. Current publication/risk state remains separate from the report-only history tables.

**Tech Stack:** Python, SQLite, unittest; no new dependency.

**Spec:** Existing approved October15 production archive proposal and October16 archive integration implementation, task4.

## Global constraints

- Existing October16 approval covers this disabled history adapter and synthetic correctness checks; continue without another approval request. No deployment, activation, deletion of live evidence, economic backtest, training, paid calls or trading.
- Preserve eight wallets, original raw packets/settlements/object bytes, rowids, financial ancestry, costs and review history. No financial-account compaction in this step.
- Work in the authorized dirty feat/live-hardening candidate; do not commit/reset or replace prior reviewed artifacts.
- Caller pins come from independently verified exports. This adapter does not resolve/authenticate archive manifests, establish independent-copy durability, resume a worker or authorize retirement. Those remain explicit integration work.
- Reject nonempty WAL/rollback journals. Input/cache ceilings are separate, not an aggregate Railway/PC capacity proof. A finite SQL/read deadline cannot bound an OS-blocked file operation.

## Review focus

Missing or conflicting retired records must fail closed; overlapping tails must not double count. Compressed shared objects must be preserved. A main-file hash cannot pin live WAL state. Restored full history must not be presented as a runnable current journal. A missing archive must never silently qualify a report.

### Task1: Original shared history reader

Files: create `hermes_trading/archive_shared_history.py`, `tests/test_archive_shared_history.py`.

Interface: `shared_history(sources, *, enabled=False, expected_cohort_sha256, expected_experiment_id, cache_root, max_bytes, timeout_seconds, clock=...)` yields a view with query-only `history` and `current` connections. Sources contain exactly `path,sha256`; latest is the current consistent snapshot. Only current state supplies publications/risk. History contains original shared batch/settlement/object tables, not executable lifecycle/checkpoint state.

- [x] RED: union two real synthetic retirements; preserve raw bytes/rowids, read-only sources and query-only view.
- [x] RED: gaps, conflicts, missing objects, wrong cohort/experiment, regression, checkpoint corruption, incomplete sources, bad pins/WAL, bounds/deadlines.
- [x] GREEN: minimal reader; reconcile full prefix and every next-input hash.

### Task2: Native report integration

Files: modify `hermes_trading/paired_performance_report.py`; create `tests/test_paired_report_history.py`.

Interface: optional explicit `history` mapping (`enabled,sources,cache_root,max_bytes,timeout_seconds`). Latest source must equal the selected report journal. Default report rejects retired history without this adapter. Financial and inception inputs still require intact consistent account evidence.

- [x] RED: current-only retired report is incomplete; archived-versus-uninterrupted one-day reports agree on base/stress equity and observations for EUR50/200 with all8 settlements.
- [x] GREEN: use latest pinned connection for controls, union only for settled-pair iteration; unavailable/invalid history returns incomplete.

### Task3: Review and evidence

- [x] Run relevant archive/report/primary regressions; one independent review of this coherent change; fix important findings with RED/GREEN.
- [x] Record source pins, commands/results and preserved prior candidate; update AST graph and progress/readiness without incrementing trial days or claiming capacity/profit.

Evidence: `research/runs/shared-report-history-v1/`. Initial124 scoped checks pass at256pins; review exposed two further P2 guards, both RED/GREEN and reviewer-rechecked. Final31 affected checks pass at256unchanged pins,zeroResourceWarnings. This is scoped verification, not a new full-release run. AST graph updated without paid labeling. Parent archive tasks4/5/6, authenticated history resolution, installed integration and beta capacity remain open.

Ruling: implement the native report-only source-pin contract before the broader archive manifest resolver. This follows the existing account_history trust boundary and fixes a real missing consumer without treating opaque archive_sha256 values as a particular archive format. Cost if wrong: installed composition remains blocked; it receives no authority from this adapter.
