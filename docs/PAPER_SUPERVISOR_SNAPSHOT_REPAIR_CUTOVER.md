# Proposed paper supervisor snapshot repair cutover

October 25, 2026. **Draft for owner review; not executed or deployment-approved.** The [owner approval](../research/runs/paper-supervisor-retry-v1/owner-approval.json) covers the reviewed design, disabled local implementation and synthetic checks only. It explicitly excludes deployment, actual PC installation, guard changes, restart and entry-halt recovery. This document proposes a separately approved paper-only cutover. It does not grant that approval.

The repair recollects a complete supervisor snapshot after typed SQLite BUSY: at most three attempts, 0.2/0.5-second waits and a two-second retry-admission boundary. Every attempt retains the original collector and both publication gates; in-flight work retains the existing outer deadlines. Other errors still reject. **The existing `supervisor_error_halt` stays set.** There is no reset API, manual state edit, fabricated health receipt, global risk resume, strategy/risk/fee change, private exchange access, paid/model call, training, market backtest or real order in this cutover. Archive/checkpoint features remain disabled. See the [approved design](superpowers/specs/2026-10-25-supervisor-snapshot-contention-design.md).

## Frozen source and qualification

| Pin | Exact value |
| --- | --- |
| Required predecessor source | `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9` |
| Required predecessor full supervisor release | `fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844` |
| Required predecessor runtime-map hash | `0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c` |
| Candidate source | `20eb6af2a441c934618e124af30d72f411a3938a` |
| Successor full supervisor release | `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7` |
| Successor runtime-map hash | `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2` |
| Checked transition purpose | `paper_snapshot_busy_release_transition_v1` |
| Release-plan raw SHA256 | `17a768982074206fb77254cb9e792b2c03ae541aa0272348525b47f51190baf0` |
| Frozen package | `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-snapshot-20eb6af` |
| Package inventory | Sibling `hermes-snapshot-20eb6af.raw-sha256.json`, 667 files; raw SHA256 `4d97c0d3ad666912846ed3fd60d16f7b6d6cdb2ca98f28021ebc4fc1c740c990` |

The [release plan](../research/runs/paper-supervisor-retry-v1/candidate-release-plan-v4.json) retains all 129 runtime names. Only `hermes_trading/supervisor_sync.py` changes, from `f90ff5680bf79f0c813944772863ec4b78e1d7b801550b9bf2298932ad68187f` to `110c14c88d27b71b6b1b16add7a80e5b511d5e8822b0b1ad0c7b45b4366b2f74`. The separately installed operator helper `scripts/paper_release_transition.py` is `306da355b372cf0048b0192e7ec475d1c70740a5f60eb7ebe97b177d1c3b8ccf`. Unchanged watcher/native hashes are respectively `118d3170daa2bd816c205a66ec12cbaa829aceae01d456e1aa9f8d842f4e36e2` and `d1426320536bd8c369163d8fd6221f151fcb4f01e76ceb3033d945b8f96bd073`. Use the raw-pinned export, never an ordinary Windows checkout with rewritten line endings. [Package verification](../research/runs/paper-supervisor-retry-v1/candidate-package-verification-v4.json).

The new helper purpose admits this single runtime delta only from the exact predecessor source, full release and runtime map above, with unchanged watcher/native bytes. A matching filename or one-file diff alone is insufficient. Other transition purposes retain their existing scope.

Recorded local evidence:

