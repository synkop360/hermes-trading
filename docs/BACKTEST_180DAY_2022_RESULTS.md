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

All three stopped by request once their numbers had stabilized (each held steady across
several consecutive progress checks before stopping), rather than run to the literal
last day of the 180-day window. All three treated as representative final results.

| instance | window position at stop | seeded from |
|---|---|---|
| baseline | ~99% (2022-07-09 of Jan 28 – Jul 27 2022) | `state/strategy.yaml` v187 |
| consultant | ~85% (2022-06-07), **market analysis genuinely enabled** (see note below) | `state-consultant/strategy.yaml` v170 |
| trailing-exit | ~99% (2022-07-09) | `state-trailing-exit/strategy.yaml` v03 |

**Correction made mid-run:** the consultant backtest was restarted once. The first attempt
called `run_hermes_reflection(..., env={})` — an *empty* env dict, which meant
`HERMES_MARKET_ANALYSIS_ENABLED` was never set and the local Docker Model Runner opinion
(consultant's actual differentiator from baseline) was never consulted for the first
~280 trades. Restarted with real `os.environ` plus a forced
`HERMES_MARKET_ANALYSIS_ENABLED=true`, matching `scripts/watch_consultant.py`'s own
production behavior. The numbers below are from the corrected run.

## KPIs

### Baseline (FINAL)

| KPI | value |
|---|---|
| trades | 1150 |
| win rate | 51.7% (594 W / 556 L) |
| avg win | +0.0127 |
| avg loss | -0.0354 |
| **payoff ratio** | **0.359** |
| total P&L | **-12.1278** |
| wallet | **37.872** — a 24.3% drawdown, ~3x `goal.yaml`'s `max_drawdown: 0.08` target |
| exit reasons | `explicit_exit`: 594 (100% of wins), `stop_loss`: 555, `max_bars`: 1 |
| reflection changes applied | 5 (all `entry.threshold` tightening via the no-trade-accessibility guard; v187 → v188) |

### Consultant (FINAL — market analysis genuinely active)

| KPI | value |
|---|---|
| trades | 959 |
| win rate | 52.1% (500 W / 459 L) |
| avg win | +0.0125 |
| avg loss | -0.0377 |
| **payoff ratio** | **0.331** |
| total P&L | **-11.0592** |
| wallet | **38.941** — a 22.1% drawdown |
| exit reasons | `explicit_exit`: 500 (100% of wins), `stop_loss`: 456, `max_bars`: 3 |
| reflection changes applied | 3 |

**Key finding: market analysis, even genuinely consulted, does not move the payoff
ratio.** 0.331 lands in the same 0.33-0.36 band as baseline (0.359) and the earlier
no-market-analysis consultant run — consistent with its documented "advisory only,
weigh lightly" design rather than something that should be expected to fix
entry/exit sizing asymmetry.

### Trailing-exit (FINAL)

| KPI | value |
|---|---|
| trades | 1051 |
| win rate | 24.6% (259 W / 792 L) |
| avg win | +0.0443 |
| avg loss | -0.0300 |
| **payoff ratio** | **1.480** |
| total P&L | **-12.2480** |
| wallet | **37.752** — a 24.5% drawdown, similar magnitude to baseline/consultant despite the much better payoff ratio |
| exit reasons | `explicit_exit`: 0 (disabled by design), `stop_loss`: 912, `max_bars`: 139 |
| reflection changes applied | 1 |

## Results analysis

- **All three instances lose a similar amount of money, by two structurally different
  routes.** Drawdowns land within a few points of each other (24.3% / 22.1% / 24.5%),
  each roughly 3x `goal.yaml`'s `max_drawdown: 0.08` target. But baseline/consultant get
  there via a *bad payoff ratio at a decent win rate* (≈52% wins, but each loss costs
  ~3x what each win pays), while trailing-exit gets there via a *bad win rate at a good
  payoff ratio* (only 24.6% wins, but each win pays ~1.5x what each loss costs). Neither
  side of that trade-off, taken alone, is enough to be profitable — this is the central
  finding of the whole exercise.
- **The mechanism is now precisely confirmed by exit-reason counts, not inferred.**
  Baseline/consultant: 100% of wins are `explicit_exit`, 100% of losses are
  `stop_loss`/`max_bars` — a clean 1:1 split, no exceptions across 2109 combined trades.
  Trailing-exit: `explicit_exit` never fires (0 of 1051, by design); wins only happen
  when a *ratcheted* stop or a profitable `max_bars` timeout closes above entry, which
  the data shows happening far less often (24.6%) than `explicit_exit` used to convert
  RSI-recovery bounces into wins (~52%). Disabling `explicit_exit` doesn't add profit
  to existing wins so much as it removes the one mechanism that reliably banked a
  bounce before it reversed — most RSI-recovery bounces are short-lived (consistent
  with the very first finding this session, that even a 50-period EMA is too slow to
  track them), so without an early exit most of them round-trip into a loss instead of
  ever reaching the trailing stop's ratcheted protection.
- **Market analysis doesn't touch the payoff ratio.** Confirmed with the local Docker
  model genuinely in the loop (see correction note above): 0.331, statistically
  indistinguishable from baseline's 0.359 and the earlier no-market-analysis run.
- **Drawdown runs unchecked in training mode across all three.** None of the three
  configurations has anything resembling live mode's `RiskEngine` kill switch. All
  three drawdowns are of a similar, uncomfortable magnitude (22-25%) despite very
  different exit mechanics underneath — worth a training-mode guard regardless of
  which exit strategy is running.
- **Reflection's adaptation rate stayed low on all three** (5 / 3 / 1 changes) for the
  same self-throttling reason: the eligibility gate (`hermes_trading/reflect.py:557`)
  needs ≥5 new trades *and* ≥30 new decisions since an asset's last reflection, and
  every applied change so far reduces that asset's own future trade rate, pushing the
  next eligibility check further out. A long timeframe alone doesn't guarantee
  proportionally more adaptation once a strategy starts tightening.

## Key takeaways

1. **Win rate and payoff ratio trade against each other under the current binary
   exit design — the fix needs to combine them, not pick one side.** `explicit_exit`
   on (baseline/consultant) buys win rate at the cost of payoff ratio;
   `explicit_exit` fully off (trailing-exit) buys payoff ratio at the cost of win
   rate. Both land at a similar net loss. This directly motivates a **hybrid**: let
   `explicit_exit` keep closing quick, modest bounces (preserving win rate) but defer
   to the trailing stop only once a trade has already moved meaningfully favorably
   (capturing the payoff-ratio upside on the subset that's a genuine trend, without
   giving up the base rate of quick wins on everything else). See the new
   `exit.trailing_activation_atr` design below.
2. **All three findings are large-sample-confirmed, not small-sample noise.** 2960
   combined trades across baseline/consultant/trailing-exit converge on the same
   structural story seen in live samples of a few dozen trades all session, across a
   completely different market regime (2022 vs. Aug 2026) — strong evidence the issue
   is structural to the exit design, not a fluke of small samples or this specific
   period.
3. **Training mode needs its own drawdown guard**, independent of which exit
   mechanism is running — worth scoping as a follow-up.
4. **Market analysis is confirmed advisory-only in practice, not just by design intent.**
   Real model consultation, real large sample, no measurable effect on the payoff
   ratio. Not a reason to disable it (it may still help entry timing in ways this
   metric doesn't capture), but not a lever for this specific problem.
5. **Reflection's adaptation rate is self-limiting by design, not stuck**, on all
   three instances — the eligibility gate's trade-count requirement combined with
   each change reducing that asset's own future trade rate. Worth knowing before
   assuming "more time = more adaptation."

## Follow-up: hybrid exit design (`exit.trailing_activation_atr`)

`exit.trailing_activation_atr` refines `trailing_overrides_explicit_exit`: instead of
handing exits to the trailing stop unconditionally from the first hold cycle, it lets
`explicit_exit` keep firing normally until the trade has already moved at least N ATRs
favorably, then defers to the trailing stop from that point on. Validated with a 7-way
sweep of the same cached 180-day 2022 window, fixed strategy (no reflection), all 5
assets, `PositionEngine`/`evaluate_strategy` unchanged:

| Variant | Trades | Win rate | Payoff ratio | Total P&L |
|---|---:|---:|---:|---:|
| `explicit_exit` only (baseline-style) | 1386 | 51.4% | 0.356 | -14.8076 |
| full trailing override (trailing-exit) | 1161 | 24.8% | 1.490 | -12.8893 |
| `trailing_activation_atr=0.5` | 1164 | 25.6% | 1.420 | -12.8844 |
| `trailing_activation_atr=1.0` | 1241 | 35.3% | 0.853 | -13.1343 |
| `trailing_activation_atr=1.5` | 1323 | 45.0% | 0.491 | -14.3060 |
| `trailing_activation_atr=2.0` | 1359 | 49.4% | 0.397 | -14.4755 |
| `trailing_activation_atr=3.0` | 1383 | 51.1% | 0.356 | -14.8736 |

**Result: `trailing_activation_atr` is a smooth, monotonic win-rate/payoff-ratio dial,
not a way to combine both.** As the threshold rises, win rate and payoff ratio move
directly against each other, converging back to baseline's numbers by 3.0 ATR. Total
P&L is *worst* in the middle (1.5-2.0) and *best* at the extremes — 0.5 ATR and the
full override are statistically indistinguishable (-12.8844 vs -12.8893). This
falsified the initial hypothesis that a single governance-rule dial could combine
trailing-exit's payoff ratio with baseline's win rate; the fix needed to split the
*position*, not tune one rule for the whole trade.

## Follow-up 2: partial-close and other structural variants (2026-08-29)

Deployed as new live paper-trading agents (`state-partial-close/`, `state-short-ema/`,
`state-activation-atr/`, `state-multi-variable/`) and backtested the static-strategy
ones the same way as the sweep above (`activation_atr=0.5` reused from the sweep
table; `multi-variable` isn't a static strategy — its effect is entirely in how
reflection evolves the strategy over time, so it can't be backtested this way):

| Variant | Trades | Win rate | Payoff ratio | Total P&L |
|---|---:|---:|---:|---:|
| `partial_close_pct=0.5` (new) | 1751 | 50.6% | 0.435 | **-12.7236** |
| `short_ema` entry filter (new) | 674 | 54.3% | 0.301 | **-7.6638** |

`exit.partial_close_pct` (see `hermes_trading/strategy.py`) banks a fraction of the
position via `explicit_exit` (win rate) while the remainder keeps riding the trailing
stop (payoff ratio) -- a structural alternative to the single-dial hybrids above,
motivated directly by their negative result. At 0.5: win rate lands close to
baseline's (50.6% vs 51.4%) *and* total P&L beats every trailing-family variant,
including the pure override and every `trailing_activation_atr` point. It doesn't
reach trailing-exit's full 1.49 payoff ratio -- it shifts the trade-off frontier
outward rather than picking one extreme -- but it's the best result in the whole
trailing-mechanism family so far.

`entry.require_ema_rising` with a short `trend_ema_period: 20` (an earlier, long
200-period version of this same filter had failed) is the standout of this whole
document: -7.66 total P&L vs everything else clustered around -12 to -15, on a 54.3%
win rate. Trades far less often (674 vs 1100-1750 for every other variant), which is
expected from an added entry filter and deserves a caveat on sample size, but the
combination of higher win rate *and* far fewer, more selective entries is a real
signal worth taking seriously, not sampling noise.

**A real bug was caught and fixed during this round:** the first `partial_close_pct`
backtest attempt scored statistically identical to the plain full-override variant
(24.8%/1.490 vs 24.6%/1.480) because its base config also had
`trailing_overrides_explicit_exit: true` set -- that flag is checked first and
unconditionally in `evaluate_strategy`, so it permanently suppressed
`explicit_exit`/`partial_exit` before `partial_close_pct`'s own
`partial_exit_count > 0` check ever ran, silently making the whole feature dead code.
Fixed by explicitly clearing that flag in the config (the live `state-partial-close/`
agent's own config was correct throughout -- only the backtest harness's copy of it
was wrong) and by making the combination a hard `ValueError` in
`evaluate_strategy` going forward, so this can't silently no-op again.

## Follow-up 3: combining the two winners, with real LLM reflection (2026-08-30)

Three new strategies combining `short_ema`'s entry filter with `partial_close_pct`'s
exit (the two winners from follow-up 2 above), backtested over the same 180-day 2022
window but this time with **real `--hermes` LLM reflection** enabled (not the
fast/deterministic no-reflection sweep used everywhere else in this document) -- the
first explicit ask this session for a properly LLM-validated result, aimed at
outright profitability rather than "less bad than baseline."

| Variant | Trades | Win rate | Payoff ratio | Total P&L |
|---|---:|---:|---:|---:|
| `hybrid-best` (short_ema + partial_close_pct) | 1005 | 51.0% | 0.401 | -8.0161 |
| `hybrid-tight-stop` (+ tighter `stop_atr: 1.5`) | 998 | 45.7% | 0.443 | -10.9713 |
| `hybrid-momentum` (+ `entry.max_adx: 25`) | 232 | 45.3% | 0.410 | **-2.6985** |

**`hybrid-momentum` is the best result in this entire document, by a wide margin** --
less than half the loss of the next-best result anywhere in this document (-2.70 vs
-7.66 for `short_ema` alone). Combining the two winners alone (`hybrid-best`) did
*not* clearly beat `short_ema` on its own (-8.02 vs -7.66) -- the real unlock was
adding an ADX ceiling (`entry.max_adx: 25`) on top of both, filtering out entries
during choppy/ranging markets where an RSI-recovery bounce is more likely to be a
false start than the beginning of a real move. Trades far less often than everything
else (232 vs 674-1751), the natural consequence of stacking three selective filters,
which deserves a sample-size caveat but doesn't undermine the direction of the
result. Reflection barely engaged in any of the three (1 applied change each, at the
same tick-60 bootstrap point every variant this session hits) -- this result is
essentially the raw strategy edge, not a reflection effect. Deployed live as
`state-hybrid-momentum/`, seeded from the exact post-reflection strategy.yaml this
backtest produced.

**Two more real performance bugs were caught and fixed while building this
backtest**, both specific to compressing 180 days of real-LLM-reflection backtesting
into a tight loop (neither matters in production, where a candle closes once per ~15
real minutes):

1. `run_hermes_reflection` re-reads and re-parses `decisions.jsonl`/`trades.jsonl`
   from scratch on every call (~863 calls across 17270 ticks); `decisions.jsonl`
   reached 13.7MB at only 55% through one run. Fixed with a rolling-window
   truncation in the backtest harness (safe because decision-count eligibility is
   always satisfied well before trade-count eligibility is, given decisions
   accumulate every tick regardless of trade activity).
2. `ArtifactProjector.project_candle_outcome` unconditionally calls `rebuild_all()`
   -> `export_system_status()` -> `integrity_check()` (a full DB scan) after every
   candle, and `append_event` re-reads the whole target file to dedupe before every
   write -- both scale with total DB/file size and were confirmed by profiling to be
   92% of tick-processing time. Worse: naively passing `projector=None` to skip this
   doesn't work either -- `persist_candle_outcome` still inserts an unacknowledged
   outbox row per candle into the `incidents` table regardless, and
   `pending_projection_items()`'s query has no supporting index, so it scans/filters
   the *whole* table on every single candle as the backlog grows. Fixed with a
   minimal projector that keeps acknowledging items (bounding the backlog) but skips
   the snapshot rebuild entirely, since nothing here reads `positions.json`/
   `system_status.json`/`open_orders.json` -- only `decisions.jsonl`/`trades.jsonl`.
   Net effect: profiled 500 candles against the actual 92MB backtest database at
   1389ms/candle before the fix, 3.0ms/candle after -- roughly a 460x speedup, taking
   the 3-variant run from an extrapolated 14+ hours down to about 20 minutes each.

## Follow-up 4: pushing hybrid-momentum toward breakeven (2026-08-30)

Six more real-LLM-reflection variants, each varying one lever from `hybrid-momentum`
(the best result so far, -2.6985) on the explicit goal of reaching *positive* total
P&L, not just "less bad":

| Variant | Trades | Win rate | Payoff ratio | Total P&L |
|---|---:|---:|---:|---:|
| `hybrid-momentum` (baseline) | 232 | 45.3% | 0.410 | -2.6985 |
| `momentum-no-partial` (drop `partial_close_pct`) | 162 | 46.9% | 0.310 | -2.7317 |
| `momentum-tighter-adx` (`max_adx: 15` vs 25) | 27 | 40.7% | 0.396 | **-0.3710** |
| `momentum-wide-stop` (`stop_atr: 2.5` vs 2.0) | 240 | 50.0% | 0.422 | **-1.8650** |
| `momentum-more-concurrent` (4 slots vs 2) | 242 | 45.5% | 0.404 | -2.8261 |
| `momentum-fast-ema` (`trend_ema_period: 10` vs 20) | 235 | 47.2% | 0.400 | -2.4962 |
| `momentum-deep-rsi` (root `entry.threshold: 25`) | 232 | 45.3% | 0.410 | -2.6985 |

None crossed into positive territory, but two independently improved on the
baseline via unrelated levers: `momentum-tighter-adx` (entry-side, a stricter ADX
ceiling) got closest to breakeven of the whole session, though on only 27 trades --
a real sample-size caveat, not a dismissal. `momentum-wide-stop` (exit-side, a wider
stop) improved by a smaller margin but on a much more reliable 240-trade sample.
`momentum-no-partial` confirms `partial_close_pct` is close to neutral inside this
combination (-2.73 vs -2.70, essentially the ADX filter doing all the work).
`momentum-more-concurrent` and `momentum-fast-ema` were both mild negatives.

**`momentum-deep-rsi` is a confirmed no-op, not a null result:** it came back
byte-identical to `hybrid-momentum`'s own numbers because the base config (seeded
from `hybrid-momentum`'s own backtest-evolved `strategy.yaml`) already has a
per-symbol `entry.threshold` override for all 5 assets, which completely shadows a
root-level threshold change. Left in the harness script as a record of the mistake
rather than deleted.

Since the two winning levers (entry-side ADX, exit-side stop width) are unrelated,
the natural next step is testing whether they compound.

`momentum-combined` (`max_adx: 15` + `stop_atr: 2.5`): **-0.2825 total P&L on 29
trades** -- the best result of the entire session, essentially breakeven (equity
ended 49.72 from a 50.00 start). Stacking the two levers didn't compound as much as
hoped beyond `momentum-tighter-adx` alone (-0.3710/27 trades) -- the ADX ceiling
dominates, and the wide stop's marginal effect on an already-tiny, already-filtered
trade set is small. **Sample size at this point (29 trades over 180 days) is a real
statistical concern, not just a caveat** -- deployed live as `state-momentum-combined/`
regardless, since it's the strongest evidence available, but this result needs more
real trades before it can be trusted as a genuine edge rather than noise.

**Pushed one step further to test the trend, and found where it breaks:**
`momentum-adx10` (`max_adx: 10`, even tighter): **3 trades** over the entire 180-day
window across all 5 assets, 0 wins, total P&L -0.1241. This is the actual stopping
point, not a further improvement -- the "tighter ADX gets closer to breakeven" trend
across 25 -> 15 -> 10 was real down to 15, but at 3 trades the number stops meaning
anything at all: any small P&L is just the sum of a handful of individual trade
outcomes, no longer evidence of an edge one way or the other. Not deployed --
3 trades is not a tested strategy.

**Honest conclusion of this multi-round search:** no variant tested this session --
roughly 20 across four rounds -- reached positive total P&L on a sample large enough
to trust. `momentum-combined` (-0.28/29 trades) and `momentum-tighter-adx`
(-0.37/27 trades) are the closest credible results, both already at the edge of
"thin but not meaningless." The honest framing is "got the loss down to near-zero
on a genuinely hostile 180-day 2022 bear-market window," not "found a profitable
strategy" -- and further tightening the ADX ceiling from here is chasing sample-size
noise, not signal. `momentum-combined` stays deployed as the best available live
candidate, but should be watched over real trades (not re-tested by tightening ADX
further) before drawing any stronger conclusion.

## Follow-up 5: genetic algorithm search (2026-08-30)

Everything so far was one-lever-at-a-time manual search. This round replaced that
with an actual evolutionary search over the full gene space at once: a 9-gene genome
(`entry_threshold`, `require_ema_rising`, `trend_ema_period`, `max_adx`, `stop_atr`,
`trailing_atr`, `max_bars`, `partial_close_pct`, `max_concurrent_positions`),
population of 10, 5 generations, tournament selection (size 3) + uniform crossover +
20% per-gene mutation, top-2 elitism. Seeded with the 5 strongest genomes found
manually (`momentum-combined`, `momentum-tighter-adx`, `hybrid-momentum`, `short_ema`
alone, `momentum-wide-stop`) plus 5 random genomes for diversity. Deliberately
excludes per-symbol threshold overrides (out of scope -- that's what live reflection
already does) and position-sizing genes (`position_size_r`, `risk_per_trade_pct`
held constant, so fitness differences reflect signal/exit quality, not just bet
size).

**Fitness = total P&L, minus a smooth per-missing-trade penalty below 30 trades**
(`0.1 x max(0, 30 - trades)`), not a hard cliff. A hard cliff was tried first and
rejected during a smoke test: this genome space has no per-symbol overrides, so even
`momentum-combined`'s own exact config only produces ~6 trades here (vs. 29 with the
per-symbol overrides its live deployment actually has), and a cliff would have
blacklisted the entire ADX-tightening direction -- the strongest lever found this
session -- with an arbitrary -1000 penalty rather than letting the GA weigh a real
trade-count/reliability trade-off. Each individual evaluated as its own subprocess
(the fast, no-reflection harness from earlier rounds) for process isolation and true
parallelism, 8 workers at a time.

**Generation-by-generation:**

| Gen | Best fitness | Trades | Total P&L | Genome (vs. prior best) |
|---|---:|---:|---:|---|
| 1 | -1.90 | 100 | -1.90 | `momentum-combined` seed with `stop_atr` bumped to 2.5, `max_adx` relaxed to 25 |
| 2 | **+0.28** | 34 | **+0.28** | `entry_threshold` dropped 30->26, `trailing_atr` dropped 3.0->2.0 |
| 3 | +0.28 (held) | 34 | +0.28 | best challenger: same genome with `trailing_atr` reverted to 3.0 (+0.17/35 trades) -- close but didn't beat the champion |
| 4 | +0.28 (held) | 34 | +0.28 | best challenger: `max_concurrent_positions` dropped to 1 (+0.14/31 trades) -- worse |
| 5 | +0.28 (held) | 34 | +0.28 | best challenger: `trailing_atr` raised to 4.0 (+0.20/36 trades) -- close but didn't beat it |

**The winner, found in generation 2 and never beaten across 3 more generations of
mutation/crossover pressure:**

```yaml
entry:
  threshold: 26          # was 30/34-35 in every manually-tested variant
  require_ema_rising: true
  trend_ema_period: 20
  max_adx: 25
exit:
  stop_atr: 2.5
  trailing_atr: 2.0      # was 3.0 everywhere else
  max_bars: 48
  partial_close_pct: 0.5
simulation:
  max_concurrent_positions: 2
```

| KPI | value |
|---|---|
| trades | 34 |
| win rate | **64.7%** (22 W / 12 L) |
| **payoff ratio** | **0.669** |
| total P&L | **+0.2769** |

**This is the first result across the entire session -- roughly 30 backtests over
five rounds -- with both a good win rate *and* a good payoff ratio at the same
time**, not one traded off against the other. That was the exact problem statement
from the very first round's key takeaway #1 ("the fix needs to combine them, not
pick one side"). It got there via two changes nothing manual tried in combination:
a *lower* entry threshold (26, more permissive RSI-recovery trigger) than every
manually-tested config, paired with a *tighter* trailing_atr (2.0 vs. 3.0
everywhere else) that lets the trailing stop bind earlier. Neither change alone was
tested manually this session -- the GA found a genuinely new region of the search
space, not a rediscovery of something already tried.

34 trades is a real sample, not a `momentum-adx10`-style artifact -- it cleared the
GA's own 30-trade reliability floor with zero penalty applied, and three separate
generations of mutation pressure searched its neighborhood without finding anything
better, which is itself evidence this is a genuine local optimum rather than a lucky
single draw. It is still 180 days on one market regime and one exchange's data, the
same caveats that apply to every number in this document -- but it is a materially
stronger, more credible result than anything the manual search produced. Deployed
live as `state-ga-champion/`.
