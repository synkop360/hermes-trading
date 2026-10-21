# Monthly relative-strength rotation — results

Completed 2026-09-28. Decision: **reject this candidate; do not start paper or live trading with it.**

The fixed rule selects BTC, ETH or cash once a month using the preceding 90-day return. It keeps unchanged holdings. Execution uses the next monthly open, with 0.8% commission and 0.1% slippage per leg. Stress slippage is 0.3%. Parameters were frozen before this run and were not optimized.

| Period | Net return | Stress net return | Daily-close maximum drawdown | Completed holdings |
|---|---:|---:|---:|---:|
| 2019–2022 development | +752.12% | +715.43% | 72.38% | 11 |
| 2023–2024 evaluation | +126.61% | +120.35% | 38.51% | 6 |
| Jan 2025–Aug 2026 final retrospective check | -17.71% | -19.02% | 51.62% | 4 |

Both evaluation periods breached the predefined 30% drawdown limit. The recent period also lost money and had only nine active months, below the twelve-month evidence minimum. Its completed-holding win rate was 50%; that did not produce a positive return. The 2026 figure alone is -21.85% through August, not a full-year return.

For context, identically costed BTC buy-and-hold returned +455.60% in 2023–2024 and -17.52% in the final period. Initial 50/50 BTC/ETH buy-and-hold returned +314.84% and -22.46%, respectively. The rotation did not provide a convincing improvement.

These are normalized research-sleeve results with full allocation, not returns at the live bot's exposure caps. Operating costs, order minimums and account-specific fees are excluded. The price history had previously been inspected in other project research; the final period is not pristine unseen validation. Daily-close drawdowns can understate intraday losses.

Reproduction: `scripts/run_monthly_rotation.py`; artifacts in `research/runs/monthly-rotation-v1/`, including frozen source, protocol, source/data hashes, daily equity and trade ledgers. Five focused tests passed, alongside eleven related tests; the subsequent full suite passed all 565 tests. An independent code review found no material correctness issue. No live configuration changed.

Next distinct hypothesis: spot plus an equal short perpetual position to collect funding. Data integrity, funding units, costs, basis risk and collateral feasibility must be checked before any claim of profitability.
