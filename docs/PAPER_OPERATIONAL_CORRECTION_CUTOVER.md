# Paper backup correction and retained-experiment cutover

Status: disabled local candidate. The owner approved design, local implementation and synthetic copies only. Successor deployment, live release-binding migration and schedule changes require separate approval. No new experiment is proposed.

Deployable source candidate: `49c851315b98bb33b1eb78180fb789afe2a49f3d`, based on approved `6c9d520da5f8473b51c912d1eb0e269ee94d4b1b`. Only runtime change: permit literal `+` in snapshot component names, so Railway's `lost+found` and its evidence can be included. All other archive guards remain. The separate operator script changes six release guards with checked intent, consistent rollback copies and receipts. It never deploys or starts a writer.

The effective-stop proposal `3e785e1` is excluded. Strategies, fees, limits, archive/checkpoint enablement, provider/model selection and real-order/paid-call settings remain as approved.

## Exact retained identity and source

| Item | Frozen value |
| --- | --- |
| Experiment | `paper-beta-v1-20261021-6c9d520` |
| Account/cost T0 | `2026-10-23T20:13:29.470165+00:00` |
| Authoritative opening | `2026-10-23T20:13:56.622959+00:00` |
| Opening canonical SHA256 | `50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349` |
| Original manifest raw SHA256 | `43ec91c55d05f6f04f7a227dd9adc23a2283dd0d788a2255903cb63ee60cbb64` |
| Old supervisor release hash | `ae9cd5c4fbdeb829ccc1c9f72b27a8b2a9de4ace6a1f8a85c3035753233c9e90` |
| New supervisor release hash | `f5cae59278c4e99fc3fa3a33da4bf55890276b29370a28260e9cf3400729edfd` |
| Old/new runtime map hashes | `c013977db0bd446ddc36005352b38a2b0ba2783a77b4547f11682feb104c8450` / `2577bc1ce7c64adccd92a211cf60e25cce6f1e392bb63cfdb516802c1df17fcc` |
| Railway project | `f78f42d9-20ec-44f6-ae3f-f8bb79ee7000` |
| Environment / service | `fdcc71a3-00a9-410c-8d6f-59af9bc98d36` / `8b94cbc9-293e-4222-a8a7-698b656985f7` |

The [release plan](../research/runs/paper-operational-corrections-v1/proposed-release-plan-49c8513.json) contains all 129 runtime hashes and unchanged watcher/native-launcher hashes. Its raw SHA256 is `3ef8dc9bb000a8ba3209aaa2bc31708638439001f17f0debc981998cb5b3bce4`. The [original identity](../research/runs/paper-operational-corrections-v1/proposed-original-identity-20261024.json) includes all eight account hashes and the original policy.

Use the prepared `research/runs/paper-operational-corrections-v1/release-upload-49c8513` export. Its [522 raw file pins](../research/runs/paper-operational-corrections-v1/release-upload-49c8513.raw-sha256.json) preserve the approved deployed bytes where unchanged; every exported tracked file was checked against the candidate commit after newline normalization. A fresh Windows checkout changes 107 additional runtime digests through line endings and must not replace this export. Only `archive_snapshot.py` differs between actual old/new runtime maps. Recheck every raw pin before upload and on installation.

## Current operation and local evidence

The October 24 10:16 UTC read-only worker observation records 732 completed common batches, zero orders across all eight accounts, original opening/manifest hashes and healthy paper/simulated heartbeat. Entries remain blocked by `daily_backup_failed`. Primary equity is about EUR199.86547 after EUR0.13453 estimated hosting; no trading profit is established. The PC backup task failed again at00:15 UTC. A recent supervisor invocation failed and subsequent invocations completed; preserve both outcomes. This live sample is not a closed consistent state copy.

Local test results and exact limitations are recorded in [verification](../research/runs/paper-operational-corrections-v1/VERIFICATION.md). The independent review found three operator admission/recovery defects at `79de870` and accepted their fixes at `49c8513`. Synthetic successor tests preserve original opening/accounts, clocks, histories, limits, costs and sticky safety latches; they also exercise renewal, usage imports, both host views, native boot, daily backup and verified synthetic pull. Neither a synthetic receipt nor a release-binding receipt certifies live operation or profitability.

## Approved-operator procedure after separate cutover approval

