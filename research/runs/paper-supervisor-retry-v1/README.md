# Disabled local supervisor snapshot repair

October 25, 2026. Program candidate **`a79fb936fd970eb9b25a23edf6522a2e34f6e17f`** and the corrected packaging candidate **`20eb6af2a441c934618e124af30d72f411a3938a`** are local only. The latter adds original test inputs and byte-preservation metadata without changing program/test code. The deployed Discord release remains `ed439e8`; no actual state, guard, schedule, readiness marker, clock, risk limit or entry halt was changed by this implementation.

The owner [approved disabled local implementation](owner-approval.json) of the [reviewed design](../../../docs/superpowers/specs/2026-10-25-supervisor-snapshot-contention-design.md). Deployment and latch recovery are excluded. [Plan](../../../docs/superpowers/plans/2026-10-25-supervisor-snapshot-contention.md).

Local correctness qualification is complete through composed evidence covering all1,712catalog IDs. The original full failed run remains preserved. [Final verification](VERIFICATION.md). The current [morning observation](FORWARD_OBSERVATION_20261026.md) confirms187reporting windows and the retained halt; deployment is still unapproved.

## Implemented behavior

Only snapshot CLI collection gains a retry wrapper. It permits at most three complete fresh collections, waits 0.2/0.5 seconds capped by a two-second monotonic retry-admission boundary, and retries only an actual numeric SQLite BUSY cause. The original collector, both publication gates, all source/account/report/health checks, successful JSON and outer process deadlines remain intact. Permanent contention and invalid state still reject. Retry success does not clear the sticky supervisor halt or invoke a model.

The operator helper has a separate purpose pinned to the exact deployed predecessor/full-release/runtime-map hashes. It permits only `supervisor_sync.py` in the runtime delta, leaves watcher/native bytes unchanged, and retains all six guarded components and root-bound intents. Its tests use synthetic closed state; none of those transitions was applied to actual hosts.

Code commits: `c726cfa` (collector wrapper/tests), `44d000c` (scoped transition/tests), `cda8967` (approved plan), `a79fb93` (explicit synthetic receiver clock). The last commit changes a test fixture only; product bytes reviewed independently remain unchanged.

The [20:24 UTC read-only forward observation](FORWARD_OBSERVATION_20261025.md) verifies the deployed reporting runtime and original eight account hashes, 84 confirmed summary windows, no recorded delivery gaps and all eight accounts flat with no orders. Volume is 35.7% of declared capacity. The retained supervisor latch is still the sampled entry block despite a recent successful acknowledgment. The common journal returned BUSY, so no freshly qualified paired financial comparison or opening proof is claimed. No actual operation was changed.

## Verification

[Final verification and limitations](VERIFICATION.md), [machine audit](composed-verification.json), [per-case evidence](composed-case-evidence.json).

- The original full Windows run executed1,712cases:1,698passes, nine missing-input errors and five platform skips, no unrun cases and unchanged376source pins. The failed result and one cleanup warning are retained.
- All11restored-input cases pass on the final package, covering exactly the nine original errors and two overlapping passes. No failures/errors/skips/ResourceWarnings after collection; all667raw pins unchanged.
- All16selected Linux cases pass: the eleven restored cases plus every Windows-skipped case, with no network, read-only source and an unprivileged user. The original FIFO two-second deadline remains unchanged. Its first harness import failure is preserved.
- All20new repair/transition cases pass on the final Windows package. The earlier21-case Linux repair run covers the same program bytes.
- The exact union is1,707distinct Windows passes plus five Linux platform passes, covering all1,712catalog IDs. **This is composed evidence, not one fresh passing full suite or a full Linux suite.**

The original temporary-directory warning is retained. A negative control reproduces that warning when missing-input setup fails before cleanup ownership transfers; recovered tests emit none. The exact historical temporary-object identity is not proved. Independent product/provenance/composition reviews remain scoped to their recorded evidence; the final harness follow-up and machine audit are implementer-authored. No model, paid service, training, market backtest, real order or actual-state migration occurred.

## Exact package

The [current package record](candidate-package-verification-v4.json) verifies the prior603 raw files and all57 historically restored inputs, freezes667 files from the now-durable candidate and retains all129 runtime names. Exactly one runtime file changes from the deployed predecessor: `supervisor_sync.py`. Raw hashes of the unchanged watcher and native launcher match. The short sibling package path avoids Windows long-path failures. Earlier package records remain preserved; the original full run continues on its unchanged608-file package. The current667-file package has completed composed correctness verification; see the final record below.

Package: `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-snapshot-20eb6af`.

New supervisor release: `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7`.

New runtime-map hash: `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2`.

The [release plan](candidate-release-plan-v4.json) is a local checked input, not authority to apply it. No old-state rollback, latch reset, account initialization or readiness change is included.

## Remaining work

The disabled local implementation is ready for owner review of the [paper-only cutover](../../../docs/PAPER_SUPERVISOR_SNAPSHOT_REPAIR_CUTOVER.md). Deployment, actual guard migration and PC installation remain unapproved. The existing entry halt stays set; later recovery needs a separately checked and authorized procedure. Correctness checks do not establish a winning strategy, LLM learning outcome, profitable EUR50–200 wallet or improved returns. Overall goal estimate remains65%.
