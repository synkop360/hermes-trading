# Carry research — feasibility checkpoint

2026-09-28. **No carry strategy is validated or ready to trade.** This investigates returns from financing demand instead of directional price prediction. All requests are public and read-only.

## Kraken perpetual funding

Both BTC and ETH public histories contain 8,846 hourly records from September 24, 2025 to September 28, 2026. There are seven gaps totaling eight missing hours per asset. No duplicate timestamps were found. The official bulk archive returned HTTP 403 for both HEAD and GET; no access workaround was attempted. Missing hours remain unknown.

A descriptive protocol was written before aggregating funding. Seven complete calendar months are available for each asset; September endpoints and four months containing gaps are excluded from complete-month comparisons.

| Complete month | BTC funding/reference value | ETH funding/reference value |
|---|---:|---:|
| October 2025 | +0.554% | +0.234% |
| January 2026 | +0.674% | +0.469% |
| March 2026 | -0.090% | -0.017% |
| April 2026 | -0.306% | -0.137% |
| June 2026 | +0.015% | +0.162% |
| July 2026 | +0.506% | +0.504% |
| August 2026 | +0.534% | +0.749% |

These are **not portfolio returns**: each numerator is the signed sum of hourly USD-per-coin funding (`fundingRate`) for a short position, divided by the first nonzero implied index price of that month. Kraken calls this field the absolute rate, meaning a currency amount rather than a percentage; negative payments are preserved. That denominator is not an executable fill. No spot/futures price gains, fees, funding-boundary execution, collateral or operating costs are modeled. Negative values mean shorts paid funding. Relative-rate sums are retained separately and are not misrepresented as fixed-quantity returns.

The currently published entry-tier taker rates imply approximately 1.7% of one-leg notional for two spot and two futures legs at unchanged prices, before slippage. This is an illustrative cost hurdle, not exact net accounting or confirmation of the user's account tier. The observations do not support treating funding carry as easy short-horizon profit. They also do not reject every possible long-horizon or lower-cost carry strategy.

## Kraken fixed-expiry snapshot

Nearly concurrent public spot and futures requests were captured on September 28. For BTC, futures bid versus spot ask showed approximately +0.05% to October 2, -0.26% to October 30, +1.03% to December 25, and +2.48% to March 26, 2027. ETH showed +0.04%, -0.26%, -0.31% and +2.03%, respectively. All maturities within three months were below the illustrative 1.7% fee hurdle. Six-month premiums leave a thin margin before other costs and require longer capital commitment.

One snapshot is not a trading opportunity validation: quotes can change, available sizes differ, and neither leg was executed. Fixed contracts incur a settlement taker fee. Their settlement uses a thirty-minute index average; selling spot at one arbitrary final tick does not guarantee the same price. Margin calls remain possible even with an equal spot holding elsewhere. No risk cap was raised and no real or paper position was opened.

## Next research step

Obtain longer public funding, spot and mark-price histories from a second venue, Bybit, to separate weak carry economics from Kraken's high spot-cost hurdle and limited available history. A probe returned 200 historical funding observations successfully. This is a venue-specific research comparison, not a recommendation to move funds or an assumption that the user can access its derivatives. Published global fees may differ by region; operational eligibility and actual fees must be verified before any implementation for an account.

Before economic tests: fix a non-optimized candidate, chronological partitions, costs and collateral failure conditions. Preserve all attempts and distinguish descriptive checks from independent validation. A passing result would still require forward paper evidence. Do not claim a route to doubling capital in three months from these observations.

## Evidence

Artifacts: `research/runs/funding-carry-feasibility/`: source manifest/hashes, raw funding responses, protocol, monthly summary, instruments, raw quotes, and fixed-expiry snapshot with request timestamps and hashes.

- [Kraken funding history API](https://docs.kraken.com/api-reference/historical-funding-rates/historical-funding-rates)
- [Kraken funding archive](https://support.kraken.com/articles/export-historical-funding-rates)
- [Kraken EEA contract specifications](https://support.kraken.com/articles/perpetual-contract-specifications-for-clients-in-the-eea)
- [Kraken fee schedule](https://www.kraken.com/features/fee-schedule)
- [Bybit funding history API](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)
- [Bybit published fee schedule](https://www.bybit.global/en/help-center/article/Trading-Fee-Structure)
