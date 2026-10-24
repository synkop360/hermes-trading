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
