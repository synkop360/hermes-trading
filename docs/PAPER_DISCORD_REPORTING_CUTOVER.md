# Paper Discord reporting candidate

Status: **owner approved deployment and Discord activation on October25**: “deploy a discord enabled version as soon as possible.” This authorizes the paper-only cutover below, preserving the existing supervisor entry halt. [Approval record](../research/runs/paper-discord-reporting-v1/live-cutover/owner-approval.json). Candidate `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9` is committed and independently reviewed. Exact-export Windows checks pass. Full discovery is terminal with two disabled-archive fixture deadline errors and 30 unrun cases; the full suite is not passing. Execution remains subject to the checked preflight, exact source pins and retained-state procedure below.

The paper migration stopped calling the legacy loop's notification path. This candidate gives the existing paired worker an optional background webhook reporter. It publishes final native EUR observations and canonical closes from all eight accounts, including partial/protective closes. HTTP, retries and delivery metadata stay outside financial transactions. The legacy loop and Discord command/control bot are not started.

## Exact source and retained identity

| Item | Value |
| --- | --- |
| Installed base commit | `49c851315b98bb33b1eb78180fb789afe2a49f3d` |
| Candidate source commit | `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9` |
| Runtime delta | `notifications.py`, `paired_paper.py`, `paired_worker.py`, `paper_boot.py` only; 129 module names retained |
| Installed supervisor release | `f5cae59278c4e99fc3fa3a33da4bf55890276b29370a28260e9cf3400729edfd` |
| Candidate supervisor release | `fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844` |
| Candidate runtime map | `0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c` |
| Experiment | `paper-beta-v1-20261021-6c9d520` |
| Original cost T0 | `2026-10-23T20:13:29.470165+00:00` |
| Original opening | `2026-10-23T20:13:56.622959+00:00` |
| Opening canonical hash | `50fd92318969675f72e7450148e77e5bb1d4f60d9d1963ec90cab3061e27c349` |
| Original manifest raw hash | `43ec91c55d05f6f04f7a227dd9adc23a2283dd0d788a2255903cb63ee60cbb64` |

The [offline export receipt](../research/runs/paper-discord-reporting-v1/export-verification.json) verifies 522 previous raw pins, preserves 516 previous files exactly, and checks 603 candidate files against the source commit after newline normalization. Four runtime files and the operator helper are intentional executable changes; unchanged watcher/native launcher hashes remain pinned. Use **`release-upload-ed439e8`** with its [603 raw pins](../research/runs/paper-discord-reporting-v1/release-upload-ed439e8.raw-sha256.json). Earlier 8f315b3 exports are superseded, and its unsuffixed export is incomplete after a Windows path-length failure. Ordinary Windows checkout line endings change unrelated runtime digests; do not substitute a checkout for the verified export.

The [release plan](../research/runs/paper-discord-reporting-v1/proposed-release-plan.json), raw hash `f191fc0efdc85bd9d454d823cdcfd63939a73e271affafdf314ca175327471dd`, changes only those four runtime digests from the installed successor. The operator helper admits that exact delta only from `49c8513`, under a separate reporting purpose. Existing backup-correction admission remains restricted to its original base and single-file delta. All six checked guards, protected-state checks, leases, explicit apply/approval reference, interrupted-write reconciliation and post-work rollback refusal remain enforced.

## Verification and scope

