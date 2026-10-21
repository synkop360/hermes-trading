# Staged profitability comparison

**Goal:** Execute the user's approved costs → timeframe → exits → sizing comparison without promoting a live strategy.

**Architecture:** Optional research policy extends the isolated backtest runner. Production trading rules and state remain unchanged. Fixed candidates and all results are retained; no AI training or parameter search.

**Design:** Approved in the conversation: maker entry with expiry, 1h entries with 4h trend confirmation, full versus partial exits, delayed trailing, fixed targets, bounded proportional sizing. Use 300 USDT as a diagnostic balance before the final 50/150/300/500 capital comparison; this is not a deposit recommendation. Retain 0.125% effective risk per trade, 5% symbol/10% total exposure and two slots. Research halt remains 8%.

## Protocol (fixed before results)

- Recent development: 2025-10-01 to 2026-03-01; evaluation: 2026-03-01 to 2026-06-01; final retrospective check: 2026-06-01 to 2026-09-01. Older stress period: 2022-01-01 to 2022-07-27. Prior research has seen these periods; none is certified unseen.
- Development selection requires at least 20 completed positions, positive net expectancy and return, and no halt. Choose highest net return among eligible alternatives at each stage; otherwise retain the stage's control and explicitly report no improvement demonstrated. Evaluate all alternatives later without changing selection.
- Stage 1: taker versus post-only maker entry (0.1% below signal close, one bar expiry, require a further 0.1% penetration; crossing opens reject). Stops remain taker. No maker fill on mere touch. No same-bar profit credit after intrabar entry; stop-first handling.
- Stage 2: selected execution with 15m control, 1h RSI recovery, 1h RSI recovery plus completed 4h rising SMA100 / price-above-SMA100 filter. Keep the 12-hour time cap constant.
- Stage 3: control, full exit, full exit with trailing activated at +1 initial stop distance, and full fixed 4 ATR target with RSI exits disabled. Initial stop retained. Delayed activation persists once reached. Fixed target tested with its own cost screen, not claimed as a forecast.
- Stage 4: control cap versus 5% of current equity capped at 25 USDT, at 50/150/300/500 USDT. Other risk settings unchanged. Include all-in full exit fallback when a partial would violate minimums in an explicit variant.
- Close-derived discretionary exits execute at next observed open in this experiment; protective stops/targets are intrabar stop-first. Report this methodological difference from prior results. Report fees, wins, losses, expectancy, profit factor, drawdown, no-fill/minimum rejections and 90-day results. Costs at 0/1/5/10 USDT per month.
- Freeze candidate JSON and data/code hashes. Use cached data only, no credentials or paid services. No automatic paper/live promotion.

## Tasks

- [x] Add failing tests for maker non-fills, causal higher timeframe filtering, delayed trailing, next-open exits and bounded sizing.
- [x] Implement optional research policy and preserve legacy default backtest behavior.
- [x] Freeze and run staged candidates; preserve every result and development selection decision.
- [x] Run final checks and older stress comparison, inspect trade accounting and causal behavior, run full tests and update graph.
- [x] Write results and operational limitations; report whether any candidate merits forward paper validation.

Completed 57 runs. No development alternative qualified. All 549 tests passed. Exact run sources and a proof that the subsequent halt-precedence guard does not affect any recorded run are retained with the results. See `docs/PROFITABILITY_COMPARISON_RESULTS.md`.
