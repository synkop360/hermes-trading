# Daily breakout signal study — results

Completed 2026-09-27. Frozen protocol dated 2026-09-14.

**Decision: reject this version under the pre-specified screening rule; do not advance it to a portfolio backtest or live trading.** The recent period has negative raw and net seven-day returns and a negative matched advantage. The earlier evaluation is inconclusive. Small samples limit generalization; this rejects advancement of this candidate, not all breakout strategies.

## Fixed hypothesis

BTC/USDT and ETH/USDT daily close above the previous 20 completed daily highs, above SMA100, with SMA100 rising relative to five days earlier. Enter at the following daily open. Seven-day holding return is the primary outcome; three and fourteen days are secondary. No parameter selection or model training. Proposed ATR stop and channel exit are not tested here.

## Primary seven-day results

These are mean returns per spaced signal event, not portfolio returns or compounded gains. Events are spaced at least fourteen days per asset; BTC and ETH can still be correlated.

| Period | Events | Net wins | Mean raw | Mean after trading costs | 95% interval for net mean |
|---|---:|---:|---:|---:|---|
| 2019–2022 development | 50 | 46.0% | +4.99% | +3.12% | -0.28% to +5.90% |
| 2023–2024 evaluation | 30 | 50.0% | +2.88% | +1.05% | -2.04% to +3.16% |
| Jan 2025–Aug 2026 final check | 11 | 36.4% | -1.56% | -3.31% | unavailable (insufficient observations/quarters) |

All eleven primary events in the final period occurred in 2025; none occurred in January–August 2026. The period label therefore does not imply signal evidence from 2026.

Costs assume 0.8% taker commission and 0.1% slippage on each side. Actual account fees, hosting and AI bills remain unknown. Net results exclude operating costs. The recent raw mean is already negative, so eliminating fees alone would not make that sample profitable.

## Matched comparisons

Controls use the same asset, quarter, trend filter, volatility band and prior 90-day regime, without the breakout. Each control precedes entry by at least fourteen days. Differences below are raw percentage-point advantages over those controls.

| Period | Matched / events | Unique controls | Matched quarters | Mean advantage | 95% interval |
|---|---:|---:|---:|---:|---|
| 2019–2022 development | 30/50 | 30 | 7 | +0.35% | -6.12% to +5.58% |
| 2023–2024 evaluation | 19/30 | 19 | 6 | +4.71% | unavailable (insufficient observations/quarters) |
| Jan 2025–Aug 2026 final check | 5/11 | 5 | 2 | -11.07% | unavailable (insufficient observations/quarters) |

Intervals resample calendar quarters jointly across both assets, with 2,000 draws and a fixed seed. At least twenty observations and six occupied quarters are required. These are descriptive intervals, with residual dependence and multiple-comparison limitations.

## Asset and horizon diagnostics

| Period | Asset | Mean net 3 days | Mean net 7 days | Mean net 14 days |
|---|---|---:|---:|---:|
| 2019–2022 development | BTC/USDT | -0.74% | +2.35% | +5.43% |
| 2019–2022 development | ETH/USDT | +0.55% | +3.89% | +8.78% |
| 2023–2024 evaluation | BTC/USDT | +0.90% | +2.63% | +4.19% |
| 2023–2024 evaluation | ETH/USDT | -0.46% | -0.76% | +0.62% |
| Jan 2025–Aug 2026 final check | BTC/USDT | -1.09% | -1.40% | -4.12% |
| Jan 2025–Aug 2026 final check | ETH/USDT | -4.49% | -5.61% | +0.76% |

Secondary horizons cannot replace the seven-day decision. Event-level favorable/adverse excursions, yearly and regime diagnostics, overlapping events and contextual buy-and-hold returns are retained in the result files. Excursions are hindsight extremes, not executable profits. Buy-and-hold endpoint returns are not comparable to a mean event return.

## Decision and next step

Advancement required both evaluation periods to have at least thirty spaced events, twenty matches in six quarters, positive lower interval bounds for net return and matched advantage, and positive net means for both assets. Neither period satisfies this gate.

Keep this hypothesis archived and do not spend on training or optimizing it from these results. A different hypothesis should have a distinct market rationale and a separately approved, frozen study. A larger starting balance would not change the negative mean percentage return measured in this event sample; future expectancy remains uncertain. This study provides no support for doubling capital in three months and cannot determine a minimum profitable balance.

## Data, scope and verification

- 208 checksum-verified [Binance monthly daily archives](https://data.binance.vision/?prefix=data/spot/monthly/klines/BTCUSDT/1d/) covering January 2018–August 2026; 3,165 contiguous daily bars per asset. 2018 supplies warmup. Binance prices are a proxy; execution-cost assumptions refer to Kraken.
- Chronological periods are retrospective, not certified unseen: prior project research examined parts of 2022 and 2025–2026. No new September data was added after resuming.
- Candidate, protocol, plan and source hashes were frozen before outcome evaluation. Archive extraction code is also included in provenance.
- Independent code review found no material correctness issues. All 825 event/horizon rows reconciled against causal features, prices, control timing/buckets, spacing, partition boundaries and frozen hashes.
- Full software suite: 560 tests passed. No live strategy configuration or trading orders changed in this phase.
- Artifacts: `research/runs/daily-breakout-v1/results.json`, per-period summaries and event rows, `protocol.json`, `protocol_plan.md`, `audit.json`, `data/sources.json` and `artifact_manifest.json`.
