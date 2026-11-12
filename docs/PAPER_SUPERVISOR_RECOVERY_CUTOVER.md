# Paper supervisor and Discord correction: execution

The owner said “Always deploy before stopping” and resumed this task. This is standing deployment authorization. No halt reset, real orders, paid calls, market backtests or training are included. The later delivered local-design approval is retained as the response to that earlier question.

Candidate `ea752c5f3990acff2a6f7164be733fe2bf92df35` is frozen in `hermes-recovery-ea752c5`: 671 files, 129 runtime modules. The release hash is `d8289ff44d543bebca1df87a566335f05366607ffc767d05eb00b75c6eddcf04`; the runtime hash is `ffe95446f146d79441862a3eae14228865fd7bf460fc7c7311f5ead3510834eb`. Its only runtime changes from the installed `20eb6af` are notifications, paper safety, supervisor health and supervisor snapshots. The watcher and native launcher are unchanged.

The package improves readable Discord reporting and bounded retries of confirmed SQLite BUSY errors. The audited recovery command is present but remains opt-in and will not be invoked. The existing supervisor halt is preserved.

## Executed October 28

Deployment `dd6ebf42-7cf9-4d85-addd-8b4605edf423`, instance `b295439f-053d-4911-8b55-5db4e01f00cc`, became ready at 07:29:14 UTC / 08:29:14 Paris. Both actual hosts passed the same frozen six-component transition and source validation. All eight wallet identities, original opening, clocks, history, strategies, limits and supervisor halt are retained.

The first preparation window expired overnight before state migration; restarting the same image reopened the finite gate. The original worker's last observation was October 27 at 21:22:56 UTC. The 36,378.7-second gap to readiness is retained, not replayed. A cold SQLite journal was reconciled through SQLite with original logical content/schema unchanged; 528 original snapshot files and both rollback copies were verified on E: before binding writes.

The exact package passed 215 affected cases on each platform (Windows: one symlink skip; Linux: no skips). This is not a full-suite pass. A focused independent source review became available after its limit reset and found no active blocker in the deployment tooling; its retry caveat is recorded in the execution ledger. This is not an independent audit of every runtime change.

Acceptance verifies one worker, paper mode, eight new batches, matching finalized wallet rows, a fresh confirmed Discord summary and a completed post-cutover daily backup on E:. The first ordinary supervisor run failed after access renewal and usage bootstrap. Three following runs succeeded at acceptance; a fourth subsequently succeeded. Its discarded traceback prevents a conclusive cause for that first failure, and no long-run improvement is claimed. Local notifier acknowledgments do not retain remote Discord message IDs or screenshots.

See [native operational acceptance](../research/runs/paper-supervisor-deploy-v1/live-cutover/OPERATIONAL_ACCEPTANCE.json). That acceptance retained the halt. The owner later explicitly requested an unhalt, and [checked recovery](PAPER_SUPERVISOR_HALT_ACKNOWLEDGMENT.md) completed at 13:26:33 UTC. Post-recovery processing verifies no entry-admission block. Model attempts reserved remain zero; no profit or LLM steering has been established.

The exact package must pass affected Windows and Linux tests before interruption. The broader transition run passed 29 of 30 tests; the remaining new test had a fixture error, corrected with all four new tests subsequently passing. This is not a full-suite pass. Independent review is unavailable due to the reviewer account limit; no alternative agent/model was used to bypass that limit.

Use the same checked six-component two-host transition as the prior snapshot cutover. The helper admits only the exact predecessor and four-module delta. Capture fresh original state on both hosts and verify copies on E: before changing bindings. Keep all eight original wallet identities, opening, cost clocks, history, fees, strategy, risk and supervisor halt.

1. Verify the current source, paper settings, positions/orders/pending work, free-model predicates, original identities and schedules.
2. Pause only the existing supervisor job and daily backup task, drain owned children, set a distinct 1,800-second readiness gate, stop and prove the predecessor terminal, then deploy the frozen package to the existing pinned Railway service.
3. Keep readiness withheld. Capture native closed originals, reconcile only a verified cold SQLite journal through SQLite itself if necessary, verify the complete E: copy and upload the closed PC reference.
4. Prepare one fresh native plan and separate root-bound intents. Verify both rollback copies on E: before any binding update.
5. Back up and install exactly four PC runtime files plus the operator helper. Reconcile all six checked bindings on each host; verify actual PC launch, both completed receipts and all original protected evidence.
6. Release readiness only after native validation. Restore the captured schedules, observe ordinary supervision and at least five new common batches, confirm a naturally delivered readable Discord summary, and record the interruption and any unavailable evidence.

Execution artifacts are in `research/runs/paper-supervisor-deploy-v1/live-cutover`. New marker: `/app/state/.paper-ready-recovery-ea752c5`. Existing service/project/environment are pinned in `release-plan.json`. No manual guard edit, database restore over new work, history deletion, cost-clock rewind or artificial trade/message is permitted. A lost reply requires actual-state inspection before reconciliation. After new protected work, preserve history and move forward rather than restoring older databases.
