# Independent review: paper Discord reporting

Reviewed October 24, 2026 against base `49c851315b98bb33b1eb78180fb789afe2a49f3d` and candidate `bceeea6ccb17e835b313b8fbe32b03428aa8e052` (reporter implementation `85f7856`). This is a source/patch review of the four runtime modules and the narrow successor admission in `scripts/paper_release_transition.py`, using the approved design and implementation plan. The review found three reproducible P2 issues requiring correction before the candidate satisfies that design.

The reviewer used the required graph query, inspected the full reporter and native capture/worker/bootstrap changes, traced native valuation and event fields, and exercised native fixtures/fake IO. No runtime source or tests were edited, no live state was inspected or modified, and no deployment, restart, Discord request, paid call, or private credential access occurred. All runtime/script bytes still matched the candidate at review completion. Research artifacts are ignored by the repository unless explicitly added.

## P2: Archive-outage protective closes never reach the reporter

**Location:** `hermes_trading/paired_worker.py:422` (alternate `archive_protect` invocation of `run_paired_cycle`).

The ordinary worker path enables `notification_capture` and retains the returned capture, but the archive-outage/replay path neither passes that option nor retains the result. Once this path closes positions, its heartbeat is consequently offered as an operational observation with empty accounts. The real financial close events are absent; subsequent acknowledgement/restart does not recover them through the bounded ephemeral capture contract. This affects the explicitly supported optional archive-protection mode; the current installed archive mode remains disabled.

Reproduction uses the existing `ProtectiveWorkerTests` signed disposable receiver and actual worker/settlement. A normal batch opens the positions, the receiver becomes unavailable, and the native protective batch closes all eight. Actual offers are:

```text
operational completed_batches=0 close_count=0 error=worker_starting
settled     completed_batches=1 close_count=0 error=None
operational completed_batches=2 close_count=0 error=archive_acknowledgement_pending
```

All eight canonical positions are closed and the worker is `waiting_for_archive`, but the assertion requiring a settled batch-2 envelope with eight closes fails. Fresh saved run: one failure, 4.019 seconds, strict ResourceWarning mode. [Full output](independent-review-archive-red.txt).

Retain optional capture in this invocation and hand it off on both acknowledgement-pending and successful reconciliation paths. Preserve the original protection behavior and connection/lease boundary; do not reconstruct historical events in the sender.

## P2: Default-executor metadata IO defeats the two-second process shutdown bound

**Locations:** `hermes_trading/notifications.py:810` (`asyncio.to_thread(self._persist)`), `hermes_trading/notifications.py:823` (same mechanism for `_load`), and `hermes_trading/paper_boot.py:82` (`asyncio.run`).

The reporter can cancel within its own bound while a metadata operation continues in the default executor. The entrypoint's `asyncio.run` then waits for that executor during process shutdown. A stalled filesystem operation therefore delays termination beyond the promised two seconds, potentially indefinitely, even though `run_paper`'s `asyncio.wait(..., timeout=2)` has finished. This does not block protection during normal operation; it violates the terminal shutdown contract after trading work has unwound.

The synthetic probe replaces only `_persist` with a finite three-second fake IO stall and uses the same `asyncio.run` entrypoint semantics. No HTTP dispatch occurs. Fresh result:

```text
run_paper_style_cleanup_seconds=0.422 reporter_task_done=true
asyncio_run_total_seconds=3.016 promised_shutdown_bound_seconds=2
AssertionError: asyncio.run waited for stalled metadata executor thread after reporter cancellation
```

[Full output](independent-review-shutdown-red.txt). Bound the lifecycle of metadata IO independently of the event loop's default executor and cover terminal process exit in a subprocess regression, in addition to checking that the event loop stays responsive.

## P2: Truncated native provenance hides estimated costs

**Location:** `hermes_trading/notifications.py:911` (`_paper_text(obs.get('cost_provenance', 'unknown'), 64)`).

Native `cost_provenance` is a map of AI, funding, hosting, and tools records, each with source, quality, and amount. Serializing this whole map and truncating it to 64 characters retains only the first AI record prefix. Other categories' actual/estimated/missing quality is lost even with ordinary short source strings, so a wallet with estimated hosting costs displays only `quality: actual` from AI. This misses the approved requirement to present costs with their provenance.

The probe sets only the native fixture's hosting quality to `estimated`, runs real `run_paired_cycle`, verifies that native capture preserves the mixed provenance, then renders the actual summary. The cost line is:

```text
costs={"ai": {"source": "explicit_test_zero", "quality": "actual", "am missing=[] ...
```

The assertion requiring the estimated quality fails. Fresh saved run: one failure, 1.169 seconds, strict ResourceWarning mode. [Full output](independent-review-costs-red.txt). Render a bounded per-category quality summary before any truncated source detail, preserving all eight rows and four comparisons within the part limits.

## Reproduction and scope limits

The self-contained [probe script](independent-review-repro.py) provides `archive`, `shutdown`, and `costs` cases. From the repository root in PowerShell:

```powershell
$env:PYTHONPATH='tests;.'
& '../hermes-trading/.venv/Scripts/python.exe' -B -X utf8 -W error::ResourceWarning research/runs/paper-discord-reporting-v1/independent-review-repro.py archive
# Repeat with shutdown or costs.
```

All three intentionally exit 1 against the reviewed candidate. Both real HTTP transport methods are patched to reject access. The archive fixture uses its in-process disposable signed receiver, with no external network.

No further material issue was established in the reviewed release-admission changes or the inspected accounting/transport boundaries. That statement is limited to this scoped review and these reproductions; it is not blanket approval from existing test counts. The root's full Windows run and known baseline archive-capacity deadline error were not independently rerun here. The three findings have been sent to the implementer; no fixes or follow-up verification are included in this report.
