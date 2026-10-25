# Disabled local supervisor snapshot repair

October 25, 2026. Candidate **`a79fb936fd970eb9b25a23edf6522a2e34f6e17f`** is local only. The deployed Discord release remains `ed439e8`; no actual state, guard, schedule, readiness marker, clock, risk limit or entry halt was changed by this implementation.

The owner [approved disabled local implementation](owner-approval.json) of the [reviewed design](../../../docs/superpowers/specs/2026-10-25-supervisor-snapshot-contention-design.md). Deployment and latch recovery are excluded. [Plan](../../../docs/superpowers/plans/2026-10-25-supervisor-snapshot-contention.md).

## Implemented behavior

Only snapshot CLI collection gains a retry wrapper. It permits at most three complete fresh collections, waits 0.2/0.5 seconds capped by a two-second monotonic retry-admission boundary, and retries only an actual numeric SQLite BUSY cause. The original collector, both publication gates, all source/account/report/health checks, successful JSON and outer process deadlines remain intact. Permanent contention and invalid state still reject. Retry success does not clear the sticky supervisor halt or invoke a model.

The operator helper has a separate purpose pinned to the exact deployed predecessor/full-release/runtime-map hashes. It permits only `supervisor_sync.py` in the runtime delta, leaves watcher/native bytes unchanged, and retains all six guarded components and root-bound intents. Its tests use synthetic closed state; none of those transitions was applied to actual hosts.

Code commits: `c726cfa` (collector wrapper/tests), `44d000c` (scoped transition/tests), `cda8967` (approved plan), `a79fb93` (explicit synthetic receiver clock). The last commit changes a test fixture only; product bytes reviewed independently remain unchanged.

## Verification

| Evidence | Result and scope |
| --- | --- |
| [Independent review](review.md) | No material findings. Reviewer independently ran 15 retry and three plan-validation checks. Its recorded test-file hash predates the explicit receiver-clock fixture correction; product hashes are unchanged. |
| [Affected Windows run](verification/affected-windows-v1.result.json) | 140 cases executed: **139 passed, one Windows symlink skip**, zero failures/errors/ResourceWarnings, all 376 source pins unchanged. This run predates the test-only clock correction. |
| [Original exact package](exact-package-verification.json) | All 20 new checks passed against the earlier `cda8967` byte-preserving package, with no package file changes. |
| [First Linux run](linux-verification.json), [log](linux-tests.log) | 21 executed, 20 passed and one fixture error: simulated health timestamps exceeded the real receiver clock on the faster Linux host. No runtime defect or safety-limit relaxation followed from that error. |
| [Corrected exact Linux package](linux-verification-v3.json) | **21 passed**, zero skips/failures/errors, CPython 3.11.17, cached image, network disabled, unprivileged user and read-only source. The real Linux symlink case passes. All 608 package files remain unchanged. |
| [Corrected exact Windows package](exact-package-verification-v3.json) | **20 passed** at the final `a79fb93` package. All 608 files remain unchanged and the imported 129-file runtime map matches the frozen release plan. |
| Full frozen Windows regression | Running after successful package acceptance, using `run_frozen_correctness.py`. At 20:17 UTC, 25 cases had completed with no failures/errors; this is not a terminal result or a full passing-suite claim. Results will be under `verification/full-frozen-windows-v1.*`. |

The regression runner denies external HTTP through httpx while retaining loopback test servers. These are synthetic correctness checks, not a market backtest, training, provider inference or profit evidence. The historical Discord full run retained two disabled-archive fixture deadline errors and 30 unrun cases; the new full run must be reported on its own merits, without relaxing deadlines or expanding archive work.

## Exact package

The [final package record](candidate-package-verification-v3.json) verifies the prior 603 raw files, freezes 608 files from the candidate and retains all 129 runtime names. Exactly one runtime file changes: `supervisor_sync.py`. Raw hashes of the unchanged watcher and native launcher match the deployed predecessor. The short sibling package path avoids Windows long-path failures; the required new-test input is included. Earlier incomplete packaging records are preserved and superseded explicitly.

Package: `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-snapshot-a79fb93`.

New supervisor release: `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7`.

New runtime-map hash: `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2`.

The [release plan](candidate-release-plan-v3.json) is a local checked input, not authority to apply it. No old-state rollback, latch reset, account initialization or readiness change is included.

## Remaining work

Finish the active exact-package/full regression checks, record their terminal outcomes and source invariance, and prepare a concrete reviewed cutover before asking for separate deployment approval. Later entry recovery needs a separately checked and authorized procedure; fixing snapshot contention alone will not resume entries. Overall goal estimate remains **65%**, with no profitable strategy, LLM learning outcome or minimum profitable EUR50–200 wallet established.
