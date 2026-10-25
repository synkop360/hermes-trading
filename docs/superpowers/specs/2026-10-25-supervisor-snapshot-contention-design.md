# Bounded supervisor snapshot recollection

Date: October 25, 2026. Status: **owner-approved for disabled local implementation and synthetic correctness checks**. The owner's reply was “Approve design and disabled local implementation.” This document grants no deployment or latch-reset authority. [Implementation plan](../plans/2026-10-25-supervisor-snapshot-contention.md).

## Intent and evidence

Allow a new fully validated supervisor snapshot when SQLite contention resolves within a small retry budget. A live read-only observation at 19:39:19 UTC proved numeric `SQLITE_BUSY` (5) in the zero-wait publication gate. A prior call in the same process validated successfully; the lock duration and its release after the rejected call were not measured. [Evidence and limitations](../../../research/runs/paper-supervisor-diagnostic-v1/README.md).

The current CLI calls `make_snapshot` once. Its publication gates reject pending work, bad cohort/manifest/risk state and database unavailability. Those checks protect the experiment and must continue to reject invalid state. The improvement is a brief opportunity to collect a completely new valid snapshot after a typed BUSY failure.

## Approaches and recommendation

1. **Bounded whole-snapshot recollection inside the CLI — recommended.** Reuse the exact existing collector and validators. Retry only a real SQLite BUSY cause, with a small attempt and time budget. The watcher command, wire schema, native wrapper and global invocation deadlines stay unchanged. This addresses the confirmed failure without a new remote error protocol.
2. Increase the publication gate's SQLite connection timeout. This would change behavior for publication and steering callers beyond snapshot collection and could compound their budgets. It has a wider effect than the evidence justifies.
3. Add a retry/error protocol to the watcher. This can provide richer diagnostics later, but would change both sides of the interface and add more release bindings for this small repair.

The recommendation and numerical defaults below are engineering proposals, not preferences already approved by the owner.

## Runtime behavior

- Keep `make_snapshot(state_dir, *, now=None)` as the existing single-attempt collector. Add a small CLI-facing wrapper in `hermes_trading/supervisor_sync.py`; `main()` calls that wrapper.
- Allow **at most three complete collection attempts**. After the first and second retryable failures, wait **0.2 and 0.5 seconds**, respectively. Admit no further attempt at or after **two seconds of elapsed monotonic time** from the wrapper's start; do not sleep past that admission deadline.
- The two-second limit governs retry admission and waiting. It does not claim to interrupt an in-flight filesystem read. The existing 30-second remote subprocess limit and outer invocation/phase budgets remain the hard process boundaries; never extend them.
- Retry only `sqlite3.OperationalError` with an integer `sqlite_errorcode` whose base code (`code & 255`) equals `sqlite3.SQLITE_BUSY`, either directly or through an explicit `__cause__` chain from this collection attempt. Bound traversal and detect cycles. Never infer retryability from an error message, class-name string, or implicit `__context__`.
- `SQLITE_LOCKED`, I/O/corruption/open errors, absent/unrecognized codes, unrelated exceptions and validation failures fail immediately. No broader transient category is introduced without evidence.
- Discard every incomplete attempt. Re-run the original entire collector, including both publication gates, identity/cohort checks, all file reads, original-config/primary/account rechecks, source/startup/process checks, healthy/fresh market evidence, strategy version and report/heartbeat hash validation.
- Return only a fully validated snapshot. On exhausted attempts or admission budget, re-raise the actual final failure; do not synthesize a healthy result, suppress failure, reuse partial files or publish a success receipt.
- Successful CLI JSON remains the existing schema. No new provider/model calls, strategy writes, background processes, diagnostic payload journal or raw exception retention is introduced.

## Release and operation constraints

The intended runtime delta is exactly `supervisor_sync.py`; watcher and native-launcher bytes stay unchanged. Before any later deployment, prepare a frozen candidate and extend the existing checked release-transition helper with a purpose scoped to this exact runtime delta and unchanged watcher/native hashes. Pin that purpose's predecessor to deployed source `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9`, supervisor release `fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844` and runtime-map hash `0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c`. Derive and verify successor pins from the frozen candidate; never infer predecessor authority from filenames or a matching one-file diff alone. Other transition purposes retain their current scope. All six bindings on both hosts still require the checked transition and their own root-bound intent. A local prototype must prove rejection of foreign predecessors/deltas and mixed releases.

Local implementation approval permits synthetic copies only. It does not permit editing actual launch guards, installing code in the PC runtime, live migration, deployment, restart or a readiness marker change. Those actions require a concrete reviewed cutover and separate owner approval. Preserve the existing original opening, manifest, eight wallets, clocks, journals, costs, policy, fixed control and risk limits.

**The sticky supervisor entry halt remains set.** This proposal adds no automatic or manual reset, fake health receipt, state-file edit, global risk resume or clock reset. Later entry recovery needs its own checked procedure and explicit authorization; it is not a side effect of retry success. Archive/checkpoint features remain disabled and unchanged.

## Required evidence before a candidate is reviewable

Use local synthetic state and real SQLite contention; no historical market replay, strategy optimization, backtest, training or paid call is needed.

1. A real exclusive lock causes BUSY, is released between attempts, and only a completely revalidated later snapshot succeeds. Use coordination rather than a timing-sensitive expected race.
2. A lock held throughout rejects within the existing outer boundary and attempts remain at most three. Fake monotonic/sleep inputs verify exact waiting and admission behavior; no extra attempt occurs at the two-second boundary.
3. Prove second/third collection reads fresh files and executes both gates. Configuration/identity/report changes introduced after the first failure must reject rather than reuse an earlier good subset.
4. Pending batches, global halt, unhealthy/stale worker, report/heartbeat mismatch, foreign account/source/manifest, malformed JSON and non-BUSY SQLite errors retain their existing rejection behavior and do not trigger retry.
5. Direct BUSY, explicit wrapped BUSY, unsupported code, implicit unrelated context and cyclic exception causes exercise the precise classifier boundary.
6. The CLI keeps its success schema, final nonzero failure semantics and unchanged watcher argv/deadlines. A successful retry does not clear `supervisor_error_halt` or invoke the model.
7. The new release-transition purpose accepts only its declared runtime delta, preserves original guard/environment/identity checks and rejects partial/mixed/foreign transitions on synthetic copies. Native wrapper and watcher hashes remain unchanged.

Run the affected snapshot, publication, safety, launch/transition and paired-worker checks, with source hashes checked before and after. Report failures and coverage limits explicitly. The already recorded disabled-archive fixture deadline errors are not a reason to expand archive engineering or loosen deadlines.

## Acceptance and remaining uncertainty

Acceptance means typed transient contention can recover to a fully validated snapshot locally, while permanent or invalid state still fails closed. A live success rate improvement remains unproved until a separately approved deployment is observed. The earlier operator-cycle failure and all historical cron failures cannot be attributed solely to this cause. This repair alone does not resume trading, establish an LLM learning outcome, qualify a profitable wallet or prove an edge.