- The earlier 608-file program package passed [20 Windows checks](../research/runs/paper-supervisor-retry-v1/exact-package-verification-v3.json) and [21 Linux checks](../research/runs/paper-supervisor-retry-v1/linux-verification-v3.json), with unchanged raw files. Linux used read-only source, an unprivileged user and no network. The new 667-file package preserves all program/test bytes and adds the 57 original historical inputs, their raw inventory and exact-path Git attributes; these were omitted from the earlier source package. [Provenance](../research/runs/paper-supervisor-retry-v1/historical-fixture-provenance-review.md), [tracking verification](../research/runs/paper-supervisor-retry-v1/historical-fixture-tracking-verification.json). Its formerly failing fixture setup/activation-copy now passes, but the eleven dependent checks and full-result composition remain pending.
- [Affected Windows run](../research/runs/paper-supervisor-retry-v1/verification/affected-windows-v1.result.json): 139 passes and one platform skip, zero failures/errors. This predates the test-only explicit receiver-clock correction. Product bytes are unchanged; the retained first Linux fixture error and corrected package results qualify the composed evidence. [Review follow-up](../research/runs/paper-supervisor-retry-v1/review-followup.md).
- [Independent product review](../research/runs/paper-supervisor-retry-v1/review.md) reports no material findings; it predates that test-fixture correction. The subsequent follow-up is implementer-authored.
- **The original 1,712-case frozen Windows regression remains nonterminal** in live session `42772`, started at20:16 UTC on the earlier 608-file package. At20:46 UTC it records1,275 cases and three errors. An isolated fixture-stage reproduction proves a missing historical manifest before the venue-review test reaches runtime code. The current run and its package remain untouched. Collect every terminal traceback/unrun case and source-invariance result, then verify the restored eleven-case dependency set on the new package before deployment approval. No passing full-suite claim or final package qualification is made. [Diagnostic](../research/runs/paper-supervisor-retry-v1/venue-fixture-stage-diagnostic.json), [corrected setup](../research/runs/paper-supervisor-retry-v1/venue-fixture-stage-diagnostic-v4.json).

The [previous reporting cutover](PAPER_DISCORD_REPORTING_CUTOVER.md) and its [final acceptance](../research/runs/paper-discord-reporting-v1/live-cutover/FINAL_VERIFICATION.json) establish the historical predecessor, reporting confirmation and retained halt. Their deployment IDs, roots, counts, zero-trade observations, old transition ID and intents are historical evidence, not reusable authority or current preflight. The earlier full reporting suite retained two disabled-archive fixture errors and 30 unrun cases. This repair does not erase those results, the historical continuity gap or any qualification limit.

## Identity and host bindings to preserve

Use the original [identity record](../research/runs/paper-discord-reporting-v1/proposed-original-identity.json) as the expected identity; all eight named account hashes must match, not just the EUR200 primary:

- Experiment `paper-beta-v1-20261021-6c9d520`; `adaptive_50/100/150/200` and `control_50/100/150/200`; EUR200 remains primary and the fixed controls stay fixed.
- Manifest raw hash `43ec91c55d05f6f04f7a227dd9adc23a2283dd0d788a2255903cb63ee60cbb64`; opening canonical hash `50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349`; owner-policy hash `db684ab9780a550de2cbaa22d586dfe706154cea47dfd6bef8ed247ff56d1577`.
- Common opening `2026-10-23T20:13:56.622959+00:00`; original account cost T0 `2026-10-23T20:13:29.470165+00:00`; existing supervisor deadline `2027-04-21T20:13:29.470165+00:00`. Preserve every account's recorded cost clock, qualification clock, day-30 checkpoint, counters, journals, costs, strategies, pending evidence and latches; never initialize a replacement experiment.
- Railway target from the frozen plan: project `f78f42d9-20ec-44f6-ae3f-f8bb79ee7000`, service `8b94cbc9-293e-4222-a8a7-698b656985f7`, environment `fdcc71a3-00a9-410c-8d6f-59af9bc98d36`; worker root `/app/state`.
- Historical PC supervisor root `E:/_OB_VAULTS/Main Vault/Projects/Automatic Trading/hermes-trading/state/paired-supervisor/paper-beta-v1-20261021-6c9d520/adaptive_200`; native launch file `C:/Users/thoma/AppData/Local/hermes/scripts/hermes_trading_paper_6c9d520_native.json`. Re-resolve actual runtime, supervisor and launch paths from the fresh native launch validation; do not assume these dated paths are unchanged.

## Sequence proposed for separate approval

Every step below is **unrun**. Any later approval must refer to this candidate, exact target, retained halt and actual frozen transition evidence. Use a new audit directory; retain typed, secret-free receipts. Never print credentials, webhook URLs, provider tokens or response bodies.

1. **Fresh preflight, before interruption.** Record current deployment/instance IDs, actual process/source pins, service volume and native safety/admission state; validate paper mode and real-orders-disabled settings. Verify all original identities, economics and clocks above; record actual positions, orders, pending batches, importer/renewal work, supervisor reservations and current entry latch. Historic zero counts are insufficient. Verify existing free-model metadata/usage predicates without a provider or model call; a stale/unknown predicate remains a block. Verify the 667-file inventory, plan hash and all old/new release maps. Record current schedule definitions and backup evidence. Refuse a foreign predecessor, altered runtime inventory, missing identity, unavailable safety check or unresolved protected work.

