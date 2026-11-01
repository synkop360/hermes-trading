# Frozen strategy economics and LLM steering review

October22,2026. Analysis of approved `6c9d520`, saved October21 public quotes and copied synthetic inputs. No deployment work, market backtest, training, model inference, new market collection, strategy entry or runtime/configuration change. This is additional analysis, not a new launch gate or profitability qualification.

**Finding:** the model has four potentially effective adjustment variables in this baseline, plus one percentage-stop variable that can pass proposal validation while leaving the actual ATR stop unchanged. Combining the payoff screen with minimum-size risk also gives stricter necessary wallet bounds than the previous cost-only screen. Neither finding establishes a profitable strategy or a deposit recommendation.

## What the model can actually change

One review may choose `no_change` or change one variable, globally or for one configured asset. A global variable need only affect one active asset after overrides; it does not necessarily affect all three. Existing positions retain their entry-version contract.

| Variable | Bounds; maximum absolute change per review | Effect in the frozen baseline |
| --- | --- | --- |
| `entry.threshold` | 15–35; 2 | Changes asset RSI recovery timing. A global change is shadowed by all three symbol overrides and rejected; a targeted change can work. |
| `stop_loss_pct` | 0.3–3; 0.2 | **Ignored by the stop policy while `exit.stop_atr` is present.** Changing the YAML field can still pass proposal validation. |
| `exit.stop_atr` | 0.5–4; 0.5 | Changes future ATR stop distance and risk-sized quantity. A narrower stop can improve size eligibility while placing the stop closer. |
| `exit.take_profit_atr` | 1–15; 7 | Changes future targets and the configured payoff screen. A global change affects XRP; BTC and ETH override the global value with11. |
| `exit.max_bars` | 1–24; 4 | Changes the holding timeout; whole bars are required. |

These limits are maximum changes, not discrete step grids. `position_size_r` is explicitly locked despite appearing in the generic bounds supplied to the prompt. Fees, order/exposure/risk caps, minimum quantities, exit RSI threshold55, indicator family and asset universe are outside this paper menu. It cannot eliminate execution friction or add capital.

Sources: [bounds](../hermes_trading/reflect.py), [proposal validation](../hermes_trading/paper_strategy_proposal.py), [review and prompt](../hermes_trading/global_reflection.py), [publication guard](../hermes_trading/paper_publication.py), [stop policy](../hermes_trading/execution_policy.py).

### Reproduced ineffective adjustment

Four pure-function checks on copies of the frozen strategy confirm:

1. `stop_loss_pct:0.6→0.8` passes proposal validation. With a mocked ATR10 and fill2000, the stop remains1975 because `stop_atr:2.5` governs it.
2. As a positive control, changing `stop_atr:2.5→2.0` moves that synthetic stop to1980.
3. A global RSI change26→28 is rejected because all three symbol overrides remain unchanged.
4. A global target change4→11 changes only XRP's effective target.

No model review or publication was attempted. Static tracing shows a normally accepted review can receive a new version and pass the publication field-change guard without testing whether the modified field is consumed by the stop policy. This is a misleading learning action, not evidence that an inert change was published in a running experiment.

Recommended local follow-up: exclude percentage-stop proposals when ATR stops govern every affected asset, at both prompt and validation boundaries. Preserve percentage-stop support where it actually controls the stop. Keep explicit `no_change`, one variable, existing risk limits and publication guards. The approved `6c9d520` release still retains the original behavior.

Subsequent independent work prepared this correction as local commit `3e785e1` on `fix/paper-effective-stop-proposals`, in a separate worktree. All203 affected regression checks pass and independent code review has no findings. The branch is not merged, pushed or deployed; current cutover approval still targets `6c9d520`. Its affected-path checks are not a new full release qualification. [Local fix, patch and verification](../research/runs/paper-effective-stop-fix-v1/progress.md).

## Combined ATR and minimum-size bounds

The frozen baseline uses stop multiplier `m=2.5`; target `t=11` for BTC/ETH and `t=4` for XRP. Fees remain0.8% taker per side and assumed slippage0.1% per side. Kraken's public Tier1 table still lists0.4% maker/0.8% taker when checked October22; the private account tier remains unknown. [Official fee schedule](https://www.kraken.com/features/fee-schedule).

Let `A` be closed-history ATR, `C` the **signal candle close**, `q` the smallest valid quantity on the saved grid, `P*=ask×1.002` the stressed fill price, and `E*` initial stressed equity. With no previous expenses or inventory, native stress admission implies:

