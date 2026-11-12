# Restore automatic Discord reporting for the paper beta

October 24, 2026. **Proposed design only.** Approval is requested for disabled local implementation and synthetic checks. No deployment, restart, message send, credential validation, paid call, market backtest, training or trading change is included. The current experiment keeps running.

## Intent and present evidence

Restore useful container messages after the migration from `colocated/run_loop` to `paper_boot/paired_worker`. Actual PID1 inspection at17:11UTC confirms notifications enabled, a webhook present and validly parsed settings, but the running paper path never invokes the sender. Its relevant installed module hashes match the approved49c8513 export. [Diagnosis](../../../../hermes-trading/docs/PAPER_DISCORD_NOTIFICATION_DIAGNOSIS.md), [installed predicates](../../../research/runs/paper-operational-corrections-v1/live-cutover/inspect_discord_20261024.native-result.json).

Existing settings are `pnl_only`, summaries every5cycles and12hours, daily/weekly financial reports, errors enabled and heartbeat flag true. Further source inspection shows the old heartbeat flag was never consumed either; providing meaningful liveness reporting is an explicit addition, not restoration of demonstrated old heartbeat messages.

Success means correct simulated trade outcomes, operational changes and configured summaries reach the existing webhook after a separately approved installation. Failures or slow delivery must not delay protection, change strategy ownership, alter any wallet or falsify missing measurements.

## Approaches and proposed choice

1. **Recommended: optional background notifier inside the current paper process.** Capture compact observations at its existing completed-batch boundary; send from a separate asyncio task. This reuses authoritative settlement data and existing transport/configuration without another database reader or trading writer.
2. Separate read-only observer process. This isolates process failure but must contend with exclusive lifecycle/publication locks, coordinate several files and reconstruct a common settlement. It is more machinery for this repair.

The owner delegated routine implementation choices through the standing instruction to work independently. Choice1 is the author's proposal; the design/implementation scope is not yet approved. The legacy `colocated` entrypoint and command/control bot are outside this repair because they also start the old writer or expose mutating controls.

## Integration and data contract

- Add a reporter in existing `notifications.py`, reusing its settings parser and transport interface. Add only the necessary hooks in `paper_boot`, `paired_worker` and `paired_paper`. Preserve legacy notification behavior. Reuse existing runtime filenames, keeping the release-map key set unchanged.
- `paper_boot` owns the optional background task and cancellation. `run_worker` accepts an optional observer, absent by default. Its synchronous `offer(envelope)` never waits for a queue, network, retry, marker write or historical scan. It receives copied compact data, never live database handles or mutable financial state.
- Successful financial envelopes are emitted only after the common batch is fully committed and its account connections are closed. Reuse final in-memory measurements and existing heartbeat/latest-batch data. An ephemeral return value may carry observations; do not change the stored financial packet, account journal, original settlement schema or history to serve notifications.
- Validate experiment/opening/manifest identity, EUR200 primary, all eight expected account identities, common batch/input hash and matching observation time. Financial rows come from final settlement, including later risk sweeps. An unobserved or mismatched row stays unavailable; do not combine unrelated latest projections.
- Include native EUR net equity, closed net P&L, open inventory/liquidation value, costs and their actual/estimated/missing provenance, risk/entry status and decision counts/reasons. Do not sum trade percentage returns or subtract fees/slippage twice. Current-cohort differences are labeled adaptive minus same-size control; retain all eight rows and all four comparisons.
- Individual close events use canonical `(account, asset, position_id, closed_at)` identity, actual size and realized EUR P&L. Capture normal, partial and protective closes while canonical account data is already owned/read for settlement; include later halt sweeps. Reuse available position rows or one bounded exact-batch query under that existing ownership. Never scan full history or open/migrate a repository from the sender. A missing event source is explicitly unavailable.
- Operational/error observations may have no newly completed batch. Label them separately, with last-settled time; never attach them as a new financial outcome. All source/capture/configuration exceptions affect notification status only and must not enter trading/supervisor error counters, entry halts, leases or restart logic.

## Messages and cadence

Every message identifies paper/simulated execution and the actual observation timestamp. No message claims a winning strategy, a qualified return or a real fill.

