# EUR50–200 wallet and LLM-steered evaluation

**Historical research note, preserved for provenance.** The October9 sizing sample and proposed volume-reversal steering menu below are not the current frozen paper beta. The current primary wallet is€200, with all eight adaptive/control scenarios retained. See the [current menu and combined sizing review](PAPER_STRATEGY_ECONOMICS_REVIEW.md), [dated EUR market feasibility](PAPER_PUBLIC_FEASIBILITY.md) and [cost envelope](PAPER_WALLET_COST_ENVELOPE.md). Neither this old table nor its proposed EUR100 primary selection authorizes current trading or establishes a profitable minimum.

October9,2026 scope update requested by the user: assess minimum starting wallets between EUR50 and EUR200 and include an LLM steering the strategy every few trades. No money is committed and no model API calls are authorized by this document.

## Preliminary order feasibility

The saved diagnostic covers13 crypto contracts from the October8 liquidity shortlist, including BNB but excluding tokenized equities, gold and oil. It is not an exhaustive market search. It uses16 contiguous completed daily candles per asset to obtain14 true ranges, a2ATR stop, unchanged0.25% risk per leg and5% per-symbol exposure cap. The reserve includes stressed fees/slippage plus the earlier observed spread. A1% notional buffer is applied. EUR conversion uses captured Kraken EUR/USD bid1.12257 with a separate illustrative1% conversion haircut; the haircut is an assumption, not a quoted fee. Mixed snapshot times and closing-price references mean these are preliminary thresholds, not executable order authorizations.

| Starting wallet | Sampled contracts meeting per-leg minimum sizing |
|---|---:|
| EUR50 | 8 of13 |
| EUR100 | 12 of13 |
| EUR150 | 12 of13 |
| EUR200 | 13 of13 |

Illustrative individual thresholds: SOL aboutEUR39, XRP53, ETH65, LINK66, SUI78 and BNB177. Other sampled contracts have smaller minimum requirements. These thresholds do not imply a viable diversified strategy at those amounts. Required pairs must both fit at actual entry, after rounding, fees, aggregate exposure and margin checks. Higher volatility or changed minimums can raise the required balance. The snapshot suggests EUR100 as a useful research scenario with more room than EUR50; it does not establish a profitable minimum or recommend a deposit. EUR150adds sizing room but no new contract in this sample.

Artifacts: `research/runs/cross-sectional-feasibility-v1/wallet-sizing-v1/` contains13raw candle responses, source manifest and sizing formulas' outputs. EUR/USD response and manifest are in its parent directory. No signal P&L, training or parameter selection was performed to generate this table.

## Include the steering policy in the test

The existing implementation's `ReflectionPolicy` defaults include5new trades,20closed trades,30new decisions and a12-hour cooldown. Therefore existing behavior must not be described as an unconditional call exactly every5trades. Its supported mutations concern RSI/stop/exit/risk variables; compatibility with the new volume-reversal strategy is unproven. No existing reflection integration was changed.

The fixed-rule October8 protocol remains a control and data specification. It cannot alone qualify the user's requested adaptive system. Before starting prospective performance observation, freeze an additional steering policy and evaluate that policy end to end. The following is a proposed bounded research design, not a completed integration:

- Use5completed paired episodes as the provisional review interval, after20completed paired episodes and at least12hours since the last review. A pair is one research decision with two legs; report both pair and individual-leg counts. This maps the user's “few trades” to the candidate explicitly, rather than silently equating pair counts with the existing engine's trade counters.
- Give the model only information actually available at review time: recorded market features, past decisions, executed model fills, costs, risk and data-quality flags. Use a fixed trailing30completed-episode summary plus account totals and current public market context captured then. Do not ask today's model to role-play historical decisions and call that an unbiased backtest; pretrained knowledge can leak future events.
- Fix model/version, prompt, tool access, sampling settings and allowed actions before launch. Initial action menu: retain current policy; pause new entries until the next daily decision; choose exposure multiplier0.5or1.0within existing caps; choose quiet-volume eligibility threshold z<0or z<-0.5. These are preregistered bounded steering choices, not a finding that these alternatives improve results. No risk-cap increases, unbounded code edits or new indicators within an evaluation run. Any broader strategy proposal becomes a separately registered future candidate.
- Changes apply only to future entries after the review completes; existing positions keep their entry rules and stops. Every prompt, response, accepted/rejected change, timestamp and strategy version must be retained. Invalid response, timeout, unpriced usage or model unavailability blocks adaptive new entries; neither replay a favorable answer nor ignore a losing intervention. Existing exits remain managed by deterministic rules.
- Run the fixed-rule control and LLM-steered account on the same chronological data and wallet scenario. Charge model/retrieval costs to the adaptive account only, while reporting common infrastructure cost assumptions for both. Compare daily net account returns, drawdowns, turnover, missed opportunities and paired daily differences. More winning trades alone is not proof of improvement.

EUR100 is the provisional primary research wallet; EUR50,150and200 are prespecified sensitivity scenarios, not four chances to choose whichever result passes. There is no real funding action. Steering reviews should use the primary account's history and share the resulting decisions across sensitivity accounts to avoid four paid model conversations and post-hoc policy selection. Each sensitivity account still enforces its own sizing and may skip unexecutable signals. Before launch, freeze statistical acceptance for both absolute net performance and incremental value over the control, including multiple-testing treatment and any revised180-day sufficiency requirements. Current protocol amendments remain incomplete; no prospective performance start date is set.

## Costs can dominate this wallet range

For a recurring combined AI/hosting cost ofEUR1/month, the account must earn2%,1%,0.67%or0.5%monthly on EUR50,100,150or200 respectively merely to cover that bill. AtEUR5/month those hurdles are10%,5%,3.33%and2.5%. These are arithmetic break-even costs, not forecasts. At10% maximum gross deployment, required returns on deployed exposure are much higher than wallet percentages.

Record actual input/output/cached token counts, verified model pricing, tool costs and hosting allocation; unresolved prices remain unknown rather than zero. Earlier606unpriced usage records make this a material existing gap. Before recommending a minimum wallet, demonstrate positive net account results at realistic full costs and sufficient executable opportunities. A free-data collector and an unpriced chat review are not evidence of a zero-cost autonomous LLM trading service. Ask before starting additional paid services or model usage.