- [47 new local checks](../research/runs/paper-discord-reporting-v1/combined-final-green.txt) pass, including real native settlement, missing costs, partial/risk closes, pending recovery, stalled HTTP with continued protective processing, restart deduplication, all-eight rows/four comparisons and the checked both-host transition on invented state. The exact 603-file export additionally passes [164 Windows checks](../research/runs/paper-discord-reporting-v1/packaged-verification-ed439e8/packaged-verification-results.json), including native child processes with external HTTP denied and all raw pins unchanged.
- [Independent review and follow-up](../research/runs/paper-discord-reporting-v1/independent-review.md) reproduced and then verified fixes for omitted archive-protection close envelopes, default-executor shutdown delay and hidden estimated cost quality. No archive feature is enabled or expanded.
- Metadata IO uses a single outstanding daemon filesystem operation, never the event loop's default executor. Native `asyncio.run` subprocess tests cover stalled loading and writing; task cancellation alone is insufficient evidence. An OS call cannot be cancelled and may complete before process exit; it holds no financial lease. Interrupted/corrupt delivery metadata degrades reporting and is never used as financial evidence.
- A captured input that has events and a due summary produces one combined report, bounded to four parts of at most 1900 characters. Dispatch uses a fixed queue sequence cutoff, preserving the 16-event bound. Each part has at most three ten-second attempts with asynchronous backoff. An exhausted combined intent is not retried as a different summary-only intent on the next poll or restart. Excess events and gaps are disclosed. Confirmed finite markers do not promise exactly-once delivery: timeout/crash after dispatch can duplicate a message. [Independent bound/failure follow-up](../research/runs/paper-discord-reporting-v1/combined-followup-review.md).
- [Selected Linux coverage](../research/runs/paper-discord-reporting-v1/harness-review.md) passes 52/52 using the cached image, read-only source and no network. This is qualified harness evidence: only the two exact metadata-child outer startup allowances change from eight to thirty seconds; the original shutdown assertion remains below two seconds and the fake stall remains three seconds. Measured shutdown is 0.004/0.408 seconds. Original eight-second startup failures remain recorded. This is neither unmodified-deadline nor full Linux qualification.
- [Final full Windows result](../research/runs/paper-discord-reporting-v1/full-regression/discord-full-windows-v3.verification.json): 1,692 catalog cases, 1,662 executed, **1,657 passes / five Windows skips / two class-fixture errors**, with **30 cases unrun**, zero individual test failures/errors, no ResourceWarnings and all 374 source pins unchanged. The raw runner's passed=1,655 subtracts fixture error holders from testsRun; actual executed passes are 1,662 minus five skips. Preserve that original receipt and use the [separate classification](../research/runs/paper-discord-reporting-v1/quote-checkpoint-setup-review-20261025.md) for exact counts. Both errors hit the unchanged capacity deadline during checkpoint q3/control-segment q2 setup. They are different locations from the predecessor's individual q4 deadline error. No source-introduced reporting failure is established, but the run is **not a passing full suite** and does not execute those 30 cases. All 47 new cases pass within the full run.
- Archive/checkpoint features remain disabled and preserved. This reporting cutover does not qualify or repair them, relax their deadlines or claim current execution coverage for the missing cases. Earlier v1/v2 full attempts were interrupted for review fixes; v2 also exposed an overbroad harness guard blocking synthetic loopback. The corrected harness permits literal loopback and still denies external HTTP. All five Windows-skipped filesystem cases have separate qualified Linux evidence.

All implementation verification uses synthetic inputs and fake transports. No deployment, restart, Discord authentication/request, paid model call, market backtest, training, strategy/fee/risk change or real order occurred. Existing notification credentials have not been validated.

The separate [read-only installed observation](../research/runs/paper-operational-corrections-v1/live-cutover/observe_reporter_candidate_20261025.native-result.json), October 25 at 10:00 UTC, records 1,651 completed batches, no pending batch and zero orders/positions across all eight accounts. Primary adaptive/control net equity is EUR199.638030 after EUR0.361970 estimated hosting. Worker heartbeat is healthy, but new entries are blocked by the sticky `supervisor_error_halt` after three failures, despite later successful checks. This cutover preserves that latch. Restoring reporting neither resolves its cause nor authorizes clearing it. [Retained cron evidence](../research/runs/paper-discord-reporting-v1/supervisor-halt-diagnostic-20261025.md) confirms intermittent failures but cannot establish their underlying exception, which the launcher discards.

