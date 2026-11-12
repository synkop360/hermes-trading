# Supervisor snapshot contention implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans task by task. Read the approved spec before changing code.

**Goal:** Recover from typed SQLite BUSY during snapshot collection without weakening validation or changing the existing entry halt.

**Architecture:** Add a CLI-only bounded wrapper around the unchanged single-attempt collector. Extend the operator transition helper with one purpose pinned to the deployed predecessor and one runtime-file delta. Runtime and transition work use separate files and can proceed independently; integration is reviewed together.

**Tech Stack:** Existing Python 3.11+, sqlite3, unittest and installed project environment; no new dependencies.

**Spec:** [Approved design](../specs/2026-10-25-supervisor-snapshot-contention-design.md). The owner approved design and disabled local implementation; deployment and recovery remain excluded.

## Global constraints

- At most three fresh collections; waits 0.2/0.5 seconds; two-second monotonic retry-admission/waiting boundary, not an in-flight I/O deadline.
- Numeric SQLite BUSY base code 5 only, through direct error or bounded explicit cause chain. Never parse messages or implicit context.
- Preserve every existing collector check, success schema, 30-second subprocess limit, outer phase/invocation limits, native/watcher source bytes and sticky entry halt.
- Synthetic state only; no deployment, actual guard edits, restart, provider/model use, backtest, training, private exchange request or real order.
- Keep preexisting `docs/PAPER_OPERATIONAL_CORRECTION_CUTOVER.md` edits excluded. Do not change the PC runtime checkout or actual state.

## Review focus

- A lock that clears can conceal an intervening identity/report change: the entire new collection must reject bad later state.
- An implicit exception context or non-BUSY SQLite code must not create retries.
- An attempt or sleep that consumes the admission budget must not allow another attempt.
- Release plans with a valid one-file delta but a foreign predecessor must still reject.
- Synthetic checked guard transitions must preserve latches, clocks, opening and all eight account identities.

### Task 1: CLI snapshot retry

**Files:** Modify `hermes_trading/supervisor_sync.py`; create `tests/test_supervisor_snapshot_retry.py`.

**Interfaces:** Preserve `make_snapshot(state_dir, *, now=None)`. Add `make_snapshot_with_retry(state_dir, *, now=None)` and private typed-classification helper; `main()` calls the wrapper. Tests may patch module monotonic/sleep for deterministic scheduling; do not add production test-only methods.

- [ ] Add a real exclusive-lock regression around the actual collector and CLI-facing wrapper. Coordinate lock release through the test's wait callback. Run it against the old implementation and record the expected missing-retry failure.
- [ ] Implement the smallest wrapper/classifier satisfying the exact approved bounds. Re-raise the final original failure when retry stops.
- [ ] Add permanent-lock, attempt/deadline/wait, explicit/implicit/cyclic-cause, fresh-file/two-gate, invalid identity/report/health/pending/global-risk and CLI schema/nonzero-exit coverage. Preserve single-attempt behavior.
- [ ] Run `python -B -X utf8 -m unittest discover -s tests -p test_supervisor_snapshot_retry.py -v` and existing snapshot/publication/safety/launch/paired checks. Record RED/GREEN terminals and source hashes.
- [ ] Commit only this task's owned source/test files after verification.

### Task 2: Exact predecessor transition purpose

**Files:** Modify `scripts/paper_release_transition.py`; create `tests/test_paper_snapshot_release_transition.py`. Reuse existing fixture APIs rather than alter shared fixtures without coordination.

**Interfaces:** Keep existing plan shape/inspection/apply API and old transition scopes. Add a purpose for predecessor source `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9`, release `fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844`, runtime-map `0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c`, exact changed runtime set `{'supervisor_sync.py'}` and unchanged watcher/native bytes.

- [ ] Write and run rejecting-before-implementation tests for the new valid purpose, foreign predecessor, other/missing/no-op deltas, watcher/native changes and mixed bindings.
- [ ] Extend purpose selection and plan validation minimally; preserve all six components and current guarded transition semantics.
- [ ] Exercise the new purpose on synthetic closed state, including inspection, apply/idempotency, wrong-root intent, partial/mixed replay and preservation of original protected evidence/latches.
- [ ] Run `python -B -X utf8 -m unittest discover -s tests -p test_paper_snapshot_release_transition.py -v` and the existing transition tests. Record terminals; commit only owned source/test files after verification.

### Task 3: Candidate review and evidence

- [ ] Review both tasks against the approved spec with an independent agent. Address material findings with a regression that fails before the correction.
- [ ] Run the affected combined checks once code is stable, and retain full-suite evidence/limitations without changing disabled archive behavior or deadlines. Do not equate a selected run with a full passing suite.
- [ ] Update the AST-only project graph and verify no extra runtime/watcher/native delta or actual-state installation occurred.
- [ ] Save a source-pinned local verification record and disabled candidate summary. Keep the original deployed release and entry halt; prepare any exact future cutover for separate approval only after local evidence is reviewable.

## Preflight and execution record

Task 1 changes a runtime hash consumed by Task 2's successor map, but Task 2 pins the immutable old map independently and accepts arbitrary valid successor digests only for the approved single-file delta. Neither task changes the other's source/test files. Source/export composition waits until both are stable. Execution evidence is retained in `research/runs/paper-supervisor-retry-v1/`.
