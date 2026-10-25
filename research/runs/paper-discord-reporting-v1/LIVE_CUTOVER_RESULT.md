# Paper Discord cutover result — October 25, 2026

The owner-approved paper reporting version is deployed and activated. **Startup and the first five-new-batch reporting cadence are confirmed** by the native sender's validated HTTP-response contract and retained local markers. The acceptance observation has seven new completed batches and matching native financial evidence for all eight accounts; a later native receipt at 12:26:21 UTC verifies source and zero trade counts. This result does not establish natural-close alert delivery or profitability.

## Deployment and preserved state

The [owner instruction](live-cutover/owner-approval.json), “deploy a discord enabled version as soon as possible,” authorized the reviewed paper-only cutover. Source is `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9`; supervisor release is `fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844`. Deployment is `3cf55164-902f-4493-9a8a-6c97d394c0b8`, instance `b45705e2-e89a-49fe-89c5-f2f81bbac862`.

The original worker stopped, and its [525-file closed reference](live-cutover/worker-original-reference.json) retained all 1,741 completed batches. The [original remote intent and rollback evidence](live-cutover/worker-intent-offhost-verified.json) were verified off-host before any binding write. One frozen transition, `84fa276f399f1e0070e6eaca57cdab923262479d92437cb5ce0c63dfaea93981`, uses separate immutable intents and root bindings on each host. The [worker transition](live-cutover/apply_worker_transition.native-result.json) and [PC transition](live-cutover/pc-transition-completed.json) each completed with all six checked guards in their frozen after state.

The [actual PC native validation](live-cutover/pc-native-launch-validated.json) confirms all 129 runtime bytes, unchanged watcher/native launcher, the expected EUR200 launch, original account/owner bindings, cost T0 `2026-10-23T20:13:29.470165+00:00`, deadline `2027-04-21T20:13:29.470165+00:00`, 982 supervisor cycles and zero reserved model attempts at validation. The original common opening remains `2026-10-23T20:13:56.622959+00:00`, canonical hash `50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349`. No replacement experiment, clock rewind, strategy/fee/risk change or latch reset was part of this cutover.

[Readiness was released](live-cutover/release_successor_readiness.native-result.json) at 12:16:36.052072 UTC after both transitions and PC validation. The operator reports the bounded supervisor and backup schedules resumed around 12:17 UTC. The backup retry returned result 0 / `already_completed`; that refers to the existing [verified October 25 daily generation](daily-copy-verification-20261025.json), captured at 00:00:26 UTC, and does not establish a new post-cutover backup generation.

## First live reporting observation

The [12:17:36 native inspector receipt](live-cutover/inspect_reporting_acceptance.native-result.json) records:

- `startup_confirmed=true`, two locally confirmed part IDs, no pending part or abandoned intent, and zero recorded delivery-unknown, gap, invalid, dropped, oversized, omitted or restart counters. These are local delivery-confirmation markers; Discord remote message IDs and message bodies are not retained. Two markers do not prove exactly two requests or exactly-once delivery.
- One new common batch: 1,742 complete, zero pending. All eight finalized accounts match the original account hashes, latest settlement, stable heartbeat and common observation at 12:16:47.297443 UTC. EUR200 remains primary.
- All four adaptive net-equity values are unknown because the observation records missing hosting, AI, tools and funding cost coverage. Primary control net equity is EUR199.6161400431256944444444444; controls disclose estimated hosting/AI/tools and actual funding. This receipt does not establish recovered adaptive cost coverage or four available financial comparisons.
- New entries remain blocked by `supervisor_error_halt` and stale supervisor health. The retained supervisor latch has three errors despite its latest success flag. Its underlying cause remains unknown; reporting activation did not clear or repair it. The first heartbeat is degraded, not a fabricated healthy result.

The [second inspector receipt](live-cutover/inspect_reporting_acceptance2.native-result.json), at 12:21:22 UTC, records four new complete batches (1,745 total), five locally confirmed part markers and still zero delivery-unknown/gap/abandoned counters. Its common wallet observation at 12:20:16.853636 UTC has original hashes for all eight accounts, no missing fields, estimated hosting/AI/tools and actual funding. Primary adaptive/control net equity is EUR199.6155821967785879629629630 each. The heartbeat is now `ok`; this reports worker health, not restored entry permission. The journal query returned `SQLITE_BUSY`, so settlement comparison is unavailable in that sample; aggregate false comparison fields do not prove a financial mismatch.

The [retained successor schedule record](live-cutover/scheduled-supervisor-after-cutover.json) shows the enabled supervisor's first retained post-cutover execution failed at 12:19:23–12:19:38 UTC. The [later schedule record](live-cutover/scheduled-supervisor-later.json) then records ordinary completed executions ending at 12:21:37 and 12:23:41 UTC. The latest remote supervisor observation is successful, while its original three-error entry latch remains set. No latch was cleared. The intermittent failure's underlying provider, SSH or other cause remains unknown; successful reporting and cost observation do not establish its repair.