| Existing setting/event | Proposed paper behavior |
| --- | --- |
| `pnl_only` | Send realized close outcomes; suppress routine order messages. Other existing modes retain their intended trade/order filters. |
| Every5cycles | One combined update per five newly completed common batches, with eight wallet rows, primary highlight, costs, decisions, liveness and entry admission. Repeated heartbeats/recovered reads do not increment the count. |
| Every12hours | UTC scheduled operational summary. Report the observed interval and known restart/coalescing/drop gaps; unknown coverage remains unknown. |
| Daily/weekly financial frequencies | Dated current paper financial snapshot at the configured UTC schedule. It is not a reconstructed historical period-end valuation or a native profitability qualification. Full interval P&L/Sharpe is unavailable unless supplied by verified native evidence. |
| Errors/admission changes | Bounded state-change alert, with repeated occurrences counted rather than posted continuously. |
| Heartbeat flag | Include liveness in configured cycle/periodic summaries and status changes; do not create an additional message every minute. If no summary cadence is enabled, a true heartbeat flag gets one15-minute liveness update. This interpretation is new and explicit. |
| Strategy version | Report an observed verified publication/version change. Do not infer profitability or an LLM review outcome from it. |

Container supervisor health is not a review audit: `success=true` can mean `not_due`. Report that distinction. Detailed `no_change`, rejection and review-response history remains unavailable from the container until actual PC audit evidence is supplied. This repair adds no new PC-to-container review bridge, model call or strategy writer.

First installation sends one current startup snapshot, not legacy history. Subsequent restarts retain delivery markers and disclose observation gaps. The existing six-hour trading observation gap remains recorded; neither notifications nor a report can repair the frozen qualification coverage.

## Bounded delivery and failure behavior

- One latest-summary slot coalesces routine snapshots; a16-entry event queue retains bounded compact event envelopes, each at most128KiB. Overflow/coalescing is counted and disclosed. Financial source records remain authoritative; a delivery gap is never represented as zero trading activity.
- All HTTP, retries and delivery-marker IO run in the sender task, outside trading transactions. No task/thread is started if notifications are disabled. Invalid enabled configuration disables/report-errors for the notifier only; paper protection continues.
- Reuse `send_notification` with an injectable Discord poster. Use `wait=true` for server confirmation, suppress allowed mentions, preserve configured thread parameters and do not follow redirects. Confirm a successful message response before marking delivery. Discord documents its confirmation parameter, content limit and mentions handling in [Execute Webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook).
- Bound text to1900characters per part and at most4parts per envelope. Summarize excess events and report their omitted count instead of unbounded splitting. A10-second outer timeout covers the whole envelope attempt, not just one network phase. A429 delays this sender using server `Retry-After`/`retry_after`; never sleep the worker. [Discord rate limits](https://docs.discord.com/developers/topics/rate-limits).
- Limit each envelope to3attempts with asynchronous backoff. Permanent authentication/not-found errors disable only delivery. A timeout or crash after dispatch is `delivery_unknown`; retry can duplicate. Reuse stable event/part tags and make no exactly-once promise. Unconfirmed or abandoned parts remain counted as gaps.
- One atomic reporter-owned JSON file, at most64KiB, binds experiment/opening identity and stores bounded success markers, counters and delivery status. It contains no credentials or raw HTTP response. Advance each part's success marker only after confirmation; never mark success while constructing messages. Corrupt/unwritable metadata degrades/disables the notifier without altering trading state or replaying unbounded history.
- Close transactions before delivery. On shutdown cancel the reporter; any best-effort drain is at most2seconds after trading work has finished. Never retain a financial lease to finish a message. Log only typed/redacted errors, never webhook URLs, tokens or arbitrary raw response bodies.

## Local implementation verification and later rollout

Use fake transport, clocks and synthetic eight-account inputs. No real Discord request is needed for local approval. Meaningful checks cover disabled/malformed configuration isolation; exact settled cohort and unknown costs; native EUR accounting; normal/partial/risk closes with duplicate IDs; healthy-but-not-due supervision; cycle/UTC schedules; split limits and mention suppression; success/429/permanent failure/timeout/marker failure; bounded overflow and restart gaps; stalled delivery with continued worker/protective progress; terminal cancellation and retained clocks/controls/history/limits.

Run affected worker/paired/notification/bootstrap tests and independent review, then appropriate release verification with all results and known baseline failures disclosed. Construct one exact successor with byte-preserved unchanged modules and the existing checked both-host release transition. The implementation does not authorize installation. Ask separately before the exact deployment/notification activation and any live test send; name the candidate and expected messages then. Preserve the running experiment and all safety limits throughout.
