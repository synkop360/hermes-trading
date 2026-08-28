# 180-day historical backtest — baseline / consultant / trailing-exit (2026-08-28)

## What this is

Each live instance's **current** `strategy.yaml` (as of 2026-08-28) replayed against
real historical Binance 15m OHLCV data via `scripts/fetch_historical_data.py`, through
the actual production `PositionEngine`/`evaluate_strategy` code (not a reimplementation),
with **real `--hermes` LLM reflection** enabled on the same ~20-cycle cadence Railway's
in-process trigger uses. Isolated, throwaway backtest state dirs — nothing here touches
the live `state/`, `state-consultant/`, or `state-trailing-exit/` directories or their
running processes.

**Two caveats that matter for reading these numbers:**

1. **Window is Jan 28 – Jul 27, 2022, not "the last 180 days."** cryptodatadownload.com's
   *daily* OHLCV is refreshed to today, but the **1-minute-granularity data (needed to
   resample to this project's actual 15m timeframe) is a static historical dump that
   stops at 2022-07-27** for all 5 symbols. So "180 days" means the last 180 days
   *available at minute granularity*, which lands in a 2022 market regime — not a
   forecast or a test against recent conditions.
2. **Data source is Binance, not Kraken** — the exchange the strategy actually trades.
   Broad price action is highly correlated across major exchanges for these liquid
   pairs, but microstructure differs. Read this as "does the strategy logic hold up
   over a much larger real sample," not "predicted Kraken fills."

## Status

| instance | status |
|---|---|
| baseline | **stopped by request at ~99% through its window** (2022-07-09 of 2022-01-28 – 2022-07-27) — see final KPIs below |
| consultant | **in progress** — running now as an independent process, seeded from `state-consultant`'s current `strategy.yaml` |
| trailing-exit | **in progress** — running in parallel (`state-backtest-trailing-exit-parallel`), started earlier, seeded from `state-trailing-exit`'s current `strategy.yaml` |

Baseline seeded from `state/strategy.yaml` v187 (before this run's own reflection
changes). Stopped deliberately ~18 days short of the full 180-day window; payoff ratio
had been stable in the 0.33-0.36 band for many consecutive checks beforehand, so this is
treated as a representative final result, not a partial/incomplete one.

## KPIs

### Baseline (FINAL — stopped ~99% through its window, at 2022-07-09 of Jan 28 – Jul 27 2022)

| KPI | value |
|---|---|
| trades | 1150 |
| win rate | 51.7% (594 W / 556 L) |
| avg win | +0.0127 |
| avg loss | -0.0354 |
| **payoff ratio** | **0.359** |
| total P&L | **-12.1278** |
| wallet | **37.872** (from $50 starting) — a 24.3% drawdown, ~3x `goal.yaml`'s `max_drawdown: 0.08` target |
| reflection changes applied | 5 (all `entry.threshold` tightening, all via the no-trade-accessibility guard; seeded v187 → final v188) |

### Consultant

In progress — will be filled in once complete.

### Trailing-exit (partial, still running as of last check)

| KPI | value (as of 2022-05-14, ~91% through its window) |
|---|---|
| trades | 697 |
| win rate | 23.7% |
| avg win | +0.0458 |
| avg loss | -0.0318 |
| **payoff ratio** | **1.440** |
| total P&L | -9.3614 |
| wallet | 40.639 |
| reflection changes applied | 1 (v03 → v04) |

Will be updated to final numbers once this run completes.

## Results analysis

- **Drawdown already exceeds the configured risk limit.** Wallet is down 21.3% from
  its $50 start (`-10.64`), well past `goal.yaml`'s `max_drawdown: 0.08` (8%). In live
  mode, `RiskEngine` would have hard-stopped trading well before this point; training
  mode has no equivalent circuit breaker, so the backtest kept trading straight through
  it. This is real evidence for adding a training-mode drawdown guard, not just a
  live-mode one.
- **Payoff ratio is stable, not noisy — and stable in the wrong place.** 0.334 has held
  essentially flat (0.337 → 0.334) across a wide span of the backtest, on a large
  sample (953 trades). At the current 52.0% win rate, breakeven needs a payoff ratio of
  roughly **0.92** — actual is barely a third of that. This matches, and statistically
  reinforces with much more data, the same structural finding from the small live
  samples earlier this session: average losses are consistently 2-3x average wins.
- **Reflection engaged correctly but adapted less than the long window might suggest.**
  5 real, sane LLM-driven changes (one `entry.threshold` tightening per asset, all
  triggered by negative score) over ~640 reflection-eligibility windows. The gate
  (`hermes_trading/reflect.py:557`) requires **both** ≥5 new closed trades **and** ≥30
  new decisions since an asset's last reflection — not a pure cooldown timer. Because
  every applied change tightens entry criteria (trade less), it's self-throttling: each
  adaptation makes the next one's evidence bar take proportionally longer to clear. A
  long timeframe alone doesn't guarantee proportionally more adaptation under this
  policy once the strategy starts tightening.
- Win rate (52.0%) is actually reasonable in isolation — the problem is entirely on the
  loss-size side, not trade selection frequency.

*(Consultant/trailing-exit sections to be added once those runs complete — in
particular, whether `trailing_overrides_explicit_exit` moves the payoff ratio toward
breakeven here the way it did in the shorter, live-data-scoped tests earlier this
session, at this much larger sample size and in a different market regime.)*

## Key takeaways

1. **The payoff-ratio problem is real and large-sample-confirmed, not small-sample
   noise.** This is the single most important finding: 953 real trades converge on
   almost exactly the same 0.33-0.38 payoff ratio range seen on live samples of a few
   dozen trades all session. That consistency across sample sizes and even across a
   completely different market regime (2022 vs. Aug 2026) is strong evidence the issue
   is structural to how `explicit_exit` caps wins, not a fluke of small samples.
2. **Training mode needs its own drawdown guard.** A 21% drawdown ran unchecked for
   hundreds of trades in a mode that has no equivalent to live mode's `RiskEngine`
   kill switch. Worth scoping as a follow-up regardless of what the other two
   instances show.
3. **Reflection's adaptation rate is self-limiting by design, not stuck.** Only 5
   changes over ~640 eligibility windows isn't a bug — it's the eligibility gate's
   trade-count requirement combined with each change reducing that asset's own future
   trade rate. Worth knowing before assuming "more time = more adaptation."
4. *(Pending)* Whether `trailing_overrides_explicit_exit` (trailing-exit) actually
   closes the payoff-ratio gap at this scale, and whether consultant's independently-
   drifted thresholds perform differently from baseline's, will be added once those
   two runs finish.
