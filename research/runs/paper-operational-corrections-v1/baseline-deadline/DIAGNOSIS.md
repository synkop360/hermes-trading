# Existing archive deadline failure: approved-baseline comparison

The original approved `6c9d520da5f8473b51c912d1eb0e269ee94d4b1b` export reproduces the same failing test and the same `q4` quote-retention capacity deadline as the correction candidate. This demonstrates that the failure already exists without the operational correction. It does **not** turn the candidate's full run into a passing run or qualify the deferred archive/checkpoint path.

## Exact execution

Ran only:

```text
test_quote_checkpoint.QuoteCheckpointTests.test_terminal_floor_deletion_requires_raw_originals_before_initial_and_resumed_transition
```

Source root: `hermes-trading/research/runs/paper-activation-v1/release-upload-6c9d520-20261023-v2`. Interpreter: the existing main `hermes-trading/.venv/Scripts/python.exe`, with `-B`. The evidence runner is [run_baseline_deadline.py](run_baseline_deadline.py). No second baseline test execution or full-suite rerun was performed.

Started `2026-10-24T10:23:17.784798+00:00`; finished `2026-10-24T10:30:40.837742+00:00`. The process returned **exit 1**, with **one test, one error, zero assertion failures, zero skips and zero ResourceWarnings**. Wrapper elapsed time was **442.828 seconds**; unittest reports **440.586 seconds**. Both include the expensive shared class fixture; they are not the duration of the bounded `q4` operation alone.

The operation retained its original `timeout_seconds=120`, `max_bytes=64 * 1024**2`, inputs and assertions. No runtime/test source or deadline was edited. No network, model/provider call, training, backtest, deployment or live-state action was performed.

## Comparison and classification

| Run | Result | Failing operation and check |
| --- | --- | --- |
| Candidate `49c8513`, full Windows discovery | 1,639 passes, five platform skips, one error out of 1,645 | Existing quote-checkpoint test's `q4` operation |
| Candidate, isolated unchanged-case recheck | One error; 437.984 seconds including shared setup | `test_quote_checkpoint.py:620` → retention `after_cohort_complete` → capacity deadline |
| Approved `6c9d520`, original export, independent isolated run | One error; 442.828 seconds including shared setup | `test_quote_checkpoint.py:620` → retention `after_account_ack` → capacity deadline |

Both isolated traces terminate through `archive_quote_retention.py:290` (`lease.check()`), `archive_capacity.py:400` (`_measure`), line 190 (`deadline.check`) and line 76 (`CapacityUnavailable: capacity admission deadline/clock unavailable`). The baseline expires at a slightly earlier account-ack boundary, rather than the candidate's final cohort-completion boundary. This is the same bounded operation and capacity inventory path, not an identical last callback.

Static comparison confirms that the test, retention coordinator, checkpoint coordinator/state and capacity implementation are unchanged between baseline and candidate after newline normalization; their exact raw hashes are recorded in [relevant-path-comparison.json](relevant-path-comparison.json). Capacity checking repeatedly inventories the registered domain under one finite deadline, and this test has accumulated multiple complete worker copies before `q4`. That explains the relevant workload visible in the code and failure trace. The run does not isolate the contribution of individual Windows filesystem costs or establish a performance threshold for another host.

**Disposition:** classify this as a reproduced pre-existing Windows deadline limitation in the explicitly enabled test of the otherwise disabled archive/checkpoint path. Preserve the failed full-run result and the separate baseline evidence. Do not increase the timeout, weaken assertions, skip the case, enable archive/checkpoint operation, or expand this backup correction into deferred archive engineering merely to obtain a green suite.

## Source and fixture integrity

- All **513** tracked files in the original export are present and match the approved commit: **288** are raw Git-blob byte matches and **225** are CRLF-only representations of the same content. Validation normalizes only its comparison; the original export bytes were never rewritten. [Validation](approved-base-source-validation.json).
- Exactly **57** ignored historical fixtures were restored from main, only after both their SHA256 and byte length matched the saved inventory. No existing fixture was overwritten. [Restoration](fixture-restoration.json).
- All **570** execution files were hashed before and after. There are **zero changed execution files**, **zero changed original export files**, and **zero runtime/test imports from outside the original export**. The only persistent additions to that export are the 57 authorized historical fixtures. [Original pins](original-export-before.sha256.json), [before](execution-before.sha256.json), [after](execution-after.sha256.json).
- The preliminary source preflight stopped before test execution when comparing Windows bytes directly to Git blobs. Inspection established that every discrepancy was solely CRLF representation. The subsequent test preserved those exact bytes; this was not a failed or repeated test run.

The complete [structured result](baseline-single-v1.json) includes traceback, timestamps, import origins, hashes and warning capture. [Terminal log](baseline-single-v1.log) SHA256: `cf12d8ff3feb6512d5f179698a83f29febb119319470ebd4e0cdaa038e166703`. The unchanged evidence runner SHA256 at execution was `a550ccfefdc433d165dd475547c5874b76c70b1a968db4bfb61fec79d8aa9ee3`.

## Parallel read-only cutover review

Reviewed the proposed cutover, verification report and release summary while the baseline process ran. The distinction between immutable candidate content and the preserved raw deployed bytes is explicit. Independently verified all **522** staged raw pins, the exported new runtime map, release-plan/raw-pin manifest hashes and the sole old/new runtime delta `archive_snapshot.py`; zero mismatches. [Independent export check](cutover-export-independent-check.json).

Flagged and rechecked the initial worker-stop ordering ambiguity and the missing inline finite readiness command. The revised document drains supervisor/backup handles first, then stops and verifies the worker, refuses unexplained pre-existing markers, specifies the exact 1,800-second wait/exit-78/exec command, and requires both actual hosts' checks before marker publication. Closed references, retained sidecars, separate root-bound receipts and no automatic fallback remain explicit. No remaining material unsupported claim identified in that proposed procedure. The two exported native composition tests have their separate terminal receipt: two passes in 26.438 seconds, unchanged 522 raw pins and no ResourceWarnings; this is synthetic evidence only.

No successor deployment or live cutover approval is implied by this diagnosis or document review.
