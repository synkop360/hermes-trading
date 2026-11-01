# Paper wallet cost and sizing envelope

Calculated October 21, 2026, against approved commit `6c9d520`. **Arithmetic only: no market backtest, training, strategy execution, paid call or Railway access.** All eight approved configurations, 364 release pins, risk limits and prepared activation inputs remain unchanged. There is no actual experiment inception or forward profitability evidence.

**Finding:** €200 has the lowest fixed-cost burden among the approved wallets, but no profitable minimum wallet is established. Under the declared costs, an inactive €200 wallet reaches the 3% drawdown trigger before day 30. Additional capital alone cannot fix the €5 stressed order cap or configured quantity minimums. Keep the bounded paper trial as a test of these constraints, including inactivity; it is not evidence that the three-month doubling aspiration is achievable.

## Hosting and the drawdown clock

The candidate allocates estimated standalone hosting of **€0.23/day**, or **€6.90/30 days**, to each virtual scenario. This is a declared estimate, not a current measured invoice. The scenarios are alternative standalone deployments: do not add their eight allocations together as the real hosting bill. Extra model/tool costs are excluded below; missing prices remain unknown. Verified-free LLM access is still required before activation.

Assume no trades or inventory, hosting only, initial equity/peak equal to starting capital, and continuous cost accrual:

| Starting wallet | Hosting / initial capital, 30 days | Initial 3% loss budget | Time to reach that budget | Trading gain needed by day 30 to remain below 3% drawdown* |
| --- | ---: | ---: | ---: | ---: |
| €50 | 13.80% | €1.50 | 6.52 days | >€5.40 |
| €100 | 6.90% | €3.00 | 13.04 days | >€3.90 |
| €150 | 4.60% | €4.50 | 19.57 days | >€2.40 |
| €200 primary | 3.45% | €6.00 | 26.09 days | >€0.90 |

*Trading gain here is after execution fees/slippage and before hosting. These are necessary endpoint bounds under the stated initial-peak assumption, not sufficient conditions for avoiding an earlier halt. The path matters: profits must arrive before the threshold is reached; subsequent higher peaks can create a larger measured drawdown. Negative trades, open liquidation values and additional costs can trigger a halt sooner. Inactivity is not an absence of expense.

The native paired policy triggers at **drawdown ≥3%** at an observation. Either arm's breach halts both arms of that wallet; either €200 primary arm's breach halts entries globally. Halts are latched. They attempt protective closes, while costs and reporting continue. A fresh valid bid and venue/minimum evidence are required for liquidation, so this is not a guaranteed maximum loss or instant exit. A day-30 operational checkpoint can therefore include an earlier halt and no learning reviews.

`€6.90 / .03 = €230` is the **hosting-only threshold**, outside the requested €50–200 range. At exactly €230 the no-trade loss reaches 3% at day 30; staying strictly below it would require more. This is neither a profitable minimum nor a recommendation to deposit more.

## Execution friction and actual order ceilings

