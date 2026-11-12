# October 25 operator report from the verified offline copy

Generated October 25, 2026 with the existing, unchanged main-project `paper-operator-report-v1/operator_report.py`, SHA256 `2e69b8b224dc3997cf8239873079e3e0dcd3048e743c0fd1960cd0aa6fac8535`. Both native CLI invocations exited 0 with empty stderr. The normal snapshot path worked; no extended-path workaround, helper modification, migration, initialization, network access, model/provider call, market replay, or snapshot write occurred.

[JSON report](operator-report.json), [Markdown report](operator-report.md), [invocation](invocation.json), [read-only verification receipt](report-receipt.json). Inputs are exclusively the completed `state-2026-10-25-73a6a6a8ab53` E: copy already covered by the root's [525-file verification](../daily-copy-verification-20261025.json). Outputs are outside the snapshot. Exit 0 means the helper produced a report; it does not mean continuity or profitability passed.

The requested activity window is **[2026-10-23 20:13:56.622959 UTC, 2026-10-25 00:00:26.323709 UTC)**, from the original opening to the captured copy. It spans 27 hours 46 minutes 29.701 seconds of calendar time, not measured operating time. All eight final observations belong to verified completed common batch `4cc93668-6310-47fe-8652-3a3f9340cccf`, observed **00:00:24.566136 UTC**; no pending batch was found.

Rounded display values below are EUR; full native precision and cost provenance remain in the JSON. Net equity already includes recorded costs.

| Account | Net equity | Recorded hosting cost | Orders / positions / closed positions |
| --- | ---: | ---: | ---: |
| adaptive_50 | 49.733756 | 0.266244 estimated | 0 / 0 / 0 |
| control_50 | 49.733756 | 0.266244 estimated | 0 / 0 / 0 |
| adaptive_100 | 99.733756 | 0.266244 estimated | 0 / 0 / 0 |
| control_100 | 99.733756 | 0.266244 estimated | 0 / 0 / 0 |
| adaptive_150 | 149.733756 | 0.266244 estimated | 0 / 0 / 0 |
| control_150 | 149.733756 | 0.266244 estimated | 0 / 0 / 0 |
| **adaptive_200 primary** | **199.733756** | **0.266244 estimated** | **0 / 0 / 0** |
| control_200 | 199.733756 | 0.266244 estimated | 0 / 0 / 0 |

Each standalone account has native closed P&L 0, open basis 0 and open liquidation value 0. A bounded read-only `SELECT COUNT(*)` check of the copied canonical order/position tables confirms the zero counts above; SQLite was opened with `mode=ro&immutable=1`, query-only and trusted-schema disabled. No sidecars appeared. Hosting is estimated; AI/tools are estimated zero and funding is actual zero. The scenarios are not pooled accounts, so their separate hosting charges must not be combined into an investment return. All four same-size adaptive-minus-control equity differences are **EUR 0**; this supplies no strategy edge.

Each account contains **72 recorded no-trade decisions**, all `entry_condition_not_met`, in the requested window. Decisions do not establish uptime, elapsed inactivity, independent opportunities, or 72 independent trials. With no closes and the separate supervisor audit absent, this is another dated operational/accounting observation; learning remains **not_observed** and no new strategy learning is established.

The native rolling 30-day metrics are **incomplete**, with `missing_calendar_boundary`; return, drawdown and daily ratio are unavailable. Their financial windows are separate from the requested activity window. The helper does not quantify within-window observation-gap duration in this output. This report neither verifies uninterrupted coverage nor repairs the previously recorded continuity failure. The checkpoint is `not_observed`; its original clock and records were preserved. Profitability qualification and real-trading eligibility remain false.

The copied diagnostic heartbeat is older than the final financial observation: **October 24 23:59:16.517560 UTC**, with entry admission unblocked, supervisor halt false and model readiness true. That describes this old copy only. The root's separate later **October 25 09:59:45 UTC** heartbeat observation has a sticky `supervisor_error_halt` despite later successful checks; [the diagnostic](../supervisor-halt-diagnostic-20261025.md) preserves that distinction and the unknown underlying cause. The offline report does not describe current admission, clear the latch, or authorize a restart or trade.