2. **Establish one writer and a bounded preparation window.** Pause only supervisor job `2f6c5088c6ff` and PC backup task `Hermes_Paper_Backup_6c9d520`; retain the legacy job's existing paused state. Drain their actual owned child processes, renewal/import handles and SQLite leases. Stop the existing paper worker normally and prove its terminal ownership; do not stop the shared Hermes gateway. Resolve pending work only through existing checked recovery before freezing; do not delete or rewrite it. If a safe closed cut cannot be reached, stop the cutover and retain evidence. Deploy only the verified frozen 667-file package to the pinned Railway target with the finite start command below, withholding `/app/state/.paper-ready-snapshot-20eb6af`. The preparation instance provides access to the stopped volume and candidate helper without starting `paper_boot`. Verify actual new deployment/instance IDs, source and waiting process before proceeding. Keep the old reporting marker/history; an old marker must not release this new gate.

3. **Capture fresh closed originals and cold copies.** After all writers are terminal, establish native sidecar-free closed state on both hosts using existing close/recovery checks; never delete SQLite journals/WAL/locks to force closure. Capture the complete worker root (all eight wallets, common journals, reports, ownership and delivery metadata), PC supervisor state and native launch file. Hash inventories, verify database integrity/consistency and the identity/economic/clock predicates, then verify an off-host copy before any binding write. Preserve actual positions, history and latches. Keep this closed cut distinct from the scheduled daily backup and record its last common observation for the eventual interruption measurement.

4. **Freeze one fresh six-component plan and two root-bound intents.** Use the candidate helper's `prepare_plan`/`prepare` with `candidate-release-plan-v4.json`, the original expected identity, and these fresh closed references. Save the returned plan immutably; verify its purpose, transition hash and protected-state inventory. Do not reuse the Discord plan or its intents. On the worker, use actual `/app/state` plus fresh closed PC/launch peer references; on the PC, use its actual supervisor/launch paths plus the fresh closed worker peer reference. Each layout must match the same frozen six-component plan but obtain its own root binding and new audit directory outside all inputs.

   Prepare each durable intent through the existing `apply_transition(..., apply=True, approval_ref=<actual approval>, fault=<stop at intent>)` boundary, as in the prior reviewed procedure: stop immediately after the intent and consistent rollback copies are published, before any component write. Inspect that all six remain `before`; verify both intent hashes, actual root bindings and rollback evidence off-host. This is an explicit supported pre-write boundary, not a new reset or state-edit mechanism. Future transition ID, intent hashes, audit paths and approval reference are **not yet observed** and must come from that execution.

   | Component | Checked release-only change |
   | --- | --- |
   | `worker_renewal` | `adaptive_200/model_access_refresh.sqlite` importer binding |
   | `worker_usage` | `adaptive_200/supervisor_usage_feed.sqlite` importer binding |
   | `worker_limits` | `adaptive_200/supervisor_limits.json` release hash |
   | `pc_limits` | `supervisor_limits.json` release hash |
   | `pc_lifecycle` | `supervisor_lifecycle.json` lifecycle binding |
   | `pc_launch` | Separate native launch JSON release hash |

5. **Install exact PC source with startup withheld.** Recheck that the remote preparation instance is still waiting, the new readiness marker is absent, and no worker/watcher writer exists. On the PC, back up and replace only `hermes_trading/supervisor_sync.py` and `scripts/paper_release_transition.py` with the frozen raw bytes. Use byte-preserving copies, not checkout/build output; preserve all other installed raw bytes. Verify all 129 runtime files, helper, watcher/native hashes and successor map on both hosts. No PC process starts during this installation.

6. **Reconcile the checked transition on both hosts.** Recheck immutable plan, intents, rollback copies, closed ownership, source and protected state. Call the existing helper with the respective roots, `apply=True`, `reconcile=True` and the actual approval reference; equivalently its `apply --apply --reconcile --approval-ref` interface requires `--worker-root`, `--supervisor-root`, `--launch-file`, `--audit-dir` and `--plan`. Retain six component receipts and a completed receipt per root binding. Inspect all six as `after` on each host, and validate actual host paths, source, launch/account/policy bindings and protected-state invariance. Mirrored references alone do not prove the actual peer. No manual JSON/SQL binding update is permitted.