## Financial-summary acceptance confirmed

The [third inspector receipt](live-cutover/inspect_reporting_acceptance3.native-result.json), at 12:24:07 UTC, records `startup_confirmed=true`, `new_batches=7`, `confirmed_cycle_windows=1` and `cycle_summary_confirmation_observed=true`. There are 1,748 complete batches and zero pending, exceeding the five-new-batch threshold from the original 1,741-batch closed cut. This proves the first cadence was confirmed after at least five newly observed settled batches; it does not supply an exact historical dispatch time or promise delivery precisely at batch 1,746.

The ledger retains ten confirmed local part markers. One report group, `7dac8b8d62ae1e7af86a045b`, has confirmed zero-based part indices `0`, `1`, `2`, within the four-part bound. There is no pending part or abandoned intent; all nine retained counters are zero, including delivery-unknown, gaps and dropped events. The ledger does **not** retain Discord remote message IDs, rendered bodies or historical per-report wallet snapshots. A confirmed local group is evidence of the validated sender response contract, not an independent retrieval of the remote message or an exactly-once guarantee.

At the current common observation, **12:23:45.102722 UTC**, all eight original account hashes, observation times and wallet rows match the latest finalized settlement. The journal's batch/input hashes agree with a stable heartbeat. Every account records zero missing fields, estimated hosting/AI/tools and actual funding. Current net equity is shown below, rounded to six decimals; the exact primary value on both arms is EUR199.6150278299987268518518519.

| Wallet EUR | Adaptive net EUR | Control net EUR | Adaptive minus control EUR |
| --- | ---: | ---: | ---: |
| 50 | 49.615028 | 49.615028 | 0 |
| 100 | 99.615028 | 99.615028 | 0 |
| 150 | 149.615028 | 149.615028 | 0 |
| 200 (primary) | 199.615028 | 199.615028 | 0 |

These current native rows establish source agreement and cost quality at the later observation; they are not a retained copy of an earlier Discord body. The missing adaptive values in the first receipt and the second receipt's unavailable journal comparison remain preserved evidence.

No manual notification, fabricated trade, forced close or paid model review was used for this acceptance. A future natural canonical-close notification remains separately unobserved until it occurs. The later source/trade verification below confirms fresh post-cutover zero counts; the earlier pre-cutover observations remain dated evidence.

## Continuity and remaining limits

The verified original closed heartbeat's last common observation is 11:44:54.660866 UTC; the first successor common observation is 12:16:47.297443 UTC. Their recorded interval is **31 minutes 52.636577 seconds**. This interruption is retained honestly; no observations were replayed to fill it. The earlier six-hour continuity failure and original qualification clocks/protocol remain unchanged.

Verification remains qualified as recorded in [VERIFICATION.md](VERIFICATION.md): 47 new checks and 164 exact-export Windows checks pass; selected Linux coverage passes 52 checks with its explicit startup-harness qualification. Full Windows discovery is **failed**, with 1,657 executed passes, five skips, two separate class-fixture errors and 30 unrun catalog cases. Activation does not turn that evidence into a green full suite or qualify the disabled archive/checkpoint features.

New protected work has occurred after transition. Preserve history and reconcile forward; restoring the old databases over successor work is not an eligible rollback. The broader trading goal remains active, learning from profitable closes is not established, and no strategy edge, improved return or future profitability is proved by restored reporting.

## Final source and trade verification

The [fresh native observation](live-cutover/observe_postcutover_all8.native-result.json) at **12:26:21.818070 UTC**, cross-checked against the [compact proof](live-cutover/postcutover-runtime-and-activity-verified.json), confirms all 129 installed runtime hashes match the new raw map. All eight actual trading databases contain **zero orders and zero positions**; account identities, account cost T0 values, strategy hashes, original opening and manifest match the pre-cutover evidence. There is one recorded `paper_boot` process, PID1; paper mode and real-orders-disabled predicates remain true. The worker heartbeat is `ok`, model readiness is true and the recorded common batch count is 1,750. The running worker's ownership database was unavailable under its native lease; its direct lifecycle query is not represented as a successful read.

At the later common observation, **12:26:05.843471 UTC**, primary adaptive/control net equity is EUR199.6146531729122685185185185 each, read from the native report's `observation.net_equity`. The compact proof's initial selector was corrected; the raw native receipt was unchanged. This later value does not replace the dated 12:23 financial acceptance snapshot above. Its PC lifecycle supplement records zero reserved model attempts and the original deadline. The existing supervisor entry halt remains retained.

The [final deployment status receipt](live-cutover/successor-final-deployment-status.json) records deployment `3cf55164-902f-4493-9a8a-6c97d394c0b8` as `SUCCESS` and instance `b45705e2-e89a-49fe-89c5-f2f81bbac862` as `RUNNING`. [FINAL_VERIFICATION.json](live-cutover/FINAL_VERIFICATION.json) retains the consolidated verification. These are recorded operational observations, not evidence of profitability or a natural-close alert.
