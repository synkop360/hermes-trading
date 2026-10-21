# Return validation results — 2026-09-13

**Conclusion:** No tested strategy supports doubling 50 USDT in three months. No strategy is promoted and no live trading is enabled. The recent simulation is constrained to zero executable trades; this is not a profitable trading result.

## Continuous comparisons

Trading fees and slippage included; operating costs and taxes excluded. Each strategy starts independently with 50 USDT. Simple RSI is an additional reference strategy; corrected RSI preserves the reviewed strategy’s signal settings, with corrected execution assumptions.

| Window | Strategy | Ending USDT | Net return | Max drawdown | Completed positions |
|---|---|---:|---:|---:|---:|
| 2022-01-01–2022-07-27 | Simple RSI, 15m | 45.9952 | -8.01% | 8.01% | 152 |
| 2022-01-01–2022-07-27 | Corrected RSI recovery, 15m | 48.9762 | -2.05% | 2.16% | 30 |
| 2022-01-01–2022-07-27 | Trend/pullback, 4h | 49.9386 | -0.12% | 0.12% | 1 |
| 2022-01-01–2022-07-27 | Cash | 50.0000 | 0.00% | 0.00% | — |
| 2022-01-01–2022-07-27 | Equal-weight buy-and-hold | 17.6642 | -64.67% | 73.72% | — |
| 2025-10-01–2026-09-01 | Simple RSI, 15m | 50.0000 | 0.00% | 0.00% | 0 |
| 2025-10-01–2026-09-01 | Corrected RSI recovery, 15m | 50.0000 | 0.00% | 0.00% | 0 |
| 2025-10-01–2026-09-01 | Trend/pullback, 4h | 50.0000 | 0.00% | 0.00% | 0 |
| 2025-10-01–2026-09-01 | Cash | 50.0000 | 0.00% | 0.00% | — |
| 2025-10-01–2026-09-01 | Equal-weight buy-and-hold | 27.4447 | -45.11% | 66.15% | — |

The 2022 simple RSI run halted after crossing the 8% drawdown trigger. The trend candidate’s single completed 2022 position provides no useful evidence of an edge. Current order minimums and the conservative sizing assumptions prevent every attempted recent-data entry. In the recent corrected RSI continuous run, 843 entry attempts failed the cost screen and 44 failed the minimum-size check.

## Chronological partitions

No parameter search or model training was performed. The following are separate fixed-candidate runs with a fresh 50 USDT balance and prior bars for indicator warm-up. They are retrospective chronological partitions, not certified unseen samples.

| Dataset | Partition | Simple RSI | Corrected RSI | Trend/pullback |
|---|---|---:|---:|---:|
| cached_2022 | train | -7.77% | -0.19% | 0.00% |
| cached_2022 | validation | -6.36% | -0.35% | 0.00% |
| cached_2022 | holdout | -8.01% | -1.52% | -0.12% |
| recent_public | train | 0.00% | 0.00% | 0.00% |
| recent_public | validation | 0.00% | 0.00% | 0.00% |
| recent_public | holdout | 0.00% | 0.00% | 0.00% |

2022 partitions: January–March, April–May, June–July 26. Recent partitions: October 2025–February 2026, March–May 2026, June–August 2026. Endpoints are exclusive. The 2022 archive contains one gap per asset; indicator history resets at gaps. Recent source coverage is complete for all five assets from September 2025 through August 2026, with September used for warm-up.

## Three-month target and costs

Doubling requires 25.99% compounded monthly or 0.773% per day over 90 days. None of the strategies or benchmarks doubled in the observed rolling 90-day windows: 118 windows per continuous 2022 run and 246 per recent run. These windows overlap and are not independent trials or estimates of future success probability. The best recent buy-and-hold 90-day return was 29.44%, with materially larger portfolio drawdowns than the strategy limit.

No actual operating bills were available. Each active strategy result includes illustrative 0/1/5/10 USDT monthly cost deductions in the JSON. For the recent 335-day zero-trade strategy run, even 1 USDT/month costs about 11.17 USDT, reducing the hypothetical balance to 38.83 USDT. This is an ex-post sensitivity, not a rerun with costs reducing position sizes. Idle cash does not require the trading infrastructure.

## Implementation and recommendation

Execution corrections, frozen candidates, minimum-size checks and research tooling are implemented. The strategy remains unsuitable for the stated target on the evidence available. Prioritize verifying the real account fee tier and executable position sizes before committing capital. A larger balance can make orders executable, but does not establish positive expectancy. Increasing leverage or relaxing limits is not supported by these results.

The existing runtime state and running services are preserved. The fixed candidates are research artifacts, not a promoted live configuration. The original reviewed strategy bytes are retained in `research/candidates/original_strategy.yaml`. Both reflection paths honor `research.frozen: true`.

## Evidence and reproducibility

- Full outputs: `research/runs/return-validation/results.json` and per-dataset JSON files.
- Candidate fingerprints: `research/candidates/manifest.json`; run code/data fingerprints: `research/runs/return-validation/run_manifest.json`.
- Recent data: Binance public monthly spot 15m archives, 60 files, each checked against its published SHA256. URLs and hashes are in `research/runs/return-validation/recent_data/sources.json`.
- Kraken minimum cost, quantity and precision snapshot: `research/candidates/kraken_market_constraints.json`.
- [Kraken fee schedule](https://www.kraken.com/features/fee-schedule): conservative published tier-one assumption, not a verified account fee tier.
- [Kraken public pair metadata](https://api.kraken.com/0/public/AssetPairs?pair=XBTUSDT,ETHUSDT,LINKUSDT,SOLUSDT,XRPUSDT).
- [Binance public data archive](https://data.binance.vision/).

Prices are Binance observations with Kraken fee/minimum assumptions. Current constraints are applied retrospectively, not claimed as the historical fee schedule. OHLC fills, spread and latency remain approximations. The original strategy was previously selected on 2022 data; its development and reflection may overlap recent partitions. Buy-and-hold is an idealized fully invested fractional basket, not an order-minimum-validated deployment. See [operating notes](RETURN_VALIDATION_OPERATIONS.md) for details and reproduction commands.

The final backtest rerun after the review fixes reproduced the preceding result values exactly. No paid AI training, real orders or deployment was performed.

## Verification

All 539 tests passed in the main project after applying the changes. Independent review findings were corrected and the scoped re-review found no remaining critical issues. Regression coverage includes stop-fill races, unavailable market data, ATR/trailing protection, partial exit fee allocation across restarts, frozen candidates, next-open execution, order minimums, quantity rounding and drawdown liquidation after a data gap. The code knowledge graph was updated.
