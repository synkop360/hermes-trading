# Snapshot repair — final local verification

Finalized October26,2026. Candidate **20eb6af2a441c934618e124af30d72f411a3938a**, local package `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-snapshot-20eb6af`. No deployment, actual PC installation, state/guard migration, risk change, paid inference, real order or halt clearing occurred.

**All1,712 catalog cases have passing correctness evidence through composed runs. This is not one fresh passing full-suite run.** The original full run remains recorded as failed. [Machine audit](composed-verification.json), [per-case evidence](composed-case-evidence.json).

| Evidence | Result |
| --- | --- |
| Original full Windows discovery | All1,712 unique cases executed:1,698passes, nine missing-input errors, five platform skips, no unrun cases and unchanged376source pins. One implicit temporary-directory cleanup warning was retained. [Original result](verification/full-frozen-windows-v1.result.json). |
| Restored historical-input cases on the final package | All11cases pass, covering exactly the nine prior errors plus two overlapping passes. Zero failures/errors/skips/ResourceWarnings after forced garbage collection; all667raw package pins unchanged. [Result](verification/restored-fixtures-windows-v1.result.json). |
| Linux restored-input/platform coverage | All16cases pass: the same eleven cases plus all five Windows-skipped cases. CPython3.11.17, immutable cached image, read-only source, unprivileged user and no network. All667files unchanged. FIFO child retains its original two-second timeout. [Result](linux-verification-v5.json). |
| Final exact-package repair checks | All20new retry/transition cases pass on the final667-file Windows package; its imported129-module map matches the frozen release plan. [Result](exact-package-verification-v4.json). |
| Earlier affected checks |139passes/one Windows platform skip, no failures/errors/ResourceWarnings; all product bytes remain unchanged since that run. It predates a test-only synthetic receiver-clock correction. [Result](verification/affected-windows-v1.result.json). |

The exact union is **1,707distinct Windows-passing case IDs plus five Linux-passing platform cases**, with no uncovered catalog ID. The selected Linux sixteen-case run is not a full Linux suite. The earlier21-case Linux repair run remains additional evidence for the same program bytes.

## Input and source fidelity

The nine original errors are all `FileNotFoundError` in the three historical-fixture modules. Exactly57original inputs/67,167bytes were present locally but ignored and absent from the prior source package. Their dated October23 inventory matches three independent checkout copies; commit20eb6af stores those exact bytes with narrowly scoped newline-preservation attributes. No market data, approvals, strategies or expected values were regenerated. [Independent provenance review](historical-fixture-provenance-review.md), [tracking verification](historical-fixture-tracking-verification.json).

The final667-file package preserves all608original inventoried files byte-for-byte and adds only the57inputs, their original inventory and Git attributes. The original full run generated additional bytecode/scratch artifacts; the unchanged-source statement applies to its608original raw files and376recorded source pins, not to an unchanged entire directory tree. The shipping package remains its own verified667-file tree. [Overlay audit](candidate-overlay-verification-v4.json).

The original cleanup warning is preserved. A negative control reproduced exactly one implicit cleanup warning when the same fixture setup fails before its caller can transfer cleanup ownership; explicit cleanup emits none. The restored eleven-case run emits none after collection. This supports the failure-pattern explanation without proving the identity of the historical temporary object. [Negative control](missing-fixture-cleanup-negative-control.json).

The first Linux16-case attempt failed because the FIFO test's child process ran from `tests/` without the project import path. Its failure is retained. The final harness propagates `PYTHONPATH=/workspace:/workspace/tests`; no product/test source or deadline was changed. [Failed run](linux-verification-v4.json), [corrected run](linux-verification-v5.json).

## Review and release boundary

Independent review found no material issue in the retry program; a separate independent composition audit verified catalog identity, restored-case mapping, raw source equality, dependency provenance and release maps. That composition review predates the final harness result and negative control; the final machine audit and this qualification are implementer-authored. No independent review of a different product implementation is implied. [Product review](review.md), [composition review](verification-composition-review.md).

The intended runtime delta remains exactly `supervisor_sync.py` out of129files. The watcher, native launcher, strategies, fees, original clocks, eight wallets and risk limits remain unchanged. Both-host six-component transition logic is restricted to the exact deployed predecessor. Final release `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7`; runtime map `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2`.

The local work is ready for separate owner review of the [paper-only cutover](../../../docs/PAPER_SUPERVISOR_SNAPSHOT_REPAIR_CUTOVER.md). Read-only morning observation confirms the existing reporting runtime is still installed; it does not authorize changing it. **The supervisor entry halt remains set, and this deployment would retain it.** A later checked recovery procedure needs its own authorization. No winning strategy, profitable wallet threshold or improved returns are established by these correctness checks.
