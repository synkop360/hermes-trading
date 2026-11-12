# Staged profitability comparison — 2026-09-13

No alternative qualified under the fixed development selection rule.

Research only. No live strategy, running service, account setting or trading balance was changed. No paid AI training or real orders were used.

## Fixed experiment and selection

Development: 2025-10-01–2026-03-01. Evaluation: 2026-03-01–2026-06-01. Final retrospective check: 2026-06-01–2026-09-01. Endpoints are exclusive. Each run starts independently with fresh capital; these are not segments of a single compounded account. All periods were previously available to this project and are not certified unseen.

The staged rule was fixed before results: at least 20 completed positions, positive net expectancy and net return, and no drawdown halt on development data. Among qualifying alternatives choose highest net return; otherwise retain the control. Retaining the control does not recommend trading it.

- costs: selected `control_taker_15m`; qualifying candidates: none.
- timeframe: selected `control_taker_15m`; qualifying candidates: none.
- exits: selected `control_taker_15m`; qualifying candidates: none.

## Strategy alternatives at 300 USDT

Returns include modeled trading fees and slippage, exclude operating costs and taxes, and mark remaining positions at estimated liquidation value. Trade counts count completed positions, combining partial legs.

| Candidate | Development return | Evaluation return | Final-check return | Completed trades (dev / eval / final) |
|---|---:|---:|---:|---:|
| Corrected 15m control | -0.193% | 0.000% | -0.068% | 11 / 0 / 4 |
| Post-only maker entries | -0.618% | 0.000% | -0.043% | 24 / 0 / 9 |
| 1h entries | -0.717% | -0.182% | -0.867% | 21 / 8 / 15 |
| 1h + 4h trend filter | -0.012% | -0.097% | -0.255% | 1 / 3 / 6 |
| Full RSI exit | -0.217% | 0.000% | -0.122% | 11 / 0 / 4 |
| Full RSI exit + delayed trailing | -0.240% | 0.000% | -0.128% | 11 / 0 / 3 |
| Full fixed 4 ATR target | -2.240% | -0.427% | -1.606% | 71 / 20 / 51 |
| Balance-based order cap | -0.719% | 0.000% | -0.145% | 18 / 0 / 5 |
| Balance-based cap + full-exit fallback | -0.831% | 0.000% | -0.145% | 18 / 0 / 5 |

Full exits changed development win rate from 36.364% to 54.545%, while net return changed from -0.193% to -0.217%. A higher win rate did not establish better profitability.

## Win rate, payoff and drawdown

These metrics are for the final retrospective quarter at 300 USDT, including losses and fees. Missing win rate/profit factor means the denominator is unavailable, not profitability.

| Candidate | Win rate | Average winner USDT | Average loser USDT | Profit factor | Net expectancy USDT/trade | Max drawdown | Open at end |
|---|---:|---:|---:|---:|---:|---:|---:|
| Corrected 15m control | 50.000% | 0.0909 | -0.1935 | 0.4698 | -0.0513 | 0.132% | 0 |
| Post-only maker entries | 44.444% | 0.1454 | -0.1419 | 0.8196 | -0.0142 | 0.243% | 0 |
| 1h entries | 6.667% | 0.0578 | -0.1851 | 0.0223 | -0.1689 | 0.915% | 1 |
| 1h + 4h trend filter | 16.667% | 0.0578 | -0.1516 | 0.0762 | -0.1167 | 0.323% | 1 |
| Full RSI exit | 50.000% | 0.0103 | -0.1935 | 0.0534 | -0.0916 | 0.171% | 0 |
| Full RSI exit + delayed trailing | 33.333% | 0.0080 | -0.1962 | 0.0203 | -0.1281 | 0.161% | 0 |
| Full fixed 4 ATR target | 21.569% | 0.0489 | -0.1319 | 0.1020 | -0.0929 | 1.621% | 1 |
| Balance-based order cap | 40.000% | 0.1423 | -0.2394 | 0.3962 | -0.0867 | 0.225% | 0 |
| Balance-based cap + full-exit fallback | 40.000% | 0.1423 | -0.2394 | 0.3962 | -0.0867 | 0.225% | 0 |

## Capital and order-cap sensitivity

The scalable cap is min(5% of current equity, 25 USDT); the original cap is 5 USDT. Effective risk budget remains 0.125% of equity per trade, symbol exposure 5%, total exposure 10%, at most two positions. Orders below minimum are rejected, never rounded up into extra risk.

| Candidate | Starting USDT | Development return | Evaluation return | Final-check return | Completed trades (dev / eval / final) |
|---|---:|---:|---:|---:|---:|
| Corrected 15m control | 50 | 0.000% | 0.000% | 0.000% | 0 / 0 / 0 |
| Corrected 15m control | 150 | -0.466% | 0.000% | 0.069% | 10 / 0 / 3 |
| Corrected 15m control | 300 | -0.193% | 0.000% | -0.068% | 11 / 0 / 4 |
| Corrected 15m control | 500 | -0.116% | 0.000% | -0.043% | 11 / 0 / 4 |
| Balance-based order cap | 50 | 0.000% | 0.000% | 0.000% | 0 / 0 / 0 |
| Balance-based order cap | 150 | -0.466% | 0.000% | 0.069% | 10 / 0 / 3 |
| Balance-based order cap | 300 | -0.719% | 0.000% | -0.145% | 18 / 0 / 5 |
| Balance-based order cap | 500 | -1.366% | 0.000% | -0.145% | 30 / 0 / 5 |
| Balance-based cap + full-exit fallback | 50 | 0.000% | 0.000% | 0.000% | 0 / 0 / 0 |
| Balance-based cap + full-exit fallback | 150 | -0.304% | 0.000% | -0.101% | 10 / 0 / 3 |
| Balance-based cap + full-exit fallback | 300 | -0.831% | 0.000% | -0.145% | 18 / 0 / 5 |
| Balance-based cap + full-exit fallback | 500 | -1.346% | 0.000% | -0.145% | 30 / 0 / 5 |

