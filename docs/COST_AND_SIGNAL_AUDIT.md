# Account costs and signal quality — 2026-09-13

**Finding:** the frozen RSI entry rule shows no repeatable positive advantage in this study. The primary final-quarter signals have negative mean raw returns at every tested horizon, and all primary period/horizon combinations lose after modeled costs. Lower fees alone do not establish a profitable strategy.

This audit does not change the live strategy. No real orders, private account requests, model training or parameter optimization were performed. The user reports actual fees and hosting/AI bills as unknown.

## Cost evidence

| Item | Finding |
|---|---|
| Kraken account fee tier | Unverified: no configured exchange credentials were found. |
| Research fee assumption | 0.8% taker per side, from the published tier-one schedule; not verified for this account. |
| Hosting bill | Unknown; no configured monthly amount. |
| AI bill | Unknown; token usage is recorded but rates/billed amounts are missing. |

Published reference: [Kraken fee schedule](https://www.kraken.com/features/fee-schedule). Account-specific rates can be read using [Kraken TradeVolume](https://docs.kraken.com/api-reference/account-data/get-trade-volume) when suitable account access is available. Public pair status does not establish regional or account eligibility.

The active local ledger covers 2026-08-05T07:44:02.788452+00:00 to 2026-09-13T06:23:41.954000+00:00. It has 0 malformed rows. Experimental state directories were excluded to avoid treating duplicated trial usage as an actual bill.

Recorded model `gpt-5.6-luna`: 606 calls, 9,773,396 input tokens and 137,097 output tokens. Missing usage fields: 0. These records do not establish what was charged.

If input/output rates are quoted per million tokens, the recorded token cost would be **9.773396 × input rate + 0.137097 × output rate**, before any caching, discounts, subscription allocation or billing adjustments. No price has been invented for this model.

### Public spread observations

Three brief public quote observations during this audit. These are not historical spread/slippage estimates or evidence of reliable maker fills. One basis point is 0.01%.

| Pair | Minimum spread, bps | Maximum spread, bps |
|---|---:|---:|
| ETHUSDT | 0.0399 | 0.0399 |
| LINKUSDT | 3.2939 | 4.8225 |
| SOLUSDT | 0.9917 | 0.9917 |
| XBTUSDT | 0.0129 | 0.0129 |
| XRPUSDT | 0.0740 | 0.1480 |

The assumed taker fee is 80 bps per side; quoted spreads in these samples are much smaller. Slippage can increase during volatility and is separate from the quoted spread. Raw public responses and timestamps are retained in `account_cost_audit.json`.

## Signal study

The frozen corrected 15m RSI entry rule is evaluated without an existing position and before cost/minimum gates. Outcomes start at the next open and finish at fixed 1/4/12/24-hour horizons. This is an event study, not a tradable portfolio backtest: overlapping opportunities, exposure, stops and position ownership are not simulated.

Recent development: October 2025–February 2026; evaluation: March–May 2026; final check: June–August 2026. Older stress: January 1–July 26, 2022. Each observation requires a complete contiguous 24h future window inside its partition, so all horizons share the same cohort. All periods were previously available to this project and are not certified unseen.

### Primary subset: signals at least 24h apart per asset

Net returns assume 0.8% taker commission and 0.1% slippage on each side. The average is per hypothetical trade, not account return.

| Period | Hold hours | Signals | Mean raw return | Mean net return | Net win rate | Mean favorable excursion | Mean adverse excursion |
|---|---:|---:|---:|---:|---:|---:|---:|
| development | 1 | 259 | -0.026% | -1.810% | 1.931% | 0.482% | -0.583% |
| development | 4 | 259 | -0.120% | -1.901% | 6.564% | 0.924% | -1.149% |
| development | 12 | 259 | -0.206% | -1.987% | 14.672% | 1.678% | -2.028% |
| development | 24 | 259 | -0.543% | -2.317% | 21.236% | 2.469% | -3.312% |
| evaluation | 1 | 146 | 0.086% | -1.699% | 2.740% | 0.419% | -0.353% |
| evaluation | 4 | 146 | 0.040% | -1.744% | 6.164% | 0.781% | -0.742% |
| evaluation | 12 | 146 | 0.169% | -1.617% | 10.959% | 1.432% | -1.205% |
| evaluation | 24 | 146 | -0.173% | -1.954% | 15.068% | 1.960% | -1.919% |
| final_check | 1 | 164 | -0.042% | -1.826% | 0.610% | 0.372% | -0.459% |
| final_check | 4 | 164 | -0.207% | -1.988% | 2.439% | 0.685% | -1.003% |
| final_check | 12 | 164 | -0.218% | -1.998% | 10.976% | 1.256% | -1.639% |
| final_check | 24 | 164 | -0.218% | -1.998% | 19.512% | 1.900% | -2.319% |
| older_stress | 1 | 367 | -0.112% | -1.894% | 2.725% | 0.716% | -0.813% |
| older_stress | 4 | 367 | -0.039% | -1.822% | 13.079% | 1.424% | -1.595% |
| older_stress | 12 | 367 | -0.053% | -1.836% | 23.978% | 2.709% | -2.923% |
| older_stress | 24 | 367 | -0.369% | -2.146% | 29.700% | 3.634% | -4.016% |

Favorable/adverse excursions are the best/worst prices observed during the horizon. They are not exit signals and cannot be assumed achievable; their ordering within an OHLC bar is unknown.

### Matched comparison

Controls are non-signal entry times for the same asset, ISO week, UTC four-hour clock bucket, fixed ATR/price band and trend state. The nearest eligible control is at least 24h earlier, so its outcome ends before signal entry. Matching never uses future returns. Unmatched signals remain in the absolute-return table but are excluded from paired differences. Different matched pairs can still share/reuse control periods.

| Period | Hold hours | Matched signals | Distinct controls | Occupied weeks | Signal minus control raw return | Descriptive 95% interval |
|---|---:|---:|---:|---:|---:|---|
| development | 1 | 182 | 182 | 22 | -0.004% | -0.178% to 0.210% |
| development | 4 | 182 | 182 | 22 | -0.041% | -0.284% to 0.295% |
| development | 12 | 182 | 182 | 22 | -0.285% | -0.700% to 0.258% |
| development | 24 | 182 | 182 | 22 | -0.332% | -0.833% to 0.351% |
| evaluation | 1 | 103 | 103 | 13 | -0.142% | -0.248% to -0.059% |
| evaluation | 4 | 103 | 103 | 13 | -0.182% | -0.530% to 0.128% |
| evaluation | 12 | 103 | 103 | 13 | -0.360% | -0.896% to 0.094% |
| evaluation | 24 | 103 | 103 | 13 | -0.965% | -1.698% to -0.204% |
| final_check | 1 | 118 | 118 | 13 | -0.096% | -0.172% to 0.001% |
| final_check | 4 | 118 | 118 | 13 | -0.185% | -0.454% to 0.113% |
| final_check | 12 | 118 | 118 | 13 | -0.444% | -0.906% to 0.080% |
| final_check | 24 | 118 | 118 | 13 | -0.028% | -0.942% to 0.631% |
| older_stress | 1 | 268 | 268 | 30 | -0.083% | -0.295% to 0.156% |
| older_stress | 4 | 268 | 268 | 30 | 0.155% | -0.223% to 0.570% |
| older_stress | 12 | 268 | 268 | 30 | -0.341% | -1.330% to 0.679% |
| older_stress | 24 | 268 | 268 | 30 | -0.435% | -1.571% to 0.791% |

Intervals use 2,000 fixed-seed two-week moving-block resamples, clustering all assets by the signal calendar week. At least five matched events and four occupied matched weeks are required. Intervals are descriptive, uncorrected across horizons/subgroups and not causal proof or a profitability guarantee. Calendar, volatility and trend matching cannot remove every confounder.

### Rejected signals, final retrospective quarter

These groups include overlapping signals. Minimum-size eligibility assumes a flat portfolio at the signal close, using the recorded exchange constraints and 50 or 300 USDT. It is illustrative, not actual historical order admission. Cost and minimum groups overlap.

| Subset | Hold hours | Signals | Mean raw return | Mean net return |
|---|---:|---:|---:|---:|
| all | 1 | 268 | -0.043% | -1.826% |
| all | 4 | 268 | -0.141% | -1.923% |
| all | 12 | 268 | -0.065% | -1.848% |
| all | 24 | 268 | 0.125% | -1.661% |
| cost_rejected | 1 | 261 | -0.048% | -1.831% |
| cost_rejected | 4 | 261 | -0.150% | -1.931% |
| cost_rejected | 12 | 261 | -0.092% | -1.874% |
| cost_rejected | 24 | 261 | 0.099% | -1.687% |
| cost_passed | 1 | 7 | 0.154% | -1.632% |
| cost_passed | 4 | 7 | 0.165% | -1.622% |
| cost_passed | 12 | 7 | 0.926% | -0.875% |
| cost_passed | 24 | 7 | 1.106% | -0.698% |
| below_minimum_50 | 1 | 183 | -0.054% | -1.837% |
| below_minimum_50 | 4 | 183 | -0.193% | -1.973% |
| below_minimum_50 | 12 | 183 | -0.193% | -1.974% |
| below_minimum_50 | 24 | 183 | 0.094% | -1.691% |
| below_minimum_300 | 1 | 17 | -0.003% | -1.787% |
| below_minimum_300 | 4 | 17 | -0.238% | -2.018% |
| below_minimum_300 | 12 | 17 | 0.104% | -1.682% |
| below_minimum_300 | 24 | 17 | 1.211% | -0.594% |

## Commission headroom

For each final-quarter primary horizon, this is the symmetric fee per side that would reduce its mean return to zero after the fixed slippage assumption, before running costs. It is a retrospective arithmetic ceiling, not an achievable account fee or future forecast. A negative ceiling means the average loses even with zero commissions.

| Hold hours | Break-even commission per side |
|---|---:|
| 1 | -0.121% |
| 4 | -0.204% |
| 12 | -0.209% |
| 24 | -0.209% |

## Verification and limits

Six new focused tests cover exact next-open horizons/cost arithmetic, gap and endpoint exclusion, elapsed-time signal spacing, preceding controls, disjoint control windows and sparse-cluster suppression. The full suite result is recorded in `verification.json`. Independent review prompted the disjoint-control and occupied-week checks before interpreting results; the study was restarted after those corrections.

Binance price bars are analyzed with Kraken fee/minimum assumptions. Account fees, bills, venue-specific historic execution and unseen forward outcomes remain unverified. Favorable retrospective subgroups will not be promoted without a separately fixed and evaluated strategy.

Reproduce with `scripts/audit_account_costs.py`, `scripts/run_signal_quality.py` and `scripts/report_cost_signal_audit.py` using the project Python environment. The first script reads local ledger/configuration presence and public Kraken data only. The signal script uses cached data only. Protocol, data/code hashes, full event rows and subgroup summaries are retained under `research/runs/cost-signal-audit/`.

## Recommendation

Pause further parameter tuning of this RSI entry rule. A new, explicitly specified entry hypothesis should demonstrate useful price movement before cost/execution optimization or capital increases. Keep the current strategy unpromoted; this audit provides no evidence for doubling capital within three months.

The final-quarter cost-qualified subset contains only seven overlapping signals. Its mean 24h raw move was approximately +1.106%, but its modeled net return was approximately -0.698%. The same subset was absent in the evaluation period and had a negative 24h raw mean in development. It does not establish a robust exception.

Actual Kraken fees, AI billing rates and hosting invoices remain necessary to establish account-level profitability. Until those are known, report costs as unknown and retain sensitivity scenarios. No billed amount or account fee has been inferred from the published schedule.
