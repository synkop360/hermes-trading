# Crowding and failed price continuation — fixed signal screen

Registered September28,2026 before signal returns. Hypothesis: expensive positioning that stops receiving price confirmation can unwind. This is a directional long/short event study, distinct from unconditional carry and spot trend-following. No guarantee of an edge. User capital is USD50, France; no real orders or paid services.

## Frozen rule

Assets SOL and XRP, chosen because current Kraken contract minimums fit the existing USD2.50 symbol cap and both have substantial trading volume and multi-year history. Today's selection introduces survivorship/availability bias; disclose it. Do not select either winner after outcomes.

At 00:00 UTC each day, compute the preceding UTC day's signed sum of relative funding rates, using only rates strictly before that boundary. Compare with the preceding 60 full days' daily sums, excluding the signal day from the percentile reference. Require all 61 daily windows complete; no filled gaps. Use the nearest-rank 90th and 10th percentiles of those 60 values (sorted positions 54 and 6, one-based).

Short when the signal-day funding sum is positive and strictly above its prior 90th percentile, but the completed signal-day price return is negative. Long when funding is negative and strictly below the prior 10th percentile, but completed signal-day return is positive. Otherwise no signal. Entry at 01:00 UTC, one hour after decision; exit exactly 72 hours later at 01:00. Signals for an asset are suppressed until the previous event exits. No training, parameter sweep, adaptive threshold, asset selection or additional indicator.

Kraken funding timestamp semantics receive a conservative one-hour publication lag: funding labeled 23:00 is only usable at 00:00, never earlier. Bybit funding is a settled discrete event; the prior-day window excludes the current midnight event. This is a prior positioning statistic, not a predicted future payment. Return calculations must separately respect each venue's continuous-hourly versus discrete funding mechanism. Do not transfer the hourly Kraken convention onto Bybit.

## Scope and partitions

First obtain multi-year Bybit prices/mark/funding as a **proxy-venue event screen**: 2022 development, 2023–2024 evaluation, Jan2025–Aug2026 recent check. Warmup uses available prior data; 2022 can start only after 61 complete daily windows. Reuse no optimized settings from other studies. Run both assets and retain all signals/rejections.

Kraken Sep2025–Sep2026 matching history is a subsequent portability check, not an independent market period: overlapping venues are correlated. Seven currently missing funding hours per asset invalidate affected feature windows and held-position funding calculations. Do not infer missing payments or accept a passing sample formed by undisclosed exclusions. State excluded days, entries and outcomes. Same-exchange analytics corroboration is not an independent data source.

Primary outcome is 72-hour signed net return on position notional, including funding and taker costs. Use 0.05% Kraken futures commission per leg on Kraken and published 0.055% global Bybit per leg on the proxy; fees use each executed notional. Slippage 0.1% per leg, stress 0.3%; preserve negative funding and short liability accounting. No maker-fill assumption. No returns from unsupported/unfinished paths. This event screen has no stop and is not a safe full portfolio model.

Advance only if both chronological evaluation blocks have positive mean after base and stress costs, at least 20 events each across at least six occupied calendar quarters per block, and a positive lower 95% confidence bound for net mean from a quarter-cluster bootstrap (2,000 draws, seed20260928, both assets sampled together). Recent Jan2025–Aug2026 has seven possible quarters. Report distributions, long/short breakdown, funding versus price P&L and cash benchmark; do not hide adverse tails or treat correlated events as independent. Sparse observations mean inconclusive, not success. No new rule may be tuned to this outcome without a new research-ledger entry and independent evidence.

If the event screen fails or is inconclusive, do not deploy it or bypass the gate. If it passes, independently review data/accounting, then validate actual USD50 portfolio exposure, stops, lot sizes, funding, margin, existing 0.5% daily-loss and 3% drawdown protections, actual account access/fees and operating-cost breakeven. Forward observations must be collected before a credible paper-ready claim; the screen alone cannot complete the goal.

## Tasks

- [ ] Collect and validate proxy history and Kraken portability inputs.
- [ ] Implement/test causal event selection and venue-specific signed accounting.
- [ ] Freeze immutable source/data/protocol fingerprints before outcomes.
- [ ] Evaluate once, review and record the result, including exclusions and uncertainty.

Ruling: user autonomy authorizes this additional research without another confirmation. It does not authorize paid data, a new exchange account, real trades or risk-limit relaxation. Cost if the hypothesis is wrong: research time and another disclosed failed trial, no capital loss from this study.
