# Paper beta operation and reporting

Current operation, October 25,2026: approved source `ed439e8` is installed on both hosts with original all-eight opening, clocks and limits preserved. Discord startup and first five-new-batch summary delivery are locally confirmed. At 12:26 UTC, protection is healthy and all eight accounts remain flat; new entries retain `supervisor_error_halt`. The first resumed supervisor failed, then two completed successfully; later success does not clear the latch. [Actual result](../../hermes-paper-operational-corrections/research/runs/paper-discord-reporting-v1/LIVE_CUTOVER_RESULT.md), [current progress](GOAL_PROGRESS.md).

At 19:39 UTC, a source-pinned read-only native snapshot attempt confirmed SQLite BUSY (5) at the publication gate, after a preceding attempt passed. This is one observed cause of intermittent rejection, not a diagnosis of every historical failure. The owner-approved [disabled local repair](../../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/README.md), `a79fb93`, passes 20 exact Windows package and 21 Linux checks; candidate20eb6af has passing evidence for all1,712correctness catalog cases through composed Windows/Linux runs. The original full run remains recorded as failed, with all nine missing-input errors covered by the restored eleven-case rerun. It has not been installed on either actual host. The scheduled supervisor was observed enabled with six recent completed runs at 19:32 UTC. No latch was cleared; no checked supervisor-latch recovery API was added.


October26 17:17UTC: the approved snapshot repair is installed on both hosts. Worker processing and ordinary supervision are healthy; a new confirmed summary follows five new batches. Original identities, clocks and the entry halt remain. The 10h 7m 44s cutover gap and initial failures are retained. [Execution and pending acceptance](../../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/LIVE_CUTOVER_RESULT.md). The next actual new post-cutover E: generation remains due October27 at00:15UTC; the verified October26 copy is earlier evidence.

The later [20:24 UTC read-only sample](../../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/FORWARD_OBSERVATION_20261025.md) confirms 84 recorded summary windows, no reported Discord gaps, original eight account hashes and zero orders/positions. Storage is35.7%of declared capacity; the only sampled entry reason is the retained supervisor halt, despite a fresh successful acknowledgment. Native reported primary equity is about EUR199.54 after estimated costs. The common journal returned BUSY, so the sample is not a verified common financial settlement or backup. The proposed snapshot cutover remains unapproved and unexecuted.

The October 26 scheduled E: copy has completed/off-host-verified stdout and TaskScheduler result0. An independent read-only [inventory/opening check](../../hermes-paper-operational-corrections/research/runs/paper-supervisor-retry-v1/DAILY_BACKUP_20261026.md) verifies527 recorded files, original manifest/opening and all eight account identities. The task remains enabled, with no missed runs reported at the observation. October 24 and 25 copies remain historical evidence; these checks are not restore drills. The Discord reporter is installed with the approved transition; [exact candidate and retained boundaries](../../hermes-paper-operational-corrections/docs/PAPER_DISCORD_REPORTING_CUTOVER.md).

The recorded cutover observation gap is6h1m15.689seconds, beyond the frozen300-second coverage limit. Preserve it: the original inception-to-day30/day180 continuous-path qualification cannot pass that requirement. Operational learning and the original November22 day30 checkpoint continue, with incomplete path coverage declared. This does not change the protocol or authorize resetting clocks, interpolating missing observations or changing safeguards.

The preparation evidence below is dated October 21 and preserves the then-disabled state. Its activation instructions are historical: never rerun activation/finalization or reset the committed opening. The reporting and backup acceptance rules still apply. The existing offline report tool requires a closed consistent copy; do not run it on live WAL databases or mislabel a live observation as a completed backup.

## Daily copy task — historical October 21 preparation

Windows task `Hermes_Paper_Backup_6c9d520` is installed **disabled**. Its action was corrected locally after two reproduced failures: a missing Python executable could return exit code zero, and a valid snapshot with nested members could exceed Windows' ordinary path limit during extraction. The action now stops on PowerShell errors and uses the Windows extended path prefix `\\?\` for the same E: destination. It changes neither the physical destination nor the approved runtime.

