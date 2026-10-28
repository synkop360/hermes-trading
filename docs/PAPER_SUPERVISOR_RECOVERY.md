# Checked paper supervisor recovery

Updated October 28, 2026. Candidate `ea752c5` is deployed; see [cutover evidence](PAPER_SUPERVISOR_RECOVERY_CUTOVER.md). The owner subsequently explicitly requested an unhalt. The checked acknowledgment completed at 13:26:33 UTC; see [recovery receipt and scope](PAPER_SUPERVISOR_HALT_ACKNOWLEDGMENT.md). Never use this guide to bypass a newer error, another safety blocker or missing evidence.

## Why the latch stays set

Three supervisor errors intentionally stop new entries. Later success records real health but does not remove that halt. The longer SQLite BUSY retry addresses the reproduced collection failure; it does not reset the latch. No worker, scheduler, notification or health-delivery path automatically calls recovery.

## Prepare a later approved action

1. Verify the completed two-host transition from `20eb6af` to `ea752c5`, the actual source, launch configuration and deployment identity. Any recovery requires explicit owner authorization; the October 28 acknowledgment records the owner's request to unhalt. The free-form `approval_ref` below records approval; it does not grant it or authorize clearing a future unrelated halt.
2. Install the reviewed source on both hosts, preserve original clocks/history/policy, and observe successful ordinary scheduled supervision. Diagnose unexplained continuing failures before considering recovery. An operator-generated snapshot does not substitute for scheduled health.
3. On the actual PC, call `paper_supervisor_health.prepare_recovery_pc_evidence(project, launch_file)`. It reads the checked project, activated native launcher and matching owner/account/lifecycle files. The installed wrapper must be beside the JSON file with the same stem and `.py` suffix. Maintenance, an explicit operator stop, exhausted budgets, stale/stopped lifecycle or pending health delivery rejects preparation.
4. Transfer that small JSON evidence through the authenticated operator channel. On the worker, call `build_recovery_request(root, approval_ref=..., pc_evidence=...)`. Save the exact returned request without editing it. It binds the full safety state, last supervisor event, both-host release, manifest/opening, all eight accounts, €200 primary, current settlement, policies and PC evidence.
5. Encode those exact JSON bytes as base64. Invoke the module with `--root`, `--recovery-base64` and explicit `--enable-recovery`, with `HERMES_TRADING_MODE=paper` and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`. The ordinary `--request-base64` health path is unchanged. The request lasts 120 seconds; supervisor and worker health must each still be successful and no older than 60 seconds.

The PC evidence is an operator attestation, not a remote proof of possession or authentication token. The worker compares its source, policy and account bindings, but cannot inspect the PC filesystem. The actual PC check and authenticated transfer are required cutover steps. Do not substitute a hand-authored object or synthetic fixture evidence.

## What application checks

The preflight uses the native snapshot validators and original publication/risk gate. It verifies the authoritative opening and eight wallet copies, unchanged startup configuration, settled goals/strategies, all finalized current settlements, fresh market evidence, current verified-free model access, volume/entry admission, and the existing daily backup with its original verifier. Missing files or schemas reject; no database is created or migrated. All eight accounts must be flat, all orders terminal and all incidents resolved.

The existing rollback-journal publication lease excludes the single paired writer through the safety transition. Account WAL read transactions retain the settlement view under that lease. This relies on the approved single-writer architecture; an unrelated writer or concurrent operator configuration edit is not supported. Configuration files are checked twice. SQLite queries and backup verification have a 20-second preflight deadline; the original artifact-lock behavior is retained. A later remote invocation must also have a finite process timeout. No database journal mode or ordinary protective-exit behavior changes.

Under the safety-state lock, the full expected state and event must still match. Recovery changes only supervisor `entry_halt` to false and `consecutive_errors` to zero. It preserves actual event time, identity, success/error, the worker row and all financial records. Clearing this latch does not promise an entry, a winning trade or profitability.

## Interrupted or ambiguous replies

The audit directory `paper_supervisor_recovery` contains immutable intent and completion/cancellation receipts. An intent retains the exact request, original state and target hash. A separate pending marker blocks entry admission until the verified transition and receipt agree. Files are bounded, linked paths rejected, and at most 32 request IDs are allowed; there is no automatic deletion or retention extension.

- Retry the **same exact request** only after checking the saved evidence. A completed request returns a historical acknowledgment and never clears a later halt.
- While pending, fresh health continues to be recorded. Any new health invalidates an old apply request. Do not overwrite health or invent a fresh success to make it match.
- If interrupted recovery can no longer complete, the same command with `--abort-recovery` conservatively sets only the two supervisor halt fields on the current state. It preserves newer health and records cancellation. Abort may use an expired request with a matching saved intent; it never enables entries.
- A pending completion receipt still requires fresh preflight for completion; otherwise use checked cancellation. A cancellation receipt can reconcile marker cleanup only while the supervisor remains halted.
- Conflicting IDs, malformed/missing audit evidence, full audit capacity or failed storage checks require investigation. Never delete the pending marker or edit safety JSON to force progress.

Recovery errors emit only `supervisor_recovery_rejected`. The command deliberately does not print arbitrary exception messages. Keep the exact request and sanitized evidence so an ambiguous reply can be reconciled without repeating an unknown mutation.

## Local evidence and remaining limits

See [implementation ledger](../research/runs/paper-supervisor-recovery-v1/implementation-ledger.md) and [affected verification](../research/runs/paper-supervisor-recovery-v1/local-verification.json). These are synthetic correctness checks, including real SQLite contention, not market backtests or profitability evidence. Independent review was unavailable because the delegated reviewer reached its account usage limit. An exact release package, two-host transition, deployment verification and separately authorized live recovery remain required.
