# Paper beta economic hurdles

Recorded October 18, 2026. This is conditional arithmetic on the saved public observation, not a backtest, entry-signal evaluation or return forecast. No strategy is qualified as profitable.

The next deliverable is a bounded paper beta. It should begin measuring results before financial qualification. These calculations identify what the beta needs to test; they do not add a requirement to prove profitability before paper activation.

## One saved sizing point

At the October18 14:51:11 UTC observation, only EUR200 ETH passed the unchanged native sizing guards. The quantity was 0.00236054 ETH, with about EUR5 entry notional. The effective ETH target is 11 ATR and its initial stop is 2.5 ATR. The configured assumptions are 0.8% taker fees per side and 0.1% slippage per side; they are candidate assumptions, not verified private account fees.

Using the first quote tick beyond the frozen target, or below the initial stop, gives:

| Hypothetical exit | Baseline trade P&L | Doubled trading-cost P&L |
| --- | --- | --- |
| Bid reaches the target boundary | +EUR0.1861 | +EUR0.0937 |
| Bid reaches the initial stop without a gap | -EUR0.1465 | -EUR0.2358 |

The retained hosting estimate is EUR0.23/day, or EUR6.90 per 30 days. Other unresolved expenses remain unknown, rather than zero.

| Conditional hurdle over 30 days | Target winners needed, with no losses |
| --- | --- |
| Baseline trading costs and EUR6.90 hosting | 38 |
| Doubled trading costs, hosting still EUR6.90 | 74 |
| Full protocol stress: doubled trading costs and EUR13.80 hosting | 148 |

If exactly 60 entries closed with those identical baseline target/stop payoffs, 48 target winners and 12 stop losses would be needed to cover hosting alone. This is a break-even illustration, not an expected win rate or a statement that 60 such opportunities exist.

Later quantities, volatility and prices can differ. RSI, the 24-bar limit, risk or operational exits may close earlier; quote gaps may produce greater losses. The aggregate examples do not establish survival of the 3% drawdown rule or sequential cash admission. LLM changes affect future rules and require their own prospective comparison. A higher win rate alone does not establish positive net wallet returns.

EUR200 sizing eligibility therefore does not establish a profitable minimum wallet. The EUR200 primary protocol has since been approved for disabled local implementation, which is in progress. This calculation itself changed no selection, fee, cap or strategy; the original candidate and historical evidence remain unchanged.

## What will establish improvement

From actual paper inception, record net liquidation equity, all costs, missed opportunities, drawdown and operational failures for the adaptive and fixed arms on shared inputs. The first planned comparison is day30; day90 measures the first three-month objective. Neither is a durable profitability qualification. The existing at-least-180-day protocol, sufficient completed lineages, doubled-cost results and statistical/risk requirements remain in force.

Evidence: [saved payoff calculation](../research/runs/beta-payoff-screen-2026-10-18/verification.json), [calculation source](../research/runs/production-archive-integration-v1/screen_fixed_point_payoffs.py), [native sizing observation](../research/runs/beta-feasibility-2026-10-18/native-admission/verification.json). The calculation makes no network or model calls and does not replay market returns. See [paper beta readiness](PAPER_BETA_READINESS.md) for the remaining launch gates.
