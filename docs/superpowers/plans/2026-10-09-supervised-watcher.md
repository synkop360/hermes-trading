# Supervised watcher integration plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Route configured external supervision through the global ledger and receipt publisher, with fresh paper-worker release evidence before model calls.

**Architecture:** The worker captures release hashes once at startup, with a unique process instance and experiment-policy hash, and includes them in heartbeat. A new read-only remote snapshot command returns policy/strategy/goal/trades/heartbeat together, rejecting changed configuration while reading. The watcher validates the snapshot's experiment, loaded release, paper execution and freshness before replacing its local mirror. An exclusive watcher-cycle lease covers sync/review/publication. Pending publication is retried before any model request; other unresolved attempts require reconciliation. A runner factory reads a separately reviewed local model-access configuration, constructs the strict runner with CostLedger, and never selects a fallback.

**Spec:** adaptive-paper-recovery-goal.md criteria1–4; global-review-staging.md and paper-publication.md.

## Constraints

- Keep operational maintenance active. No actual model request, deployment or publication in this implementation.
- Configured external ownership uses the new branch; absent policy retains legacy behavior until rollout. No local supervisor SQLite state is downloaded or overwritten.
- Startup fingerprint is evidence from an observed fresh process after controlled restart; on-disk hashes alone are insufficient. Preflight also checks the current files against the expected release.
- Snapshot failure, invalid/incomplete trades, stale/degraded heartbeat, nonpaper execution, wrong experiment or release mismatch blocks dispatch before local state is changed.
- Model access configuration must pin provider/model, explicitly classify verified-free versus paid, and include a provenance/authorization reference. Paid authorization cannot be inferred from credentials. No configuration will be manufactured or approved by implementation tests.
- External branch does not upload whole strategy/hypothesis files. SSH publication uses the helper's stdin JSON and verifies its receipt.
- Pending uncertain reviews block calls; due reviews use the independent five-trade ledger. No-trade diagnostics do not trigger paid LLM steering in this branch.

## Tasks

- [x] Add worker-startup evidence and validated snapshot module/CLI with focused tests.
- [x] Add exclusive external watcher branch, strict runner construction and publication transport; test ordering, pending recovery, blocked preflight/access, maintenance and absence of legacy uploads.
- [x] Run focused/full verification, independent review and graph update; preserve read-only remote heartbeat evidence.
- [ ] Next reconcile release deployment, position contracts, wallet accounting and market checks before activation.

## Verified implementation and limits

Five snapshot tests and ten watcher tests pass. The startup fingerprint covers every package Python source, with process identity, start time and experiment-policy hash. Snapshot validation checks current disk hashes against startup hashes, healthy simulated execution, a live process, heartbeat freshness and observed strategy version. Configuration changing while the helper reads is rejected; malformed trade journals cannot reach model dispatch. Windows liveness uses the process-query API, never os.kill(pid,0); POSIX uses its nonmutating liveness probe.

An exclusive watcher-cycle lease spans validation, local sync, review and publication. The configured external branch uses the independent five-trade ledger and strict model runner; a pending candidate goes to the receipt publisher before any runner is opened. Invalid/expired/mismatched/unapproved model-access records reject before provider storage is created. The implementation does not create operational access approval. Successful publication attempts a fresh snapshot matching the receipt's candidate hash. Until that succeeds, supervisor_status.json explicitly labels the mirror awaiting_worker_refresh; it cannot claim the old snapshot is current. Tests cover both stale and successful refresh using separate worker/local directories. Whole-file strategy/hypothesis uploads are absent from the supervised branch.

Independent review identified stale-mirror reporting and a test accidentally using the operational default directory. Both were corrected and verified with isolated fixtures. Follow-up review found no additional scoped blocker.

Final verification:682full-suite tests passed in79.692seconds(session19092exit0). Graph94949exit0:3072nodes8737edges171communities. All verification handles are terminal; whitespace checks passed. Evidence/source hashes are in research/runs/supervised-watcher-v1.

The remote heartbeat captured16:08:49UTC October9 remains healthy training/simulated, strategy271, without a startup-release fingerprint; its economic gate remains blocked. This confirms readiness is not yet achieved. No new code/helper has been deployed remotely. Local operational owner policy, initialized ledger and model_access.json remain absent; the watcher maintenance marker remains active. No actual model request, remote publication, new paid service or real order occurred.
