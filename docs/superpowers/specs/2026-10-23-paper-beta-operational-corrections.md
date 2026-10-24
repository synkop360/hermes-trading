# Paper beta operational corrections

**Status:** Approved October 23, 2026: “Approve design and disabled implementation.” This covers the design, disabled local implementation and synthetic verification only. Any successor deployment or migration of installed state requires separate explicit approval of its exact commit and transition plan.

## Problem and retained experiment

The approved `6c9d520da5f8473b51c912d1eb0e269ee94d4b1b` runtime is deployed as `e78e9c82-b461-40ad-bedb-1cfccf5c292e`. Its experiment `paper-beta-v1-20261021-6c9d520` has a verified original all-eight opening and completed common batches. This proposal repairs daily backup publication while retaining that experiment.

Preserve account T0 `2026-10-23T20:13:29.470165+00:00`, actual opening `2026-10-23T20:13:56.622959+00:00`, and original receipt hash `50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349`. Preserve installed manifest bytes, SHA256 `43ec91c55d05f6f04f7a227dd9adc23a2283dd0d788a2255903cb63ee60cbb64`, all account/owner/goal/strategy bindings, journals, settlements and reporting protocol. The [all-eight observation](../../../research/runs/paper-activation-v1/all-eight-operation-202024.json) verifies six completed batches and eight matching reports/journal receipts at 20:20:26 UTC; the [opening attempt](../../../research/runs/paper-activation-v1/actual-opening-attempt-20261023.json) records the original T0 and installation.

The first PC copy failed at 20:16:27 UTC. Independent native read-only isolation found `/tmp/hermes-paper-daily-backups` empty, `daily_backup_error="ValueError"`, and `_inventory('/app/state', ...)` rejecting the sole nonportable path `lost+found`. Ownership and quote-cohort checks passed; tar/timeout were present. PC/remote manifest hashes and expected generation `state-2026-10-23-73a6a6a8ab53` agreed. Thus the PC transport failed because no completed native snapshot existed.

Entries remain protected by `daily_backup_failed`; supervisor health is an independent admission condition. Preserve existing exits, safety latches and costs. These observations establish operation and an operational defect, not profitable trading.

## Approach and boundaries

Recommend a narrow portable-name correction plus one checked release transition. Changing installed source in place would leave loaded/reported releases inconsistent. Deleting or renaming `lost+found`, excluding its contents, resetting the experiment, or relaxing all filename checks would conceal or alter the source evidence.

Change only `hermes_trading/archive_snapshot.py`'s component-name acceptance to admit literal `+` alongside the existing ASCII letters, digits, underscore, dot and hyphen. Continue traversing directories and including every regular file under the existing inventory rules. Retain link/reparse/nonregular rejection, trailing-dot rejection, case-fold collision detection, reserved inventory names, sidecar validation, source-change detection and all file/byte/time bounds. Do not special-case away `lost+found` or normalize names.

Prepare the successor from `6c9d520` in isolation. Preserve the deployed baseline and its fingerprints. The separate effective-stop proposal `3e785e13d53923e067009c463e6e9dfa3fec4ff0` is excluded. Keep all eight scenarios and EUR200 primary, fees, slippage, risk/exposure/order caps, strategy economics, learning cadence, paid-call prohibition and real-orders-disabled settings unchanged. This is neither archive expansion nor a generic release-migration framework.

The PC EOF invocation-wrapper correction is separate operator work under the existing cutover: it can supply closed stdin while retaining exact native/runtime bytes. This design does not add a pinned watcher-source change.

## Checked release transition

A one-time operator helper must default to inspection, take an explicit approved old/new release plan and require an explicit apply flag. Bind the plan to this experiment, deployment target, exact source commits/runtime maps, original opening hash, raw manifest hash, eight account hashes and owner hash. Reject unexpected identities or any changed economics before writing.

The worker restart configuration hash excludes runtime source hashes, so the original lifecycle can resume unchanged. Completed packets retain their original release maps. A pending packet requires its original release, and must be reconciled before switching. Refuse transition while a watcher/review/import writer is active, a batch or publication is pending, or any inference outcome remains unresolved. Retain every pending record; never clear it to make admission pass.

With both writers quiescent, preserve a checked consistent rollback copy and record a durable transition intent. Rebind only these current release guards:

| Installed guard | Permitted change |
| --- | --- |
| PC/worker `supervisor_limits.json` and PC native launch JSON | Approved supervisor release hash only. |
| PC `supervisor_lifecycle.json` | Recompute binding over the final limits, unchanged owner and approved successor release. Preserve every counter, deadline, status and timestamp. |
| Worker `adaptive_200/model_access_refresh.sqlite` binding | Approved runtime release digest only; preserve original provenance and every renewal row. |
| Worker `adaptive_200/supervisor_usage_feed.sqlite` binding | Approved runtime release digest only; preserve all event IDs, snapshots, receipts and coverage. |

Existing rows and old binding values must remain recoverable in the transition audit. The PC initial-install renewal database remains historical unless an actual native local installer is to consume it; that case must be explicitly included in the reviewed plan. The local review/usage-source audit retains its account binding and is never reinitialized.

Each component must check its exact old value, transact or atomically publish its permitted update, and emit before/after hashes under one transition ID. Matching completed retries return the original receipt; conflicting or partially applied states stop for reconciliation. Cross-host updates are not atomic: keep schedules paused and successor readiness withheld until every required receipt agrees.

Use matching approved source on PC and Railway, the same volume/configuration and a successor-specific finite preparation marker. Verify the stopped volume has no pending old-release work before starting the successor. Do not rerun the activation stager or fresh-input installer. Native boot must read the same original opening, preserve worker lifecycle configuration/counters/deadline, and retain all financial history. Obtain genuine model renewal through native metadata checks when needed; never extend timestamps or alter owner/manifest provenance.

## Local validation and acceptance

After design approval, implement and verify only in an isolated checkout and synthetic copies. Add focused regression checks for empty and populated `lost+found`, nested plus-containing files, exact copied bytes and successful native daily-copy/pull validation. Retain negative checks for links, nonregular entries, trailing dots, collisions, orphan sidecars, reserved names, changed source and existing bounds.

Exercise old-to-new release transition on synthetic native opening/batch/renewal/usage/supervisor state. Assert byte-identical manifest/accounts/opening and unchanged historical rows, costs, counters, deadlines, pending states and safety latches. Reject wrong releases/identities, active writers, pending batches/publications and unresolved inference. Exercise interruption after each component, identical retry, conflicting retry and checked rollback. Prove successor boot resumes the original opening, creates a later common batch, renews access/imports usage under the successor binding, and validates the native supervisor snapshot. Use synthetic transports; make no provider/model call or live migration during this work.

Before requesting live approval, present the exact successor commit/diff, focused results, independent review, release maps, transition helper/plan and rollback receipts. After separate deployment approval, require observed preservation of the opening and all-eight reports, healthy acknowledged supervision, an actual completed native daily snapshot and its verified E: copy before reporting the correction operationally complete.

## Failure and rollback

Retain `daily_backup_failed` and every safety latch until native operation truthfully clears its own conditions; no manual success receipts. A failed/partial transition leaves the successor unstarted and schedules paused while authoritative component receipts are reconciled. Before any successor write, rollback may restore checked bindings/source without changing clocks or journals. After new work or new ledger rows exist, never restore an older database over them: use a separately checked reverse transition or remain protected for reconciliation. An uncertain opening or transfer is inspected, never recreated. Record all stopped time and failed copies in operator evidence.