## Older stress period

2022-01-01–2022-07-27 (end exclusive), independently starting at 300 USDT.

| Candidate | Return | Trades | Drawdown |
|---|---:|---:|---:|
| Corrected 15m control | -2.726% | 76 | 2.774% |
| Balance-based order cap | -4.828% | 89 | 4.893% |
| Balance-based cap + full-exit fallback | -4.725% | 89 | 4.791% |

## Running-cost sensitivity

Monthly 0/1/5/10 USDT costs are deducted ex post in every result JSON. This does not rerun sizing as bills reduce available funds. A 92-day quarter costs approximately 3.07 USDT at 1 USDT/month, or 15.33 USDT at 5 USDT/month. At 300 USDT those subtract approximately 1.02 and 5.11 percentage points respectively.

## Execution assumptions and limits

- Signals use closed bars. Market entries and discretionary exits execute at the next observed open. Stop/target checks use intrabar OHLC and prioritize stops when both are touched. This next-open discretionary-exit treatment differs from the earlier validation report.
- Maker entries rest 0.1% below the signal close, rounded down to the recorded tick. Crossing opens reject; a fill requires a further 0.1% penetration during the next bar, otherwise the order expires. Admission, quantity, funds, exposure and slots are reserved before reading that bar's low. No same-bar target profit is credited after an intrabar maker entry. This is an OHLC execution proxy, not exchange queue/order-book replay; maker performance requires forward verification.
- Tier-one modeled fees: 0.8% taker and 0.4% maker. Market slippage: 0.1% per leg. Maker fill price is the resting limit; sizing and entry screening retain a conservative two-leg slippage reserve. Actual account tier was not queried.
- The 4h filter requires 101 completed, contiguous 4h bars, a rising 100-bar SMA and close above it. Hourly variants retain the 12-hour holding limit. Other inherited RSI thresholds remain fixed.
- Delayed trailing starts only after a closing price reaches +1 initial stop distance, remains activated thereafter, and never removes the initial protective stop. Full RSI exits remain available. Fixed-target candidates suppress RSI exits and retain stops and the holding-time limit.
- The fixed target is 4 ATR, with its own distance-versus-cost gate. Target distance is not estimated expected profit; observed net expectancy is reported separately. No fitted target-reach probability or parameter search was used.
- Maker fees and fixed targets also change which entries pass the cost screen. These comparisons measure the resulting strategy, not a pure fee saving or exit change on an identical list of trades. Lower fees are not intrinsically harmful; additional qualifying trades can still lose money.
- Full-exit fallback is checked both at the exit signal and at the execution open, including slippage when checking quote minimums. Protective stop precedence is retained.
- Binance historical bars use the recorded Kraken minimums and fee assumptions retrospectively. The old dataset has known gaps; histories reset at gaps. Current exchange/account eligibility and historic fee changes are not modeled.
- Simultaneous candidates retain deterministic asset ordering. This experiment did not fit opportunity rankings or correlation forecasts. It is a staged comparison, not an exhaustive search of interactions between execution, timeframe, exits and capital.
- Research drawdown halt remains 8%, with possible gap overshoot. This is not an authorization to change stricter live limits. There is no leverage or borrowing.
- Research policy options are not live-deployable strategy settings. Any promotion requires execution parity work and forward paper validation; no promotion occurred.

## Verification and reproducibility

All 549 tests passed after implementation. New tests cover maker penetration/crossing, resting-order slot reservation, causal higher-timeframe filtering, persistent delayed trailing, next-open exits with gap-stop precedence, implicit RSI suppression, execution-time partial fallback and halt precedence after missing bars. Independent review findings were corrected and re-reviewed.

After the historical runs, one guard was corrected so a previously triggered drawdown halt overrides a queued partial sale after a missing bar. The exact run source is preserved in `code_snapshot/`. `verification.json` verifies that the only subsequent research-code difference is this guard and that none of the 57 runs ever halted (the flag only changes from false to true). Therefore the changed branch cannot affect the recorded runs. The new halt regression test passed; the historical simulations were not repeated after this guard-only correction. A future rerun on current code will correctly invalidate the old code fingerprints.

Run ` .venv/Scripts/python.exe scripts/run_profitability_comparison.py ` from the repository to reproduce; matching fingerprints reuse results. Run ` .venv/Scripts/python.exe scripts/report_profitability_comparison.py ` to regenerate this report. No credentials are needed.

Artifacts: `research/runs/profitability-comparison/results.json`, `protocol.json`, `selections.json`, frozen `candidates/` and individual result files. Each result fingerprints candidate settings, code, runner, source data, dates, warmup, capital and drawdown limit. The original return-validation artifacts are preserved.

## Decision

No candidate is promoted. The tested changes do not establish sustained profitability or support doubling the starting balance within three months. Increasing capital and order caps enabled additional trades but did not establish a profitable sizing threshold. The positive 150 USDT final-quarter control result was approximately 0.10 USDT on three completed trades, before operating costs, following a negative development period.

Keep these changes as research capabilities. Further work would need a separately specified strategy hypothesis and forward evaluation; widening risk limits or adding capital is not justified by this comparison.
