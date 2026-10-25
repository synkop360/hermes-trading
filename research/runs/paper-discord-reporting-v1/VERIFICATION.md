# Disabled paper Discord reporting verification

Current candidate: **ed439e897a4dd388fdcf6425e7e8b6171d5a88a9**, based on installed `49c851315b98bb33b1eb78180fb789afe2a49f3d`. This supersedes `8f315b3`. These verification results cover design, local implementation and synthetic checks. The owner subsequently authorized the reviewed paper-only deployment and real webhook activation on October 25; see live-cutover/owner-approval.json. No deployment, live restart or Discord request occurred during local verification. The subsequent owner-approved deployment and real webhook acceptance are recorded in [the actual result](LIVE_CUTOVER_RESULT.md). No paid call, private exchange use, backtest, training, trading change or profitability qualification was introduced.

## Native correctness and independent review

The [final new suite](combined-final-green.txt) passes **47/47** in 36.100 seconds:32 reporter, 12 native capture/composition and 3 exact-release transition cases. Tests cover final native EUR observations, all eight wallets/four comparisons, partial/risk/archive closes, missing/estimated costs, replay, stalled HTTP with continued protective processing, bounded process shutdown and checked state-preserving transitions.

The original absent-hook [RED](capture-red.txt) and [initial GREEN](capture-green-first.txt) are preserved. Expanded fixtures exposed wrong test assumptions about shared wallet quantities and partially delivered reports; corrected tests compare actual per-wallet quantity and the full rendered bounded intent. No production financial guard changed.

[Independent review](independent-review.md) reproduced and verified fixes for omitted archive-protection capture, default-executor shutdown delay and cost-quality truncation. Native child-process tests use asyncio.run, rather than claiming cancellation alone proves process exit. Marker filesystem operations use one outstanding daemon thread, never the default executor; an OS call cannot be cancelled but holds no financial lease or process exit hostage.

[Combined-event review](combined-followup-review.md) then reproduced a five-part input despite the approved four-part bound, plus repeated summary follow-up after combined failure. Current source uses one combined event/summary report, fixed queue sequence cutoff, confirmed-only marker advancement and durable abandoned combined-intent identity. Three added regressions verify success, partial failure and restart/new-input recovery. Limits remain 16 queued events plus one in flight, 128 KiB/envelope, 64 KiB metadata, 1900 characters/part, four parts, three ten-second attempts and two-second shutdown grace. Delivery uncertainty and excess/gaps remain explicit.

## Exact deployable bytes

[Export verification](export-verification.json) checks all 522 installed-predecessor raw pins and preserves 516 previous files exactly. Four changed runtime digests are notifications.py, paired_paper.py, paired_worker.py and paper_boot.py; 125 others, watcher and native launcher remain byte-identical. Runtime inventory remains 129 modules. The operator helper and an old plan document are the other changed predecessor files. All 603 exported files match immutable `ed439e8` after newline normalization.

Use only **release-upload-ed439e8**, bound by [603 raw pins](release-upload-ed439e8.raw-sha256.json), manifest hash f4f5ce336e1be6f955dff901f9ea345e98584c80cebfd7dc50df871ca70c7d99. A fresh Windows checkout changes unrelated raw digests through line endings. Earlier 8f exports/results remain historical; its unsuffixed export is incomplete after a path-length failure.

The [release plan](proposed-release-plan.json), rawSHA f191fc0efdc85bd9d454d823cdcfd63939a73e271affafdf314ca175327471dd, binds supervisor release fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844 and runtime map 0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c. Six checked guard transitions, leases, preserved economics/history checks, reconciliation and post-work rollback refusal remain enforced.

## Current exact-export Windows coverage

[Packaged result](packaged-verification-ed439e8/packaged-verification-results.json): **164/164 passed**, zero failures/errors/skips/ResourceWarnings, 818.812 seconds test time / 822.985 total. All 603 before/after raw pins match, no extra source files appear and imported runtime/scripts resolve inside the export. HTTP transport guards cover the parent and all three recorded native child interpreters; no external request occurred. Groups include notifications, paired settlement/risk/worker, boot, protective outage behavior and all 19 previous transition checks, including interruption at every checked write.

