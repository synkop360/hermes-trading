# Supervisor snapshot diagnosis — October 25, 2026

Discord release `ed439e8` is deployed. This investigation concerns the separate sticky `supervisor_error_halt`; it does not undo the reporting deployment or establish profitability. Overall goal estimate remains 65%.

## Finding

A live read-only snapshot attempt at **19:39:19.564923 UTC** failed with a real `sqlite3.OperationalError`, numeric **5 / SQLITE_BUSY**. Its explicit cause chain reaches `paper_publication.assert_no_pending_paired_batch`, then `archive_quote_state.operations` while querying SQLite metadata. The publication gate opens the database read-only with `timeout=0` and wraps the SQLite exception in `ValueError`. There is no snapshot retry before the CLI exits unsuccessfully.

This establishes one observed cause of intermittent snapshot rejection: database lock contention. The observation does not measure the lock duration or prove release after the final failed call. It does not establish the cause of every historical scheduled failure, prove that the earlier instrumented cycle had the same nested exception, or prove a future retry will resolve every collision.

The first call in the same process passed at 19:39:16.560169 UTC. Three preceding native snapshot calls also passed at 19:35:02–19:35:15 UTC. The failure is intermittent, rather than a continuously invalid snapshot. No exception messages, snapshot payloads, environment values or credentials were retained by the typed observation.

## Evidence and operational boundaries

| Evidence | Observation and limit |
| --- | --- |
| [Observed native invocation](observed-native-invocation.json) | One unchanged guarded launcher cycle, 13:06:36–13:06:50 UTC, exited 1 at the remote snapshot subprocess before a model review. Original argv, kwargs and deadlines were retained. Operator environment equivalence to the cron daemon is not established. |
| [Restored schedule](schedule-restored.json), [evening schedule](evening-current-schedule.json) | The schedule was paused and drained for that single cycle, restored at 13:07 UTC, and observed enabled/scheduled with six recent completed executions at 19:32 UTC. It was not left paused. |
| [First CLI probe](snapshot-readonly-probe1.json) | Actual unchanged snapshot CLI passed at 13:15 UTC. |
| [Second CLI probe](snapshot-readonly-probe2.json) | At 19:32 UTC, the actual unchanged CLI rejected with `OperationalError` and its wrapped `ValueError` at the publication gate. Numeric SQLite code was not captured in this probe. |
| [Three typed calls](typed_snapshot_observation.native-result.json) | All three native `make_snapshot` calls passed; source hashes for all 129 runtime files matched the deployed map. This is not evidence that contention was fixed. |
| [Bounded typed observation](typed_snapshot_observation_bounded.native-result.json) | Two calls, then immediate stop on the first rejection: valid snapshot followed by numeric `SQLITE_BUSY`. Maximum allowed was twelve calls spaced two seconds apart; only two were needed. Source hashes for all 129 runtime files matched. No health receipts were published. |
| [Invocation review](guarded-invocation-review.md) | An initial local sanitizer privacy defect was found and corrected before the actual invocation; fixed-label/verified-source filtering replaced raw captured identifiers. |

The ordinary launcher cycle performed its normal lifecycle and health writes; it was **not** read-only. Its lifecycle count advanced 1006→1007. Reserved model attempts remained 0; the original April 21, 2027 deadline, strategy, owner, account, limits, launch file and runtime guards remained unchanged. The standalone snapshot probes were read-only observations and did not invoke the full watcher or publish health success.

No product source, deployed release, strategy, risk limit, opening, clock or latch was changed during this diagnosis. No backtest, training, paid inference, real order, private exchange request or ad hoc Discord send was performed. The original continuous-path qualification gap remains recorded.

## Proposed next action

The [narrow snapshot-contention design](../../../docs/superpowers/specs/2026-10-25-supervisor-snapshot-contention-design.md) proposes bounded recollection only for an actual SQLite BUSY cause. It retains all checks and the existing latch. It is awaiting approval for disabled local implementation and synthetic checks, not authorized for deployment or entry resumption.

No existing checked API clears the supervisor latch; see the [separate source review](../paper-discord-reporting-v1/supervisor-halt-recovery-api-review-20261025.md). Fixing the snapshot path alone will not resume entries. A checked recovery procedure and explicit authorization remain separate work.