The six action checks exercise missing manifest, completed copy, same-day verified retry, transport failure, corrupt tar and failure to start Python. All six pass against the action read back from the installed disabled task. The transport executable is a local fixture that checks the exact SSH arguments and returns synthetic tar bytes. Native snapshot, transport, extraction, receipt/inventory validation and SQLite contents are exercised. This is not a real off-host copy or a run of Task Scheduler. [Installed-action results](../research/runs/paper-independent-ops-v1/backup/r-149749330100/verification.json), [disabled task receipt](../research/runs/paper-independent-ops-v1/backup/corrected-disabled-task.json), [preserved failing results](../research/runs/paper-independent-ops-v1/backup/r-079191356900/verification.json).

The daily trigger remains 00:15 UTC, with logon catch-up, `StartWhenAvailable`, `IgnoreNew`, hidden execution and a seven-minute task limit. Its explicit offset is retained: Windows uses an explicit trigger offset independently of local daylight-saving settings. [Microsoft trigger semantics](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-itrigger-put_startboundary).

After the authoritative opening and installed checks, enable only this approved task. Accept a daily copy only when all of these refer to the same invocation and UTC date:

- Task Scheduler's last-run time is recent and `LastTaskResult` is zero.
- `backup-last.stdout.json` is fresh, reports `completed` or `already_completed`, and has `off_host_verified=true` for the exact installed-manifest identity.
- The named generation exists on E: and its original receipt, inventory and file hashes validate.

A failure to launch Python may have no JSON output. A prior successful stdout file is insufficient; inspect the task result and log timestamps. A failed or missed copy remains a failed or missed copy. Read the last task result without running it:

```powershell
Get-ScheduledTaskInfo -TaskName Hermes_Paper_Backup_6c9d520 |
    Select-Object LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
```

The final installed manifest must still be copied byte-for-byte to the isolated PC supervisor directory before the task is enabled. No real generation has been copied, and neither the worker's ephemeral copy nor these synthetic checks establish recovery certification.

## Reporting all eight accounts

A working local [read-only operator tool](../research/runs/paper-operator-report-v1/README.md) implements this recipe. It prints JSON or Markdown from an explicitly supplied closed consistent snapshot, keeps all eight expected rows and checks account journals/common settlements before comparing arms. Sixteen checks pass, including swapped same-size reports, missing costs/accounts, exact UTC boundaries, malformed cached metrics and SQLite sidecars; independent review confirms the corrections. [Prepared candidate report](../research/runs/paper-operator-report-v1/prepared-current-report.md), [synthetic example](../research/runs/paper-operator-report-v1/synthetic-example-report.md).

```powershell
.\.venv\Scripts\python.exe research/runs/paper-operator-report-v1/operator_report.py --offline-snapshot --root ROOT-OF-CLOSED-COPY --start START-UTC --end END-UTC --format markdown
```

Use aware ISO timestamps. The tool only prints; keep captured output outside its input. It recomputes native calendar accounting from recorded equity and flags disagreements with cached JSON. It does not replay markets/trading strategies. Snapshot consistency remains caller-asserted, and exit0 does not imply complete coverage. Supervisor learning evidence stays explicitly unobserved until its separate audit is available through the existing reporting process. The local tool preserves the approved deployment candidate and the separate primary-pair qualification protocol.

Use the final manifest to enumerate adaptive and control accounts for EUR50, EUR100, EUR150 and EUR 200. Report every account, even if it never admitted a trade. Read a completed consistent snapshot or identify one completed common batch and the exact report timestamps; unrelated latest files are not evidence of a common comparison window.

For each account, include the following fields in a dated operator report:

| Field | Source and interpretation |
| --- | --- |
| Identity and coverage | Experiment ID, arm, initial EUR balance, original inception, requested window, observation count/spacing, first/last observation and common batch identity. A missing report is `not_observed`; incomplete coverage stays incomplete. |
| Net equity and P&L | Current `wallet_metrics.json`, including realized net P&L, open basis, entry fees and estimated liquidation value/costs. Include open inventory instead of relying on closed trades alone. |
| Operating costs | Hosting, AI, tools and funding, each with amount, currency, source and actual/estimated/missing quality. Retain the declared standalone cost assumptions and each arm's inference costs. A known-cost subtotal is not full net equity when another cost is missing. |
| Return and risk | Existing rolling return, running/rolling drawdown, daily Sharpe and their measurement/coverage reasons. Keep null/undefined values. Constant no-cost inactivity has zero return and undefined Sharpe, rather than a successful risk-adjusted result. |
| Trading activity | Closed learning trades separately from protective/risk-stop closes, open positions, candidate/admission activity, canonical `no_trade` decisions grouped by reason, and missing decision sources. Counts of decisions are not necessarily counts of independent trade opportunities. |
| Learning and operations | Unique closes since the previous review, actual review outcomes/changes/no-change/rejections, fixed-control integrity, halts, worker/supervisor errors, missed copies, elapsed operating time and stopped periods. Five closes are required before steering; inactivity does not justify inventing a review or increasing risk. |

The native wallet metrics do not aggregate skip reasons. For a consistent copy of each account's `trading.db`, open SQLite with `mode=ro` and bind the actual `[start, end)` window to the canonical decisions query below. Preserve the writer's timestamp representation; the current writer uses UTC ISO strings. Do not use an API that initializes or migrates an absent database merely to report it. Missing databases/tables or unverified window coverage are `not_observed`/incomplete, never fabricated zero counts.

```sql
SELECT action, reason, COUNT(*) AS decision_count
FROM decisions
WHERE decided_at >= ? AND decided_at < ?
GROUP BY action, reason
ORDER BY action, reason;
```

The native paired performance report evaluates the selected primary pair. Keep EUR 200 as that primary and show the four size comparisons separately; choosing whichever wallet looks best afterward would not preserve the preregistered comparison.

## Day 30 checkpoint limits

`checkpoint_30d.json` is a write-once **operational learning checkpoint**, with `coverage_requires_validation=true`. An offline native edge check reproduced a day-30 file with zero wallet reports when there had been no completed common batch. The file's existence therefore cannot establish all-eight coverage or completed operation. [Retained edge observation](../research/runs/paper-independent-ops-v1/reporting/no-batch-checkpoint-gap.json).

Preserve the original checkpoint unchanged. Produce a separate dated operator report that explicitly enumerates the eight expected accounts, labels absent ones `not_observed`, records halts/stopped time and validates coverage. Do not replace the original checkpoint with a later or synthetically completed one. Day 30 is not a profit certificate, a real-trading authorization or proof that EUR50–200 can grow after costs.

## Offline evidence

The [eight-account rehearsal](../research/runs/paper-independent-ops-v1/reporting/synthetic-report-summary.json) uses invented observations and positions, with network access blocked. It verifies explicit arm-specific costs, inventory valuation, seven complete measured reports and one deliberately missing-cost report that remains incomplete. A separate [activity fixture](../research/runs/paper-independent-ops-v1/reporting/synthetic-activity-summary.json) exercises the read-only canonical query. These inputs were hand-authored for correctness, not produced by strategy execution or historical market replay.

Thirty-four existing focused native tests passed across the recorded invocations. The first invocation also included two invalid test selectors; their loader errors remain preserved, and the corrected selectors passed. These scoped checks do not replace or enlarge the frozen 1,618-check release claim. [Test invocation record](../research/runs/paper-independent-ops-v1/reporting/focused-tests-terminal.txt), [corrected selectors](../research/runs/paper-independent-ops-v1/reporting/corrected-report-selectors-terminal.txt). Approved runtime/config/build/test source hashes remain the same; the backup action change is disabled operator preparation.