The frozen assumptions are taker fee **0.8% per side** and simulated slippage **0.1% per side**. The public Tier 1 Kraken Pro schedule currently lists 0.4% maker / 0.8% taker; the private account tier remains unverified. Slippage is an assumption, not a measured fill result. [Kraken fee schedule, checked October 21](https://www.kraken.com/features/fee-schedule).

For the same fully closed quantity, let `N = quantity × entry ask` and `g = exit bid / entry ask − 1`. Native trading P&L is:

```text
base:                  N × (0.991008 × g − 0.018)
same-order 2× fees/slip: N × (0.982032 × g − 0.036)
```

The executable reference move needed to break even is **1.81633%**, or **3.66587%** with doubled fees/slippage, before hosting or other operating expenses. These reference sides already incorporate the bid/ask spread; a flat market midpoint does not imply a flat entry-ask/exit-bid reference. The doubled-cost calculation holds quantity and reference prices fixed. It is a sensitivity identity, not a strategy replay or completed cost-stressed qualification; the full doubled-cost protocol also doubles operating expenses.

The actual risk budget is `.0025 × position_size_r .5 = .00125` of net equity. With enabled cost-stress admission, even the following initial order ceilings are generous:

| Initial wallet | Initial risk budget | Base entry-fill notional ceiling |
| --- | ---: | ---: |
| €50 | €0.0625 | <€1.73438 |
| €100 | €0.1250 | <€3.46876 |
| €150 | €0.1875 | ≤€4.99501 |
| €200 | €0.2500 | ≤€4.99501 |

These bounds assume ordinary/stressed equity initially equals starting capital, no prior expenses or inventory, and omit the positive stop distance when obtaining the risk ceiling. Actual ATR stops, cash/exposure, downward quantity rounding and minimums reduce admissible orders or cause a skip. Later sizing depends on actual equity and costs. The reserve checks doubled-cost feasibility; it does not double the base account's actual charges. Moving from €150 to €200 lowers the hosting percentage and adds a loss buffer, but cannot raise the configured €5 order cap.

The configured minimum quantities and stressed order cap require these necessary executable ask ceilings:

| Symbol | Configured minimum quantity | Ask must be at most, before other constraints |
| --- | ---: | ---: |
| BTC/EUR | 0.00006 | €83,166.99933 |
| ETH/EUR | 0.002 | €2,495.00998 |
| XRP/EUR | 4 | €1.247505 |

Formula: `ask ≤ €5 / (minimum_quantity × 1.002)`. Above a ceiling, adding wallet capital cannot admit that symbol with these frozen limits. **No current prices were collected**, so this does not establish which symbols can trade today. Fresh dated venue rules may be stricter. The configured payoff screen additionally requires target distance ≥2.0% ordinarily and ≥3.8% under stress admission. It measures configured distance, not observed expectancy.

## How large is the earning hurdle?

For illustration only, hold each order at or below the initial ceiling above, with no reinvestment and no losing trades. If every closed trade captured the same **+5% executable reference move**, the optimistic number needed just to cover €6.90 hosting is:

| Wallet | Conditional winning round trips needed, at least |
| --- | ---: |
| €50 | 127 |
| €100 | 64 |
| €150 | 44 |
| €200 | 44 |

These are arithmetic lower bounds under this fixed sizing assumption, not a feasible schedule or prediction. Stops/rounding make orders smaller; losing trades and other costs increase the hurdle. Later sizing could change with equity. The bot cannot manufacture this frequency from signal scarcity, concurrency limits or its holding rules. For €150/200 the generous ceiling earns only about **€0.15744** on that hypothetical +5% trade, after execution costs and before hosting.

Using **90 days** as an illustration of the three-month aspiration, hosting alone is €20.70. To double the starting wallet after hosting, trading P&L after execution costs must be at least €70.70 / €120.70 / €170.70 / €220.70 for €50 / €100 / €150 / €200 respectively, before any additional costs. These are requirements, not expected outcomes; calendar months may differ from 90 days.

## Consequences for the approved beta

1. Retain €200 primary and all comparisons. Report exact sizing/venue/cost skips and elapsed halt time; do not force entries or loosen risk limits to generate activity.
2. Verify tradability at actual inception with the native dated quotes, ATR history and venue constraints. A complete skip result is a valid operational finding, not a profitable strategy.
3. Use measured hosting/account-fee evidence when available to review the declared assumptions. Do not silently exclude costs or assume a cheaper execution path. A cost/venue/strategy redesign needs its own review before implementation.
4. LLM steering remains after five new qualifying closes, with `no_change` allowed and a fixed control. Free model access avoids one expense; it does not remove execution friction, minimums or establish an edge. No review may occur if sufficient closes never arrive.
5. Keep the day-30 checkpoint operational and the longer profitability qualification separate. No minimum profitable wallet, improved winning rate or successful doubling path is established by this analysis.

Reproduce the arithmetic with `python research/runs/paper-cost-envelope-v1/calculate.py`. [Full-precision inputs/results](../research/runs/paper-cost-envelope-v1/arithmetic.json), [independent source/formula review](../research/runs/paper-cost-envelope-v1/independent-review/arithmetic-review.md). The first calculation stopped on an overly broad equality assertion: fixed controls also contain their deliberate `research.frozen` annotation. The check now verifies trading fields and that annotation separately; no candidate input was changed.
