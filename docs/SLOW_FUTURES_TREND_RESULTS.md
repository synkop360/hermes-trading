# Slow futures trend preliminary screen

September 29, 2026. Decision: **reject this fixed candidate; do not advance to paper trading**.

The registered rule uses the sign of each asset's own preceding twelve-calendar-month return, then holds long or short for one month. SOL and XRP were fixed before outcomes from the earlier small-contract feasibility audit. The signal uses completed hourly closes, enters one hour after the monthly decision, and pays exit/re-entry costs each month. This is a related momentum trial, not a new independent family. The traditional-market research motivating the horizon does not validate this crypto adaptation: [Moskowitz, Ooi and Pedersen](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum).

| Period | Months | Mean monthly event return, after costs | Higher-slippage mean | Winning equal-weight months |
|---|---:|---:|---:|---:|
| 2023–2024 | 24 | -6.0384% | -6.5032% | 12/24 |
| Jan 2025–Aug 2026 | 20 | -0.8665% | -1.2621% | 11/20 |

These are arithmetic means of equal-weight SOL/XRP hypothetical returns per entry notional, **not compounded portfolio returns or returns on the USD50 account**. Both periods fail the predeclared positive base and stress mean gates. A 55% winning-month fraction in the recent period still loses money: win frequency does not capture loss size.

Individual base means were SOL -0.4201% and XRP -11.6566% in 2023–2024; SOL -2.5959% and XRP +0.8629% recently. Selecting recent-positive XRP now would be outcome-driven selection and is not authorized by this protocol.

Costs are 0.055% commission per leg, 0.1% adverse slippage per leg, and signed discrete funding at hourly mark-open proxies. Stress uses 0.3% slippage. This uses same-venue Bybit research data; it does not establish French-account access, Kraken profitability, or actual fee eligibility.

The worst individual monthly event was -265.1532% of entry notional in the earlier block and -41.3071% recently. The first is an unbounded hypothetical short-position loss, **not an executable fully funded monthly path or an observed account loss**. This screen has no liquidation, margin, stops, position sizing or operating bills. Those omissions prevent any claim of executable performance; the negative screen gives no reason to build those later stages for this candidate. Adding a stop or scaling now would constitute a separate registered trial, not a correction that makes this result pass.

Artifacts: `research/runs/slow-futures-trend-v1/result-v1/` preserves the protocol and source snapshots, input hashes, 88 asset-month events and aggregated results. The supplemental Dec31,2021 price tails establish the first signal's lookback; Sep1,2026 00:00 funding is explicitly included before the final 01:00 exit. Existing normalized inputs match the prior study's fingerprints. Outputs refuse overwrite. Previously inspected 2023–2026 observations are retrospective checks, not pristine unseen validation.

No parameter sweep, asset substitution, Kraken portability stage or live changes follow this rejection. Independent review status is recorded in the research ledger.