The daily PC backup task reports last result0 at October 25 02:15 local time, with fresh completed/off-host-verified stdout. A separate [read-only E: copy check](../research/runs/paper-discord-reporting-v1/daily-copy-verification-20261025.json) verifies all 525 original files / 149,572,282 bytes, inventory receipt and unchanged manifest/opening/eight account identities for the 00:00:26 UTC generation. This establishes a second daily verified copy, not a restore drill or future reliability guarantee.

## Owner-approved procedure

1. Revalidate actual installed process/release, unchanged opening/manifest/account/policy identities, paper/no-real-order/free-model settings and current native safety/volume status. Preserve all current positions and pending work; the dated zero-trade observation is not a current-state assumption. Pause only supervisor job `2f6c5088c6ff` and backup task `Hermes_Paper_Backup_6c9d520`; keep the legacy job paused. Drain their actual owned handles. Do not stop the shared Hermes gateway or delete pending records.
2. Stop the existing paper worker normally and confirm terminal ownership. Establish closed sidecar-free native state and consistent copies without deleting evidence. Prepare a **new** frozen both-host transition with the candidate operator helper, proposed release plan and original identity. Revalidate all protected state and economics; refuse any mismatch. A prepared plan from the prior cutover must not be reused.
3. Deploy the raw-pinned candidate using the finite preparation command below, withholding `/app/state/.paper-ready-discord-ed439e8`. Install only the four changed runtime files and reviewed operator helper on the PC after the watcher is terminal; preserve other installed raw bytes. Apply/reconcile the new transition through the existing checked operator procedure, retaining immutable intent, six component receipts and rollback evidence. Verify both actual hosts and all installed runtime/launcher/watcher pins before releasing readiness.
4. Create the distinct readiness marker only after reconciliation. Preserve original opening, cost clocks, deadlines, histories, counters, latches, all eight profiles, EUR200 primary, strategies and fees. Retain the marker for protective restarts. Do not initialize a replacement experiment or reset delivery/financial history to obtain a green result.
5. Resume only the existing bounded supervisor/backup schedules with current verified-free metadata and truthful usage. Verify native heartbeat, financial/protective processing, all-eight journal/report agreement and an actual E: backup generation. Existing admission blocks clear only through their original rules; no fabricated healthy receipt is permitted.
6. Approval includes the notifier's first real requests using the **existing webhook**. Observe a confirmed paper/simulated startup message and a complete financial summary after five new common batches; startup financial values may be unavailable until a new settled envelope exists. Expected messages contain all eight wallets, EUR200 primary, four same-size comparisons, individual cost quality, actual observation time and unknown coverage. No test trade or paid review is needed. A future natural close should produce its canonical EUR outcome. If no close occurs, report that limitation rather than forcing one.
7. Treat 401/403/404, timeout, corruption or delivery gaps as notification failures. Keep protection running. Report typed errors without webhook/token/response bodies and reconcile the actual same operation rather than repeatedly restarting or sending ad hoc test messages. This cutover does not start the old command/control bot.

Exact proposed start command, supplied as one structured Railway field after approval:

```sh
/bin/sh -c 'set -eu
deadline=$(($(date +%s) + 1800))
until [ -f /app/state/.paper-ready-discord-ed439e8 ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo paper_preparation_expired >&2
        exit 78
    fi
    sleep 2
done
exec /app/.venv/bin/python -m hermes_trading.paper_boot --root /app/state
'
```

Before new protected work, checked rollback may reverse the six release guards using the same immutable intent. After any new batch, renewal, import or other protected write, old-state rollback is refused: preserve history and reconcile forward. Never restore old databases over successor work, rewind clocks, clear risk latches or delete markers as a shortcut.

Reporting restoration cannot prove profitability. The historical six-hour observation gap remains a failed continuity condition under the frozen qualification protocol; healthy notifications cannot repair it. The original day-30 operational checkpoint remains November22, 2026 at20:13:56.622959 UTC. No winning strategy or improved return is established by this repair.
