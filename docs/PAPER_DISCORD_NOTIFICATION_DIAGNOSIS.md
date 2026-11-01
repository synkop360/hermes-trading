# Discord silence after the paper-beta migration

Confirmed October 24, 2026, at17:11UTC by inspecting the actual container and matching its relevant module hashes to the approved `49c8513` export. This is a notification-wiring regression introduced by the earlier `6c9d520` paper-beta migration. The subsequent backup correction changes only snapshot filename handling and did not cause it.

The former container entrypoint was `hermes_trading.colocated`. It started the legacy trading `run_loop`, its optional control API and optional Discord command bot. Webhook alerts, periodic P&L summaries and profitability messages were generated and sent by the legacy trading loop.

The approved replacement starts `hermes_trading.paper_boot`, which validates the retained paper experiment and calls `paired_worker.run_worker`. Neither this boot path nor the paired worker invokes the existing notification resolver/sender or starts the Discord bot. The files still exist in the installed image, but their messaging path is unreachable from the running worker. Writing a heartbeat JSON file does not send a Discord heartbeat.

Actual PID1 environment predicates and parsed settings establish:

| Setting | Observed result |
| --- | --- |
| Notifications | Enabled; configuration parses successfully |
| Webhook | Present; no URL or token emitted |
| Mode | `pnl_only` |
| Heartbeat notifications | Enabled |
| Cycle summary | Every5cycles |
| Periodic summary | Every12hours |
| Profitability report frequencies | Daily and weekly |
| Legacy bot configuration | Required bot/control variables are present; validity untested |
| Running known entrypoint | PID1 `hermes_trading.paper_boot`; heartbeat `ok`, execution simulated |

These enabled settings currently have no consumer in the paired paper path. Zero trades explains the lack of trade P&L alerts, but does not explain missing configured periodic reports. Those require the omitted sender. Credential presence does not prove a webhook or bot token remains valid; no Discord API/authentication request or test message was made.

Follow-up source correction: `notify_heartbeat` was parsed and stored but had no consumer in the legacy loop either. Its true value does not establish that the former container sent heartbeat messages. The demonstrated migration regression is the loss of the webhook dispatch/periodic-summary path and optional command bot; a new meaningful heartbeat interpretation must be explicit in any repair design.

Source evidence:

- [Current Docker entrypoint](../Dockerfile#L10): `paper` and `paper_boot`.
- [Paper boot](../hermes_trading/paper_boot.py#L15): only the paired worker is started, at line45.
- [Legacy colocated tasks](../hermes_trading/colocated.py#L58): legacy writer plus optional bot; bot startup at line128.
- [Existing webhook sender](../hermes_trading/notifications.py#L213) and [legacy notification dispatch](../hermes_trading/loop.py#L208), with periodic/trade dispatch at line960.
- [Original notification behavior](NOTIFICATIONS.md): configured periodic reports can report no trades.
- [Actual installed inspection receipt](../../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/live-cutover/inspect_discord_20261024.native-result.json): only presence predicates, validated nonsecret settings and code hashes.

Restoration belongs in a paper-compatible outbound reporting path. Reverting to `colocated` would restart the old trading writer, and reconnecting its full command bot would also reintroduce mutating controls. Those are unsuitable shortcuts for the current single-writer experiment. No fix, deployment, restart, notification-setting change or message send was performed by this investigation. The reporting omission is an outstanding feature defect; it does not require stopping the running paper experiment.


Subsequent owner-approved work: source `ed439e8` is now deployed as `3cf55164-902f-4493-9a8a-6c97d394c0b8`. Both actual hosts completed the checked release transition; original opening, clocks, all eight accounts, strategies, fees and risk limits remain. The reporter confirms startup and the first five-new-batch summary through validated webhook responses/local part markers. No remote message body or ID is retained. [Actual deployment record](../../hermes-paper-operational-corrections/research/runs/paper-discord-reporting-v1/LIVE_CUTOVER_RESULT.md).

The repair passes 164 exact-export Windows checks and52 qualified Linux checks. The full run retains two disabled-archive fixture deadline errors and 30 unrun cases; all 47 new checks pass. The qualification and original failed receipts remain disclosed.

A separate supervisor-error entry halt remains active. The initial post-cutover scheduled run failed; the following two completed successfully, preserving the latch as designed. Its underlying exception is unavailable because the unchanged launcher discards child output. Reporting restoration does not diagnose or clear it. No real order, paid model call, strategy change or risk increase was introduced.
