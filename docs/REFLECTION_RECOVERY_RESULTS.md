# Reflection recovery preparation

October9,2026. Realignment toward the original Railway paper worker plus LLM supervisor. No profitability qualification, remote deployment, strategy-state replacement or paid model request occurred.

## Verified findings

1. All824October records labeled `hermes_asset_failed` have empty errors; all10October applied changes identify the deterministic provider. A bounded no-op from the deterministic generator was mislabeled as model failure. It does not establish824LLM failures. The local patch now logs explicit deterministic no-change decisions separately and preserves provider/model on actual failure records. Generic missing proposals still fail unless the generator explicitly identifies an error-free no-change outcome. Strategy/version/history/cadence behavior is unchanged by that patch.
2. A malformed historical hypothesis record at line7859is present in both the mirror and a fresh remote read. The external watcher's due check reproducibly raises JSONDecodeError on that log before any model call. This is an actual supervisor blocker.
3. The external watcher counted current-format trades/decisions without recognizing `closed_at`/`decided_at`, effectively treating undated-to-that-parser historical rows as new. The local correction recognizes native and legacy timestamps, normalizes naive UTC consistently and excludes unparseable/missing timestamps. Non-string timestamps also have a tested guard. This prevents old outcomes from repeatedly earning new paid reviews.

## Recoverable log repair staged

`research/runs/reflection-diagnostics-v1/log-repair-stage-v1/` preserves the original remote capture, repaired copy, hashes, replacement metadata and validation results. Exactly one record is replaced by a valid audit quarantine entry containing the original raw bytes in base64, their hash and a parse error. It deliberately has `recorded_at` rather than `timestamp`, so the repair itself cannot advance the reflection watermark.

All10,122staged rows parse. Every previously valid record is unchanged and in the same order. Decoding the audit entry reconstructs the original full log byte for byte. An isolated copied-state watcher dry run completes with zero new trades/decisions; no watcher main function or model runner was called. Snapshot freshness and reader compatibility are not permission to overwrite a concurrently written operational log.

Before remote application, require a current-source hash match, a preserved remote backup and a coordinated write window so an append cannot race the replacement. Abort on drift. The remote operating log and local operating mirror remain unchanged. Do not silently delete the malformed historical record or manufacture its intended hypothesis.

## Verification

Three outcome tests and four cadence tests were observed failing before fixes. Related reflection/model tests passed after the outcome patch; the full suite passed594tests. After the cadence correction, the full suite passed598tests. Independent review found no material classification/cadence/staging issues but requested a non-string timestamp guard; its regression failed before the guard and all76related tests passed afterward. The full598-test result predates that final one-line guard, while the focused result covers the final code. Graph updates were run after source changes.

## Remaining recovery and profitability work

- Coordinate and apply the staged history repair safely, then verify remote and mirrored readability.
- Resolve ownership: the external watcher currently takes its watermark from the latest hypothesis of any provenance. A deterministic worker can consume the LLM supervisor's review window. Establish one strategy writer and an explicit LLM review watermark before claiming autonomous LLM steering works.
- Prove a review using the intended model and recorded costs only within authorized usage. Pure deterministic reviews are not LLM evidence.
- Reconcile deployed/local versions, tradable France-account markets, wallet metrics and one-variable-per-experiment behavior. Prepare a concrete paper release for review before deployment.
- Evaluate an LLM-steered candidate against a fixed control at EUR50/100/150/200, including full costs. The profitability goal remains active and unmet.

## Coordinated repair applied, October9

The known malformed record has now been repaired on the remote paper worker. New external watcher starts are blocked by the tested local `state/watcher_maintenance.json` marker, and no in-flight watcher/reflect process was found before the operation. This is maintenance of one supervisor, not a pause of the research goal or shutdown of paper trading.

Standalone helper `scripts/repair_hypothesis_log.py` refuses non-paper modes, unrecognized workers, independent remote reflect writers, unknown corruption, hash drift and open hypothesis descriptors. It verifies all worker threads are stopped, retains a fsynced original backup, preserves file ownership/permissions and atomically replaces only the known corrupt record. Every worker paused by the helper is resumed in finally. Tests cover byte preservation, unknown corruption, hash mismatch, backup retention, failure-path resume and excluding the container launcher/init from worker selection.

The first execution aborted without altering the log because the uv launcher at PID1could not be stopped. Read-only inspection identified the actual Python worker at PID5; the selection was corrected and regression-tested. The second execution succeeded at11:26:29UTC, briefly coordinating only PID5. Backup is `/app/state/hypotheses.jsonl.pre-repair-39815ad526bbbdcec784fdf079f59738f9a88db48fc9f58c27e7f017097781a9`. Repaired hash is `d554fd7ff6f386de6309751f35387faca36bdda0cf0b5fbf33381dbb69560722`.

Independent reads verified the backup hash, all10,128parseable records, original-byte recovery and identical remote/local mirror hashes. Heartbeat11:26:59UTC is later than the repair request and reports healthy training/simulated mode, strategy270. The strategy itself was not edited by the repair. The direct read-only watcher due check now succeeds and reports zero new trades/decisions. No watcher main/model invocation was used for this verification.

Artifacts in `research/runs/reflection-diagnostics-v1/remote-repair-v1/` preserve the aborted attempt; `remote-repair-v2/` preserves the successful helper snapshot, request, result, original backup copy, verified remote log, prior local mirror and verification JSON. The original staged repair remains separately preserved.

The external watcher maintenance gate remains active intentionally until writer ownership and model cost/access are resolved. The old deterministic in-worker source still runs; the local no-change-classification patch has not been deployed. Do not describe this successful history repair as restored or profitable LLM supervision.