7. **Release readiness only after both hosts verify.** Before expiry of the preparation gate, revalidate both completed transitions, full installed source maps, all eight identities, original manifest/opening/clocks, protected histories, retained entry halt and native launch validation. Create the distinct new readiness marker only after those checks. Record its actual release time and retain it for protective restarts. Observe one owned `paper_boot`, fresh common batches, stable heartbeat and protective/financial processing; compare all eight finalized wallet rows against native journals, including cost quality and unknown fields. Measure and retain the actual observation gap without replay or clock rewind.

8. **Restore the existing bounded schedules and verify backup.** Resume only the captured supervisor/backup schedules with unchanged time/budget limits and truthful usage, after native validation admits them. Do not invoke a model/provider or ad hoc review as an acceptance probe. Observe ordinary scheduled outcomes and unchanged guard bindings; a recurring BUSY/error is a recorded failure, not permission to relax checks. Verify the next actual completed, off-host-checked E: daily generation against its inventory and retained identities. An `already_completed` result refers to its existing generation and is not evidence of a new post-cutover backup.

9. **Confirm existing Discord reporting through natural work.** Preserve notification configuration and delivery metadata; let the existing worker use its existing webhook after readiness. Read-only observation should record a fresh confirmed ordinary summary after at least five new common batches, all eight native rows/four same-size comparisons, EUR200 primary, observation times, cost quality, unknown coverage and actual delivery counters. Startup may be deduplicated by retained metadata; an old startup marker is not fresh confirmation, and no metadata reset/manual ping is allowed. Confirmed local part markers evidence validated sender responses, not retained remote message IDs, rendered bodies or exactly-once delivery. A natural close alert remains unobserved unless a real paper close occurs; never force a trade or close. Record terminal acceptance and every unavailable predicate explicitly.

Proposed Railway start command, supplied as one structured field only after approval:

```sh
/bin/sh -c 'set -eu
deadline=$(($(date +%s) + 1800))
until [ -f /app/state/.paper-ready-snapshot-20eb6af ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo paper_preparation_expired >&2
        exit 78
    fi
    sleep 2
done
exec /app/.venv/bin/python -m hermes_trading.paper_boot --root /app/state
'
```

## Failure handling and rollback limits

Before readiness, any foreign/conflicting component, failed source/identity check, unexpected writer, intent mismatch or gate expiry blocks activation. Keep the marker withheld, retain actual deployment/process status and preserve evidence. Do not repeatedly restart into an expired/unreconciled preparation. A timeout or lost reply has an unknown outcome: inspect the same immutable intent and root binding, then reconcile its actual component states. Never create a fresh intent to hide a partial write or copy an intent between roots.

Checked rollback may reverse all six guards only while the helper verifies the original protected state is unchanged and the same intent/rollback evidence remains valid on both hosts. If reversing before protected work, keep both processes stopped and restore exact predecessor executable bytes/configuration consistently with those guards, revalidating the predecessor on both hosts before any startup. A guard rollback alone does not restore executable compatibility. The local implementation approval authorizes none of this.

After any new batch, cost/usage import, renewal or other protected write, old-state rollback is ineligible: retain successor history and reconcile forward through a separately reviewed correction. Never restore old databases over new work, rewind clocks, clear latches, delete delivery history or erase readiness/history to obtain a green result. Discord 401/403/404, timeout, corruption or delivery gaps are notification failures; preserve protective processing, report typed errors and avoid repeated manual requests. A worker safety failure follows the existing fail-closed behavior.

## Still unknown before an execution decision

The package-input omission has been repaired in a separate export; full/affected composition is not yet verified. This draft cannot be used as deployment approval. The earlier `a79fb93` code remains the tested program base; the new commit makes its historical correctness inputs durable without changing program or test source.

The full frozen regression's terminal result/source invariance; current actual deployment/process/source/guard/schedule/backup state; pending work and positions; current free-model admission/usage; new cold-cut inventories, transition/intents/root bindings; deployment IDs, marker release and post-cutover observations are **not yet observed for this repair**. They are execution gates, not claims made by this draft. The live lock duration and improvement in snapshot success rate remain unproved. Snapshot success will not clear the sticky halt, resume entries, repair historical continuity, establish LLM learning, or prove a profitable wallet or strategy.