```text
configured target:  t × A / C >= .038
minimum-size risk:  q × m × A + .036 × q × P* <= .00125 × E*

therefore:
.038 × C / t <= A <= (.00125 × E* - .036 × q × P*) / (q × m)
```

Both original and stressed stops must remain positive; the original-fill condition `A<ask×1.001/m` is slightly tighter than `A<P*/m`. All positive upper bounds in the saved table are already below it. These are necessary bounds only. A larger quantity tightens the risk constraint. Native sizing rounds down and may reject; cash, exposure, signal, history, venue, quote freshness and eventual exit checks remain separate. The risk allowance is the implementation's conservative linear reservation, not an estimate of realized loss or a guaranteed loss cap. Sources: [stress admission](../hermes_trading/paired_cost_stress.py), [reservation sizing](../hermes_trading/entry_cost_reserve.py), [payoff screen](../hermes_trading/positions.py).

The saved Ticker provides neither `C` nor `A`. Consequently this analysis does **not** establish that an actual signal passes or fails. For XRP at the saved ask1.23112, the €200 upper ATR bound is0.007236415744. An interval can exist only if the missing signal close is at most about€0.761728. This is a conditional mathematical restriction, not an observed rejection.

For an explicitly hypothetical benchmark `C=saved ask`, the combined risk floor is:

| Asset | Necessary initial equity, approximately | €150 benchmark interval | €200 benchmark interval |
| --- | ---: | --- | --- |
| BTC/EUR | €161.65 | Empty | Nonempty |
| ETH/EUR | €158.71 | Empty | Nonempty |
| XRP/EUR | €235.67 | Empty | Empty |

These rounded values are mathematical boundaries under that benchmark, **not sufficient balances or recommended deposits**. Exact values are retained in the analysis JSON. The prior €50/100 cost-only rejections still apply. A nonempty interval does not establish observed volatility, an entry, review frequency or profit. Quotes are dated October21,21:19:58UTC; they are not current entry evidence.

This narrows the earlier necessary-only result: €150/200 were not excluded by costs alone, but the target/stop relationship can eliminate that room. Raising an allowed target or narrowing a stop changes this interval. It also changes the economics of reaching targets and hitting stops; it is not a free increase in expected profit. RSI exits or the24-bar timeout can close a trade before its target; increasing the target does not remove those exits.

## How to judge the LLM's contribution

Five new qualifying closes make a review eligible; they do not establish that a parameter change improves expectancy. The coordinator can consume more than five pending identities and the supplied recent trades may span entry versions. Recovery/risk-stop closes are excluded from learning eligibility. If eligible closes never arrive, this policy cannot change settings to cure inactivity before its first review.

Evaluate each proposed adjustment against its **effective** asset settings, then separate:

- operational facts: review due/completed, valid response, one effective variable, publication and version provenance;
- economics: trading P&L after execution costs, account net equity after operating costs, drawdown and adaptive-versus-control differences on common inputs;
- learning limits: target distance is not expected return, accepted change is not improvement, and missed/skipped opportunities remain visible.

The completed September comparison already supplies a caution: full RSI exits raised development win rate from36.364% to54.545%, while return worsened from−0.193% to−0.217%. Those are historical300USDT simulations with different assumptions, not results for this EUR beta. [Preserved comparison](PROFITABILITY_COMPARISON_RESULTS.md). Optimizing winning percentage alone would repeat that mistake.

Retain the current €200 primary and fixed control. First require a meaningful, auditable model action; use `no_change` when evidence is inadequate. Any new market backtest, training, broader strategy change or risk change still needs the user's prior approval. No claim of improved profitability or a winning strategy follows from this review.

## Evidence and verification

[Full-precision interval inputs/results](../research/runs/paper-strategy-economics-audit-v1/atr-interval-analysis.json), [four native synthetic parameter checks](../research/runs/paper-strategy-economics-audit-v1/synthetic-parameter-impact-witness.json), [source hashes and verification](../research/runs/paper-strategy-economics-audit-v1/verification.json).

All364 approved source/config/build hashes remain unchanged. The first witness failed because generic YAML loading supplied a binary float to the strict stop calculator; the corrected witness uses the real engine's Decimal YAML boundary. That input failure is preserved separately and was not counted as passing. No strategy entry or performance replay was run, and the beta still has0actual paper days.
