# Combined delivery follow-up review

Reviewed the proposed combined event/summary fix over `8f315b35cf6a40267cc07be1bac2c9be126006c7` on October 24, 2026. Source/test files were not edited by the reviewer. All executed probes use the existing synthetic eight-account fixture and fake poster; real HTTP transport methods are blocked.

## P2 remains on combined delivery failure

The original successful-delivery bound is corrected: eight closes on the fifth batch plus its due summary now emit one four-part delivery group. However, `run()`'s local `summary_handled` flag protects only the current polling iteration. After a combined attempt exhausts its three retries, the next polling iteration has an empty event queue and calls `_due_summary()` without the event payload. That produces a new summary-only key, bypassing the abandoned combined intent.

The [saved failure probe](combined-failure-repro.py) confirms the first two combined parts and rejects the part containing `Canonical event outcomes:` with HTTP 500 on all three attempts. No new envelope is offered. After the next half-second poll, the reporter confirms three additional summary-only parts:

```text
confirmed_messages=5
confirmed_groups={combined:2, summary_only:3}
failed_attempts=3
event_markers=0
cycle_marker=1
```

The original combined key is abandoned, yet a second intent from the same input advances the cycle cadence. This still exceeds the approved four-part envelope bound and duplicates already confirmed financial content. [Fresh failing output](combined-failure-red.txt): one failure in 0.980 seconds. Preserve the combined attempt's identity across polling passes and prevent the same offered input from automatically falling back to a new summary-only intent after exhaustion. No implementation change was made during review.

## Fresh passing checks

- Original five-part reproduction now passes with **four parts, one tag**, one test in 0.234 seconds. [Output](cutover-parts-bound-followup.txt).
- Both added combined-delivery regressions pass: eight closes with the five-batch summary, and future offers arriving while the first delivery is stalled. Two tests in 0.602 seconds. [Output](combined-new-regressions-followup.txt).
- Focused legacy checks for canonical event deduplication, confirmed delivery markers/restart replay suppression, partial-success/429 retry, bounded overflow/coalescing, and uncertain cancellation pass: five tests in 0.884 seconds. [Output](combined-overflow-dedup-followup.txt).

Source inspection confirms sequence cutoffs keep offers arriving during delivery for the next dispatch, without taking a second 16-envelope list snapshot. Combined success advances event and cadence markers only after `_deliver` confirms its parts; omitted content remains disclosed by the existing gap counters. No additional material failure was established in the reviewed overflow/dedup paths. The combined partial-failure finding above must be resolved before this follow-up can close. The predecessor's 161 packaged/49 Linux receipts do not verify these uncommitted changes; final source-frozen verification remains pending.

## Final correction verified

Independently inspected the later persistent-intent correction and reran the failure reproduction. Summary keys now include the current `(kind, batch_id, input_hash, at)`, due cadence changes, and startup state. Before rendering or dispatch, the sender checks whether that base key has an abandoned combined intent. This check survives metadata reload, keeps an exhausted combined input from falling through into a second summary-only key, and permits a distinct input to recover.

Reviewed raw SHA256: `5020950cac1c807ab5864384172f43ba91d6d4ed77a42ad64a038b9abdefb0a4` for `notifications.py`; `a686a5dbb97efd090ed37919a940cc9dd20d2e81b1fddd4356566e364291b5fb` for `tests/test_paper_notifications.py`. These were source-frozen uncommitted changes over `8f315b3` during review; the final commit/export will supersede that earlier candidate.

- The original saved combined-failure probe now passes: **two confirmed parts under one tag**, three rejected event-part attempts, no event markers, no cycle marker, and no later polling fallback. One test in 0.982 seconds. [Output](combined-failure-followup.txt).
- All three new regression cases pass independently: successful close+summary combination, new offers during stalled delivery, and exhausted combined intent with later distinct-input recovery. Three tests in 1.474 seconds. [Output](combined-final-regressions-followup.txt).
- An additional independent inline restart probe cancels after the combined intent is durably abandoned, constructs a new reporter, and offers the same input. It emits **zero** messages after reload and leaves event/cycle markers unchanged. Distinct input 6 then confirms three parts and advances cycle 1; replaying that input adds zero parts. One test in 1.101 seconds. [Output](combined-restart-followup.txt).

The bounded-parts test's timing change waits for completed delivery instead of observing account text from an earlier part; its size, count, simulation, timestamp, mentions, and omitted-content assertions remain intact. No production guard or financial behavior was relaxed. Both real HTTP transport methods stayed blocked in the independent fixture probes. The reviewer made no runtime/test source edits.

The four-part bound finding and its combined-failure follow-up are resolved by the inspected correction and fresh tests. No further material finding was established in this focused review. Final source-frozen packaged/full verification remains separate and must use the successor commit/export; neither the interrupted full attempts nor predecessor receipts are passing evidence for the new bytes.