The old 8f [161-case receipt](packaged-verification/receipt.md) is superseded by the current 164-case result and is not evidence for final ed439e8 bytes.

## Linux scope and explicit harness qualification

The first final ed439e8 [52-case run](linux-verification-ed439e8/verification.json) passed 51 but failed the metadata child's outer eight-second startup deadline. A [single-case recheck](linux-metadata-recheck-ed439e8/verification.json) also timed out during imports. Those failures remain evidence; a generic harness arithmetic field in the recheck mishandles multiple subtest errors and is not a meaningful passed-test count.

[Import diagnostics](linux-child-diagnostic/stdout.log) measured approximately 9–11seconds importing test helpers/CCXT before the shutdown scenario starts. Native shutdown itself took 0.009/0.405 seconds, within the unchanged two-second assertion.

The independently reviewed [qualified rerun](linux-verification-ed439e8-harness-review/verification.json) passes **52/52**, zero failures/errors/skips/ResourceWarnings, with all 603 pins unchanged and no foreign imports. Only a precisely matched external harness wrapper increases the _load/_persist child outer startup allowances from 8 to 30 seconds. Pinned source/test bytes, fake three-second stall and actual shutdown below two seconds assertion are unchanged. Child wall times 10.139/10.378 seconds; actual shutdown 0.004/0.408 seconds. [Harness review and exact qualification](harness-review.md).

This uses the cached image/CPython 3.11.17, UID 65534, read-only source, no network, no download and disposable synthetic state. The owned container is confirmed removed. It is selected platform coverage, **not full Linux coverage or an unmodified-deadline pass**. Historical 8f 49-case results and the earlier 128 MiB tmpfs failure/stop remain preserved; no product reserve or risk guard was changed.

## Full Windows discovery

Final source-frozen discovery is terminal: [raw receipt](full-regression/discord-full-windows-v3.verification.json), exit 1. Of **1,692 catalog cases**, **1,662 executed: 1,657 passed and five were skipped**. Two separate class-fixture errors prevented **30 cases from running**; there were no individual case failures/errors or ResourceWarnings. All 374 source/test/script/config/build pins remained unchanged. Test time was 4,695.410 seconds; receipt elapsed time 4,702.984 seconds. All 47 new cases passed in this full invocation.

The raw runner computes passed=testsRun-errors-skips and therefore reports 1,655: its two setup-error holders are not counted in testsRun. That field undercounts executed passes by two. The original receipt is preserved unchanged. The [independent terminal classification and all 30 absent IDs](quote-checkpoint-setup-review-20261025.md) reconcile catalog membership, executed passes, skips and fixture errors separately.

Both setup errors are CapacityUnavailable('capacity admission deadline/clock unavailable') from the existing lease capacity guard: checkpoint setup q3 while staging an original artifact, and control-segment setup q2 before an account commit. The 12 inspected test/archive files have identical raw Windows pins to the predecessor and frozen current run. Notification capture is inactive on this path; small worker identity/heartbeat integration still executes, so general timing equivalence or environmental causation is not asserted. No reporting regression is established by these traces, but this is **not a passing full suite** and supplies no current execution coverage for the 30 missing cases. No deadline, assertion, guard or source was changed to force a pass. Archive/checkpoint features stay disabled; fixing or qualifying them is outside this reporting cutover.

Earlier v1/v2 were interrupted for review fixes and do not establish passing full coverage. V2 also exposed an overbroad HTTP test guard that rejected a synthetic loopback server. [Harness RED/GREEN](full-regression/loopback-harness-verification.json) verifies the original failure and current allowance for literal 127.0.0.1/::1 only; external HTTP remains denied. No product networking behavior changed.

