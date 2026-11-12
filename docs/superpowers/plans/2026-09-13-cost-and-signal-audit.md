# Account economics and signal-quality audit

User approved this read-only account-cost audit and signal-quality analysis in the conversation. No trading, deployment, strategy changes, training or fitted parameter search.

## Fixed protocol

- Audit only project-configured credentials, cost ledgers and public Kraken quotes/minimums. Do not expose keys or raw authentication errors. Distinguish account-verified fees from published assumptions and missing bills from zero cost. Active `state/` is audited separately from experimental state directories; never sum duplicated trial ledgers into bills.
- Use the frozen corrected RSI 15m candidate, all five assets and existing cached bars. All signal observations are evaluated with no position, before cost/minimum gates, so rejected entries are retained. This is an event study, not an executable portfolio backtest.
- Periods: development 2025-10-01–2026-03-01; evaluation 2026-03-01–2026-06-01; final retrospective check 2026-06-01–2026-09-01; older stress 2022-01-01–2022-07-27. Endpoints exclusive. No outcomes cross partition endpoints; missing bars invalidate outcomes. Prior research has seen these periods.
- Fixed horizons: 1/4/12/24 hours. Entry is next bar open and exit is the last close of the horizon. Report raw return, return after 0.8% taker commission per leg and 0.1% slippage per leg (plus account fee scenario only if verified), favorable/adverse excursion. Excursions are hypothetical extremes, not realizable profit targets.
- Main inference subset: accept the first signal per asset then suppress signals for 24 hours. Report all overlapping signals separately. No signals selected using future outcomes.
- Match each signal to the nearest non-signal at least 24 hours earlier in the same asset, calendar ISO week, UTC four-hour clock bucket, fixed ATR/price band (<0.25%, <0.5%, <1%, >=1%) and trend state (close above rising SMA20 or not). No outcome enters matching; do not relax matching if evidence is sparse. Report unmatched count and control reuse. Controls must have full outcomes inside the partition and contiguous history. Each matched control outcome ends before or at signal entry. Different matched pairs can remain dependent.
- For paired raw-return differences, use a descriptive 95% two-week moving-block bootstrap across calendar weeks shared by all assets, 2000 resamples, fixed seed 20260913. Report insufficient evidence for fewer than four occupied matched calendar weeks or five matched events. Intervals are exploratory, not corrected for all horizons/subgroups and not proof of future profits.
- Classify signals by the existing cost gate and illustrative minimum-order eligibility at 50/300 USDT with a flat portfolio, signal-close pricing and current recorded constraints. This is not historical portfolio admission.
- Preserve protocol, source/data hashes, event rows and aggregate outputs. No promotion based on a favorable horizon or subgroup discovered here.

## Work

- [x] Aggregate local costs and capture bounded public quote/minimum observations; identify account/billing gaps.
- [x] Add causal horizon, gap, matching, sample-spacing and cost arithmetic tests; implement isolated event-study helpers.
- [x] Run fixed study, reconcile sample counts, review conclusions and verify code.
- [x] Write account-cost and signal-quality report, retain artifacts and update code graph.

Completed: 1,440 signals, 5,700 outcome rows, 555 passing tests. Six new regression tests and independent review cover causal timing, disjoint controls and sparse-cluster suppression. The user confirmed actual account fees and bills are unknown. No strategy promotion. The exact pre-run protocol plan is preserved in the audit artifacts.
