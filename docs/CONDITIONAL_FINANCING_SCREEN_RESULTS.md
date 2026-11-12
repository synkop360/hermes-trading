# Conditional SOL financing screen

September29,2026. **No qualifying entries; do not advance this fixed rule to a full backtest.** This is a causal opportunity-count screen, not a claim about realized trading losses or every possible carry strategy.

The rule was registered before its outcomes in `docs/superpowers/plans/2026-09-29-conditional-financing-screen.md`. It considers long dated SOL and equal short perpetual, selecting the nearest listed expiry9–35 days away. At each Monday00:00 it uses the prior completed hourly quotes and168 preceding hourly funding rates. Last week's positive funding must exceed the entire positive entry premium plus a fixed round-trip fee/slippage estimate. The USD50 account's caps remain unchanged. This is related to earlier carry trials, with a different hedge and conditional entry; it is not fresh independent evidence.

| Signal-time result | Weekly dates |
|---|---:|
| Financing estimate fails fixed hurdle | 36 |
| No eligible maturity | 7 |
| Missing trailing funding | 5 |
| Qualifying provisional signal | 0 |
| Total, Oct6,2025–Aug31,2026 | 48 |

Of the36 usable comparisons,16 have positive trailing funding. The maximum funding-estimate/hurdle ratio is0.5671: even the strongest falls short of the registered cost/premium hurdle. This ratio is not a forecast confidence level or a realized return. The threshold is deliberately conservative because it covers the full positive premium over a weekly hold; failure does not prove lower-threshold approaches would lose. Changing it after seeing outcomes would be a separate trial, not validation of this rule.

No entry passed the financing gate, so execution quotes, position sizing with stops, overlap suppression, margin paths and realized P&L were not simulated. Size feasibility fields are signal-time diagnostics at fixed USD50 only, not a verified account simulation. The planned sample would also be too sparse for the stated advancement threshold even if some opportunities existed; no paper-readiness claim follows. Missing funding dates are exclusions disclosed before any return computation, not zero payments or silently dropped losing positions.

The live/history timing comparison at September29 16:27 UTC found the live absolute funding rate exactly equal to the history record labelled16:00. Combined with official continuous funding/rate-setting examples, this supports applying a rate labelled h to [h,h+1). It is a same-provider corroboration, not an independently audited account ledger. The entry filter conservatively excludes the current-hour rate entirely. The prior funding archive limitation remains; no substitute-venue funding is used.

Immutable artifacts in `research/runs/dated-small-account-v1/financing-counts-v1/` preserve five input/source fingerprints, protocol/script snapshots,82 raw hourly quote responses with source/time/hash manifests, every weekly decision and summary. All frozen input and response hashes were verified, as were finite diagnostic values and all36 failed comparisons. No production code, account, risk settings or orders changed. A source-graph refresh was run; no application test rerun was warranted by a research-only count script. Independent review is pending and no approval is claimed.

Next research should not relax this rule to force a result. Reassess whether an economically distinct edge is feasible at this capital and cost level, and prioritize genuine new evidence over repeated fitting to previously examined observations. The goal remains unmet.

Independent review subsequently completed: no material issues found. The reviewer verified48 decisions and their classifications, all five frozen inputs, both source/protocol snapshots and82 response hashes, causal input timing, maximum ratio and the report's distinction from realized/account performance. The earlier dated-feasibility sizing and quote arithmetic were also confirmed. This supersedes the pending-review status above; it does not establish dataset execution quality or profitability.