The approved 49c predecessor's full run executed 1,645 tests: 1,639 passes, 5 POSIX-only skips and one preexisting disabled-archive quote-checkpoint capacity deadline error. The same case fails on original 6c9. Preserve that limitation; this repair changes no deadline or safeguard and does not claim a passing full suite from affected checks.

## Installed status and deployment boundary

At the pre-cutover verification snapshot, the installed release was49c8513. The separate October 25 [read-only observation](../paper-operational-corrections-v1/live-cutover/observe_reporter_candidate_20261025.native-result.json) verifies all 129 installed raw hashes, original opening/manifest/account bindings, 1,651 completed batches and zero orders/positions. Primary adaptive/control net equity is EUR 199.638030, with EUR0.361970 estimated hosting. Worker heartbeat is healthy, but **supervisor_error_halt blocks new entries** after three failures, despite later successful supervisor checks. No latch was reset. This separate fault is not resolved by notifier correctness.

The owner has now authorized [deployment, checked both-host transitions and first real webhook messages](../../../docs/PAPER_DISCORD_REPORTING_CUTOVER.md). Preserve all eight scenarios, EUR 200 primary, original clocks/history/controls/fees/risk and sticky latches. The historical six-hour continuity failure remains. No profitable trade, improved return or strategy edge is established.

[Local-only supervisor diagnosis](supervisor-halt-diagnostic-20261025.md) confirms intermittent cron child failures and a three-failure streak; later successes preserve the latch. The native launcher discards child output and normalizes nonzero exit codes, so the underlying exception and provider/transport cause remain unknown. No manual watcher, model call, restart or latch reset was used to investigate it.

A subsequent single [bounded read-only native database inspection](watcher-audit-readonly-20261025.json) succeeds and finds no tables/views: the watcher database is only an exclusive lease, not an audit store. Static persistence review finds overwritten current lifecycle/status and one pending health delivery, with no retained historical stage or exception. No retry, migration or live write was performed.

The [October 25 E: copy verification](daily-copy-verification-20261025.json) checks 525 original files / 149,572,282 bytes against the native inventory, receipt, original manifest/opening and all eight account identities; no SQLite sidecars exist. Fresh task stdout reports completed/off_host_verified and the scheduler has result0. The copy was captured 00:00:26 UTC. Verification was read-only and made no network request or restore. Two preliminary analysis-only path checks failed on Windows extended-prefix equality/SQLite URI handling; comparing filesystem identity and normalizing the verified prefix for SQLite resolved the harness issues without modifying any copied byte, product source or guard.

The final AST-only graph update is [recorded separately](graph-update-ed439e8.txt): 6,339 nodes / 21,946 edges, with no model/API call. Configuration/data files without structural nodes remain an extraction limitation.

The existing unmodified [all-eight operator report](forward-report-20261025/README.md) succeeds on the verified copy without replaying market decisions. Its final common observation at October 25 00:00:24UTC has primary net equity EUR 199.733756, EUR0.266244 estimated standalone hosting per scenario, zero orders/positions/closes and four adaptive-minus-control differences of zero. Each account has72 recorded no-trade decisions. Rolling30-day metrics remain incomplete; learning is not observed. This older copy precedes the later sticky halt and does not describe current admission or prove uptime/profitability.


## Actual deployment after separate owner authorization

Source ed439e8 is installed on both actual hosts. Railway deployment3cf55164-902f-4493-9a8a-6c97d394c0b8 is SUCCESS/RUNNING; all 129 runtime bytes, original opening/manifest, eight accounts, strategies and cost T0 match the checked cutover. Readiness was released12:16:36UTC. Startup and the first five-new-batch summary have confirmed local delivery markers from validated webhook responses; no remote message IDs/bodies are retained. The12:24 sample verifies all-eight common settlement/cost coverage, and12:26 verifies zero orders/positions in every account. The supervisor entry halt remains. One scheduled failure followed by two successes is preserved; neither its cause nor profitability is established. [Actual result](LIVE_CUTOVER_RESULT.md), [final typed verification](live-cutover/FINAL_VERIFICATION.json).
