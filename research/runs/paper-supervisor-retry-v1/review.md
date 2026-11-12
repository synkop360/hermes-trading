# Independent review of the disabled local snapshot retry candidate

Date: October 25, 2026. Reviewed base: `3fcbbed`; candidate: the current uncommitted contents of the four owned files below. Read-only product review; the only reviewer-authored file is this report. The unrelated cutover document was not edited.

## Scope and verdict

No material correctness or scope findings were identified in this bounded review. The implementation matches the approved disabled-local design. This is not deployment approval, entry-recovery approval, a full-suite passing claim, or verification of a packaged successor release.

Read `AGENTS.md`, the approved snapshot-contention spec and implementation plan, and the code-reviewer guidance. Queried the existing graph first using existing graph vocabulary; the result was truncated and was used for navigation only. Inspected the product diff, complete retry tests, new transition tests and the existing six-component/protected-state pipeline and native two-host scenario.

## Relevant evidence

- `hermes_trading/supervisor_sync.py:169`: the classifier requires an actual `sqlite3.OperationalError`, an exact integer code with BUSY base 5, and follows only explicit causes. Eight-node traversal and cycle detection bound the search; implicit contexts and message text do not admit retries.
- `hermes_trading/supervisor_sync.py:183`: the wrapper invokes the unchanged complete collector on each attempt, allows at most three attempts, requests waits of 0.2/0.5 seconds capped by remaining monotonic admission time, and re-raises the current failure when the deadline or attempt limit is reached. In-flight collection is intentionally not interrupted.
- `hermes_trading/supervisor_sync.py:209`: only CLI snapshot collection is redirected to the wrapper. Original collection checks and successful JSON shape remain intact. The diff contains no watcher/native-launcher or outer-deadline changes.
- `scripts/paper_release_transition.py:34` and `:801`: the new purpose pins the exact approved source commit, canonical complete release hash and canonical runtime-map hash, then allows exactly `supervisor_sync.py` to differ. An otherwise valid one-file diff from a foreign predecessor is rejected. Existing old-purpose branches and all six components remain unchanged.
- New retry tests exercise real SQLite contention, fresh reads after either publication gate, changed account/report/source/health/pending/risk state, direct/wrapped/implicit/cyclic/non-BUSY boundaries, admission/sleep limits, CLI success/failure behavior and preservation of a genuinely seeded sticky latch.
- The revised new-purpose tests preserve the latched synthetic closed-copy fixture for the protected-state/fault/reconcile test and use an unlatched fixture for the reused native two-host scenario, whose baseline explicitly expects missing supervisor health. The closed-copy test compares the full existing protected-state digest map, which includes the seeded latch file, along with opening and component inventory.

## Independently executed checks

Using the existing shared project interpreter at `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-trading/.venv/Scripts/python.exe`, without installing dependencies:

- `-B -X utf8 -m unittest discover -s tests -p test_supervisor_snapshot_retry.py -v`: **15 tests passed**, 3.019 seconds.
- Loaded and ran `test_paper_snapshot_release_transition.SnapshotReleaseValidationTests` with `tests` on the import path: **3 tests passed**, 0.021 seconds.
- `git diff 3fcbbed -- hermes_trading scripts` shows product changes only in the two approved owned files. Explicit diff checks for watcher, native launcher, supervisor launch and lifecycle files were empty.

The first attempt using the default system Python stopped during import because that interpreter lacks `yfinance`; no test logic ran in that attempt. The installed project interpreter above resolved that environment issue.

Source hashes were unchanged between the independent checks:

| File | SHA-256 of reviewed local bytes |
| --- | --- |
| `hermes_trading/supervisor_sync.py` | `d493060b2db48ccac5cee2eafa3d06d72e68e239ef9da90543f256d8bd0d8ad9` |
| `scripts/paper_release_transition.py` | `306da355b372cf0048b0192e7ec475d1c70740a5f60eb7ebe97b177d1c3b8ccf` |
| `tests/test_supervisor_snapshot_retry.py` | `4d0f065381acd4ec5cf7e42dd1fed0ae8d399fa4b08ea5b34793f9229301a8a0` |
| `tests/test_paper_snapshot_release_transition.py` | `f974cdf921e943494c8dbb4ed8a23cbb3831fcd222caa76c27d2ea11a27f7fe7` |

## Unverified aspects and declined judgments

- The full revised native closed-copy/two-host transition scenarios were read but not independently rerun in this review; the implementer is running them separately.
- The affected combined checks, complete regression suite, final frozen successor/source-map verification and AST graph refresh are outside this bounded review's executed evidence and remain implementation verification work. No full-suite pass is inferred from the 18 checks above.
- Actual host state, launch guards, runtime installation, credentials, provider calls, restarts, real readiness/latch changes and live recovery/success rate were deliberately excluded because authorization permits disabled local implementation and synthetic checks only. No such actions were taken by this review.
- Interrupting an in-flight file read at two seconds was not judged as a missing feature: the approved design explicitly makes this an admission/waiting boundary, retaining existing hard process deadlines.
- The existing scopes of older transition purposes and broader archive behavior were not expanded; they are unchanged by this patch and not new behavior authorized by this repair.

The reviewed product changes can proceed to completion of the required local integration evidence. Any product or owned-test change after these recorded hashes requires its corresponding review/verification update.
