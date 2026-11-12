# Quarterly funding carry — rejected

2026-09-28. The fixed BTC/ETH spot-plus-equal-short perpetual candidate fails its registered gates. It is not ready for paper or live trading.

## Results

| Period | Quarters | Mean quarterly result, base | Higher-slippage mean | Collateral-unsafe asset quarters |
|---|---:|---:|---:|---:|
| 2022 development | 4 | -0.098%* | -0.460%* | 1 |
| 2023–2024 evaluation | 8 | +1.249% | +0.802% | 0 |
| Jan2025–Jun2026 recent check | 6 | +0.176%* | -0.213%* | 1 |

**Asterisked terminal results are hypothetical:** they assume holding through a collateral failure. They are retained only to diagnose the candidate and must not be presented as executable portfolio returns. The correct decision is rejection, irrespective of those terminal numbers. Means are un-compounded averages across independently restarted quarters, with BTC and ETH weighted equally; they are not annual returns.

ETH breached the conservative collateral test in 2022Q3 (first breach August13,04:00 UTC) and 2025Q3 (August12,20:00 UTC). Spot holdings were deliberately unavailable to replenish the separate futures wallet. A hedge can offset price P&L while failing that wallet's margin requirement. The 10% maintenance allowance is a study stress assumption, not a reconstructed historical exchange liquidation threshold. No precise liquidation fill is inferred from candles.

The recent stress mean was negative even before operating costs. Using Kraken-like fees on the same Bybit prices produced a recent hypothetical mean of -0.499% per quarter; this is a cost sensitivity, not a Kraken backtest. The 2023–2024 base model had eight positive quarters, but that does not overcome the later failures.

## Data and accounting

All six same-venue hourly series (spot, perpetual trades, mark price; BTC and ETH) contain 40,898 observations from Jan1,2022 through Sep1,2026 01:00 UTC, with no missing timestamps. Funding contains 5,112 eight-hour observations per asset through August31. Raw-response hashes, normalized/raw funding agreement, numeric/OHLC consistency and required-period coverage were checked. Exchange records have not yet been corroborated against an independent archive; an intact grid alone does not prove every price is accurate.

Quarterly entry/exit is at 01:00 UTC to avoid funding boundaries. Spot and short quantities match, futures sales create no synthetic cash, fees apply to actual modeled entry/exit notionals, and signed funding uses settlement-hour mark opens. The latter is a price proxy; a pessimistic hourly-range funding sensitivity is also recorded. Capital includes separately modeled cash collateral equal to the spot purchase notional. Base fees are 0.1% spot and 0.055% futures per leg, with 0.1% adverse slippage; stress slippage is 0.3%. Global published fees are not the user's confirmed account fees. These are normalized research sleeves, not changes to live exposure limits.

The model includes initial liquidation-value loss, hourly-close equity, hourly-high collateral stress, exit-open collateral check, and labeled valuation stages. Counterparty failure, USDT depeg/conversion, minimum orders, operating bills and actual account eligibility are not modeled. Those omissions prevent deployment conclusions but do not rescue a rejected candidate.

## Review and reproducibility

Frozen protocol: `docs/superpowers/plans/2026-09-28-bybit-carry-study.md`. Code: `hermes_trading/funding_carry.py`, `scripts/run_funding_carry.py`. Authoritative corrected artifacts: `research/runs/bybit-carry-v1/study-v2/`, with source, protocol and input fingerprints, per-quarter equity paths, accounting summaries and rejection reasons. The runner refuses to overwrite this version.

An independent review identified initial-drawdown, exit-margin, valuation-timestamp and snapshot-overwrite issues. The first two were reproduced with failing synthetic tests and corrected; all eight focused tests then passed, followed by all 573 tests in the full suite. Timestamps now identify close versus entry/exit valuations. The first exploratory output directory was overwritten once during correction before immutable versioning was added; do not treat it as an untouched original freeze. The corrected run retains the same rules, net results and rejection. The reviewer verified all four fixes and all eleven frozen input hashes. No parameters were fitted to improve profitability.

## Research decision

Reject this unconditional quarterly candidate. Do not increase leverage, hide failed quarters or select only the stronger asset/years. Future work must use a distinct, economically justified hypothesis and account for the growing number of trials. Clarification of starting capital, account jurisdiction and permitted products has been requested to keep further candidates operationally relevant. No orders, new account connection, paid data or live configuration changes occurred.