1. Revalidate current worker/source identity, original opening/manifest/accounts/policy, volume and all paper/no-real-order settings. Preserve the historical observations and failures. Pause only this experiment's supervisor job `2f6c5088c6ff` and backup task `Hermes_Paper_Backup_6c9d520`; keep legacy job `a316e6ed4190` paused. Drain their actual cycle, review, import, publication and backup handles to terminal states. A pause flag or an expired observation is insufficient. Never kill the shared Hermes gateway or clear pending records.
2. Stop the old worker through the normal platform path; verify its process/ownership handles are terminal and pending work is resolved without deletion. Confirm the proposed new marker is absent; if it already exists, stop and reconcile its provenance rather than deleting or reusing it. Deploy the exact raw-pinned successor with the finite preparation start override below, sent as a structured API field. Withhold the new marker `/app/state/.paper-ready-operational-49c8513`; the old marker stays intact. During preparation no worker or model runs. Install the same raw-pinned runtime on the PC at its retained original project paths: only `archive_snapshot.py` changes, with its original source saved in the cutover audit; preserve all unrelated working files and state. Invoke the new operator helper from the staged export. Verify both installed source maps and native imports before touching guards. A failed preparation remains stopped for reconciliation; never fall back automatically to old code against new guards.
3. With both actual writers quiescent, obtain complete consistent closed worker/PC/launch reference copies and verify them against the original identity. Do not copy an active SQLite main file without its committed state, ignore sidecars or remove `lost+found`. The helper admits only existing sidecar-free closed copies and acquires native SQLite owner/import leases. Unresolved pending work, unknown schemas, unsupported paths, conflicts or new mutations refuse the transition.
4. Prepare one fresh immutable six-component transition plan from this quiescent cut. It freezes current protected state and original counters; it does not reuse synthetic plans or old live inventory fingerprints. Inspect before applying. For the worker host use actual worker state plus the original PC/launch references; for the PC use the original worker reference plus actual PC state/launch. Copy the same frozen plan to both, use separate external audit directories and explicit approval references. Keep readiness and schedules withheld throughout. The two hosts are not an atomic transaction.
5. Apply with `python -m scripts.paper_release_transition apply`, explicit roots/plan/audit and both `--apply` and `--approval-ref`. The fixed components are worker renewal binding, worker usage binding, worker supervisor-limit release, PC supervisor-limit release, PC lifecycle binding and native launch release. Verify each host's intent, consistent rollback copy and all six component/completion receipts. The transition ID must match; each host's root binding must match its actual supplied roots. A reference-copy receipt alone cannot prove the other actual host changed.
6. Check both actual installed guard sets, original identity and protected history before releasing the new marker. Boot must resume the original experiment and deadline. Never rerun activation/finalization/initial installation or assign a new T0. Observe a later common batch, all eight matching reports/journals, retained costs and safety latches. Expired access proof blocks entries while protective processing continues. Resume only the existing bounded native watcher with fresh verified-free metadata and truthful usage coverage, preserving counters, model reservations and deadlines. Preserve the wrapper's EOF handling and unchanged native launcher pin.
7. Require a real completed native daily generation and an actual verified copy to the existing E: destination before claiming backup acceptance. Run the existing pull task once after its prior instance is terminal; verify original manifest/opening and inventory/receipt hashes. Resume the same daily schedule. A synthetic pull's `off_host_verified` does not satisfy this step. Keep any native entry block or error latch until its original rules clear it; no manual healthy receipt is permitted.

Exact proposed successor `startCommand` (original reviewed structure, distinct marker):

```sh
/bin/sh -c 'set -eu
deadline=$(($(date +%s) + 1800))
until [ -f /app/state/.paper-ready-operational-49c8513 ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo paper_preparation_expired >&2
        exit 78
    fi
    sleep 2
done
exec /app/.venv/bin/python -m hermes_trading.paper_boot --root /app/state
'
```

Keep the new marker after successful reconciliation/boot while this override remains installed, so a protective restart resumes immediately. Never create it as a substitute for both actual-host acceptance checks. Sidecars remaining after actual writer termination require consistent native SQLite recovery and renewed closed-copy checks; this helper never deletes or ignores them. If the approved preparation cannot establish the required closed state, remain stopped and report the unresolved condition rather than making an unreviewed repair.

The original approval allows only verified-free reviews after five newly closed trades; this cutover adds no paid call, private exchange use, training, market backtest, real order, risk increase or strategy experiment. Continue reporting skips, inactivity, costs, failures and adaptive/control differences. Operational acceptance cannot establish an edge.

## Interruption and rollback

Before any guard write the helper records the full immutable plan, target roots, explicit approval reference and consistent original rollback evidence outside the inputs. Each component uses checked before/after values, atomic file replacement or a SQLite transaction. If a component commits before its receipt, inspect the same audit and explicitly reconcile; do not create a new intent or restart blindly. All states and existing receipts are preflighted under leases before a recovery write.

Before any new protected work, `rollback` with the same plan/audit and explicit `--apply --approval-ref` reverses only the six guards. It does not restore old databases over later history. Verify both actual host receipts and guard sets before restoring the approved old release and original start override/schedules. Keep readiness withheld during any mixed state. Once a new batch, renewal, usage record or other protected change exists, old-state rollback is refused: stop the affected schedules/writer and reconcile forward while preserving all history. Never rewind clocks, costs, reviews, latches or counters. A successful identical retry returns its original receipt.
