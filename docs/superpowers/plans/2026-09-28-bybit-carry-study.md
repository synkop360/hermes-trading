# Fixed quarterly funding-carry study

Status: registered before strategy outcomes on 2026-09-28. Public funding histories and a one-day price-availability probe have been inspected; no carry outcome has been computed. User has authorized independent research. No account access, paid services or real-money orders.

## Candidate

Unconditionally buy spot and sell an equal coin quantity of USDT perpetual futures, BTC and ETH. Enter at 01:00 UTC on January/April/July/October 1, exit at 01:00 UTC on the next quarter's first day. This avoids opening/closing at the funding timestamp. No rate filter, asset selection, compounding within quarters, stop optimization or rebalancing. Re-enter after each exit in the research model, explicitly paying both round trips. Do not optimize this deliberately simple hypothesis if it fails.

Each quarter begins with 10,000 virtual USDT, split equally into independent BTC and ETH sleeves. For each sleeve choose equal spot/short quantity such that cash pays the spot purchase, entry fees, and retains cash collateral equal to the spot purchase notional. Futures sale does NOT create spendable proceeds. Spot is held separately and cannot replenish the futures wallet during this screening simulation. This is a normalized study, not a change to live exposure limits or an approved capital allocation.

Use actual same-venue hourly spot, perpetual trade and mark candles, plus recorded funding events. Prices for entry/exit are hourly opens with adverse slippage. Funding for a short is signed rate times quantity times funding-timestamp mark open; preserve negative payments. No interest on cash. Record a pessimistic funding sensitivity using the funding hour's mark low for positive payments and high for negative payments. This is a bound using hourly data, not an assertion that such prices were the settlement marks. No future rate enters a decision; unconditional entry needs no rate forecast.

Base fees: spot 0.1% and futures 0.055% per leg, based on published global entry tier, account/region unverified. Base slippage 0.1% per leg; stress 0.3%. Additional portability sensitivity: spot 0.8%, futures 0.05% with 0.1% slippage. It remains Bybit data, not a Kraken execution backtest. Exit fees use exit notional. Operating costs, USDT conversion/depeg losses and account order minimums are excluded at screening and must be addressed before paper readiness.

Hourly equity is spot liquidation proceeds plus remaining collateral plus short P&L less futures closing fee and slippage. Include the initial fee loss in drawdown. Hourly collateral stress uses mark HIGH for the short's adverse valuation, and a conservative 10% maintenance allowance plus closing fee. Test collateral before crediting positive funding in the hour; include negative funding immediately. If collateral fails, flag the whole quarter unsafe and reject the candidate regardless of any hypothetical end return; do not invent a precise liquidation fill from candles or silently exclude that quarter. Daily-close results alone cannot certify margin safety.

## Data and gates

Acquire Jan2022-Sep1,2026 01:00 UTC inclusive. Validate response symbols/category, finite positive OHLC, OHLC consistency, unique ordered timestamps, complete hourly grid, complete eight-hour funding grid. Preserve raw response hashes and URLs. Missing data prevents a passing result; never forward-fill. Funding records previously downloaded end Aug31, sufficient because funding at Sep1 belongs only to a partial-quarter descriptive check; full-quarter primary study ends July1,2026 01:00 and requires July1,00 funding, which is present.

Development: four quarters of 2022. Evaluation: eight quarters 2023-2024. Recent retrospective check: six full quarters Jan2025-Jun2026. Exclude unfinished 2026Q3 from primary inference. These periods overlap previously explored market history; do not label them pristine unseen validation. Evaluate the preselected equal-weight combination and disclose each asset separately without picking the winner afterward.

Primary reject gates: any collateral failure in any period; any missing required observations; combined mean quarter net return <=0 in either evaluation block; either block's stress net mean <=0; hourly-close drawdown >10% in any quarter. Report quarter returns, funding, fees, basis contribution, collateral minima, distribution and win fraction. A passing screen is not deployment approval: assess quarter-clustered uncertainty (BTC/ETH kept together), comparison to cash with zero incremental trading cost, dependence, operating-cost breakeven, historical crosschecks, eligibility/minimums, and forward paper evidence. Positive aggregate returns alone are insufficient. Account for this attempt in the research ledger.

## Tasks

- [ ] Collect and validate matched prices; preserve hashes and coverage.
- [ ] Implement exact accounting with synthetic tests for hedge cancellation, funding signs, execution costs and collateral failures.
- [ ] Freeze source/data/protocol hashes, run once, review independently and report all outcomes.
- [ ] Advance only if evidence supports further validation; keep the overall research goal active otherwise.

Sources: https://bybit-exchange.github.io/docs/v5/market/kline ; https://bybit-exchange.github.io/docs/v5/market/mark-kline ; https://bybit-exchange.github.io/docs/v5/market/history-fund-rate ; https://www.bybit.global/en/help-center/article/Trading-Fee-Structure .
