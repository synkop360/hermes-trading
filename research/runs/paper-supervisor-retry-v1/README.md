# Disabled local supervisor snapshot repair

October 25, 2026. Program candidate **`a79fb936fd970eb9b25a23edf6522a2e34f6e17f`** and the corrected packaging candidate **`20eb6af2a441c934618e124af30d72f411a3938a`** are local only. The latter adds original test inputs and byte-preservation metadata without changing program/test code. The deployed Discord release remains `ed439e8`; no actual state, guard, schedule, readiness marker, clock, risk limit or entry halt was changed by this implementation.

The owner [approved disabled local implementation](owner-approval.json) of the [reviewed design](../../../docs/superpowers/specs/2026-10-25-supervisor-snapshot-contention-design.md). Deployment and latch recovery are excluded. [Plan](../../../docs/superpowers/plans/2026-10-25-supervisor-snapshot-contention.md).

## Implemented behavior

Only snapshot CLI collection gains a retry wrapper. It permits at most three complete fresh collections, waits 0.2/0.5 seconds capped by a two-second monotonic retry-admission boundary, and retries only an actual numeric SQLite BUSY cause. The original collector, both publication gates, all source/account/report/health checks, successful JSON and outer process deadlines remain intact. Permanent contention and invalid state still reject. Retry success does not clear the sticky supervisor halt or invoke a model.

The operator helper has a separate purpose pinned to the exact deployed predecessor/full-release/runtime-map hashes. It permits only `supervisor_sync.py` in the runtime delta, leaves watcher/native bytes unchanged, and retains all six guarded components and root-bound intents. Its tests use synthetic closed state; none of those transitions was applied to actual hosts.

Code commits: `c726cfa` (collector wrapper/tests), `44d000c` (scoped transition/tests), `cda8967` (approved plan), `a79fb93` (explicit synthetic receiver clock). The last commit changes a test fixture only; product bytes reviewed independently remain unchanged.

The [20:24 UTC read-only forward observation](FORWARD_OBSERVATION_20261025.md) verifies the deployed reporting runtime and original eight account hashes, 84 confirmed summary windows, no recorded delivery gaps and all eight accounts flat with no orders. Volume is 35.7% of declared capacity. The retained supervisor latch is still the sampled entry block despite a recent successful acknowledgment. The common journal returned BUSY, so no freshly qualified paired financial comparison or opening proof is claimed. No actual operation was changed.

## Verification

| Evidence | Result and scope |
| --- | --- |
| [Independent review](review.md) | No material findings. Reviewer independently ran 15 retry and three plan-validation checks. Its recorded test-file hash predates the explicit receiver-clock fixture correction; product hashes are unchanged. |
| [Affected Windows run](verification/affected-windows-v1.result.json) | 140 cases executed: **139 passed, one Windows symlink skip**, zero failures/errors/ResourceWarnings, all 376 source pins unchanged. This run predates the test-only clock correction. |
| [Original exact package](exact-package-verification.json) | All 20 new checks passed against the earlier `cda8967` byte-preserving package, with no package file changes. |
| [First Linux run](linux-verification.json), [log](linux-tests.log) | 21 executed, 20 passed and one fixture error: simulated health timestamps exceeded the real receiver clock on the faster Linux host. No runtime defect or safety-limit relaxation followed from that error. |
| [Corrected exact Linux package](linux-verification-v3.json) | **21 passed**, zero skips/failures/errors, CPython 3.11.17, cached image, network disabled, unprivileged user and read-only source. The real Linux symlink case passes. All 608 package files remain unchanged. |
| [Corrected exact Windows package](exact-package-verification-v3.json) | **20 passed** at the final `a79fb93` package. All 608 files remain unchanged and the imported 129-file runtime map matches the frozen release plan. |
| Full frozen Windows regression | The original 608-file run remains active in session42772. At20:46 UTC it records1,275 cases and three errors; it is not a passing run. An isolated reproduction proves a missing historical input before venue-review setup reaches runtime code. The run/package are left intact; terminal traces and all unrun cases must be inspected. Results are under `verification/full-frozen-windows-v1.*`. |
| [Historical input restoration](historical-fixture-provenance-review.md) | All57 original inputs/67,167bytes match their dated October23 inventory in three checkouts. They were ignored/untracked and omitted from the old package. Commit20eb6af now stores their exact original bytes; no data regeneration or test/check change is used. |
| [New package overlay](candidate-overlay-verification-v4.json) | The new667-file package retains every prior608file byte-for-byte and adds only57inputs, their raw inventory and exact-path Git attributes. Both runtime/release maps are identical to the reviewed retry program. The formerly failing [fixture setup and activation-copy stages pass](venue-fixture-stage-diagnostic-v4.json); the eleven dependent test bodies remain queued until the original full run is terminal. |

The regression runner denies external HTTP through httpx while retaining loopback test servers. These are synthetic correctness checks, not a market backtest, training, provider inference or profit evidence. The historical Discord full run retained two disabled-archive fixture deadline errors and 30 unrun cases; the new full run must be reported on its own merits, without relaxing deadlines or expanding archive work.

## Exact package

The [current package record](candidate-package-verification-v4.json) verifies the prior603 raw files and all57 historically restored inputs, freezes667 files from the now-durable candidate and retains all129 runtime names. Exactly one runtime file changes from the deployed predecessor: `supervisor_sync.py`. Raw hashes of the unchanged watcher and native launcher match. The short sibling package path avoids Windows long-path failures. Earlier package records remain preserved; the original full run continues on its unchanged608-file package. The current667-file test composition is not yet fully verified.

Package: `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-snapshot-20eb6af`.

New supervisor release: `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7`.

New runtime-map hash: `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2`.

The [release plan](candidate-release-plan-v4.json) is a local checked input, not authority to apply it. No old-state rollback, latch reset, account initialization or readiness change is included.

## Remaining work

Finish the active full regression, inspect every terminal error/unrun case and source-invariance result, then run the eleven restored-input cases and resolve any other failure before finalizing the [proposed cutover](../../../docs/PAPER_SUPERVISOR_SNAPSHOT_REPAIR_CUTOVER.md). The earlier program-only Windows/Linux package checks are terminal and passing; they do not establish complete test-input packaging. The cutover is a document-only proposal and has not been executed. Later entry recovery needs a separately checked and authorized procedure; fixing snapshot contention alone will not resume entries. Overall goal estimate remains **65%**, with no profitable strategy, LLM learning outcome or minimum profitable EUR50–200 wallet established.
