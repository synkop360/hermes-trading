# Frozen daily-breakout signal study

User approved freezing the specified daily breakout and running its signal study, including additional historical data. No portfolio backtest, live changes, paid services, training or parameter optimization are authorized by this phase.

## Hypothesis and fixed protocol

- BTC/USDT and ETH/USDT daily UTC close exceeds every high in the previous 20 days (signal day excluded), exceeds current SMA100, and current SMA100 exceeds SMA100 five days earlier. Require 105 contiguous daily observations. Evaluate every qualifying day before portfolio constraints; do not introduce an unapproved first-breakout-only condition.
- Frozen candidate: `research/candidates/daily_breakout_v1.json`. Proposed stop/channel-exit rules are retained there but are NOT evaluated by this event study. No winner chosen among parameter variants.
- Primary outcome: seven-day return from next daily open; secondary: three and fourteen days. Include actual next-open gaps in the price path, 0.8% taker commission per side and 0.1% slippage per side; account fees/bills remain unknown. All horizons require a common complete contiguous 14-day outcome within the partition.
- Primary cohort: earliest qualifying signal per asset, then 14-day spacing selected before future-outcome availability. Retain all overlapping signals as descriptive data. BTC and ETH observations can remain correlated.
- Data: primary Binance monthly spot 1d archives January 2018 through August 2026, SHA256 verified against published CHECKSUM files. 2018 is warmup. Development 2019-01-01–2023-01-01; evaluation 2023-01-01–2025-01-01; final retrospective check 2025-01-01–2026-09-01. Endpoints exclusive. Source prices are Binance, execution-cost assumptions Kraken. These chronological periods are not certified unseen; earlier project research examined portions of 2022 and 2025–2026.
- Controls: same asset and calendar quarter, passing the same SMA trend condition but not the breakout condition, same fixed ATR14/price band (<1%, <2%, <4%, >=4%), and same prior 90-day return regime (>+10%, <-10%, otherwise sideways). Use the nearest preceding eligible control at least 14 days earlier. Its complete outcome must finish before signal entry; no outcomes used in matching, no relaxed fallback. Allow reuse but report it.
- For primary-cohort net means and paired raw-return differences: descriptive 95% bootstrap intervals, 2000 draws, fixed seed 20260914, all assets jointly clustered by calendar quarter. Require 20 observations and six occupied quarters for an interval. Calendar quarters are resampled as blocks; residual cross-quarter dependence and multiple comparisons remain limitations. Only the 7-day horizon drives advancement.
- Advance to proposing an executable backtest only if BOTH evaluation periods have >=30 spaced signal events, >=20 matched events in >=6 occupied quarters, positive mean net 7-day returns with lower interval bound >0, positive paired difference with lower interval bound >0, and positive per-asset mean net 7-day returns. This screening rule is not proof of future profitability. Non-positive primary net return or matched advantage in either evaluation period rejects this version; otherwise insufficient samples/uncertainty mean inconclusive. Do not substitute a favorable secondary horizon.
- Report primary/secondary returns, net win rate, excursions, matching coverage, fees and regime/year diagnostics. Excursions are untradeable hindsight extremes. Cash/asset buy-and-hold endpoint returns are context, not a portfolio comparison to mean event returns. Stop/risk/minimum behavior and doubling probability cannot be inferred from this event study.
- Freeze candidate, protocol, data and code hashes before evaluating outcomes; retain all event rows and diagnostics.

## Work

- [x] Verify archive data and freeze provenance.
- [x] Write and run causal breakout/control/bootstrap tests, implement isolated helpers.
- [x] Execute fixed study and inspect independent review; reconcile timing/counts/hashes.
- [x] Report pass/reject/inconclusive against fixed rules, verify software and update graph.
