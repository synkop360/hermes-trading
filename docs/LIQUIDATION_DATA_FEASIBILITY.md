# Liquidation-recovery data feasibility

Checked 2026-09-27. Conditional feasibility for a free, limited historical signal study. No backtest, return calculation, training, account creation, purchase or bot change performed. Completeness and profitability remain unverified.

## Direct sample checks

Bybit public REST returned 25 hourly open-interest endpoints each for BTCUSDT and ETHUSDT on January 1, 2023; January 1, 2025; March 1, 2025; and August 1, 2026. The latter six requests explicitly passed hourly-contiguity checks. Hourly spot candles also returned 25 rows per asset for January 1, 2025. These isolated samples do not certify intervening history or real-time availability.

Tardis public liquidation samples for March 1, 2025 parsed successfully: BTC 2,092 rows / 24,661 compressed bytes; ETH 1,283 rows / 16,482 bytes. Rows include event/receiver timestamps, side, price and quantity. These are messages, not independent crash events or potential trades.

CryptoHFTData's public BTCUSDT September 2, 2026 12:00 UTC liquidation sample downloaded without credentials: 4,944 bytes, valid Parquet boundary markers. Contents were not decoded because no Parquet reader was installed. No dependencies were added. Samples were inspected in memory; no bulk dataset or full coverage manifest was acquired.

## Data sources and correctness constraints

[Bybit open interest](https://bybit-exchange.github.io/docs/v5/market/open-interest) supports historical queries; linear BTCUSDT quantities are in BTC. Use consistent coin/contract units, not dollar values that fall mechanically with price. Delivery can be delayed during volatility. Historical timestamps alone do not establish when the bot could have known a value.

[Bybit liquidations](https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation) are batched every 500 ms. Raw Buy means a liquidated long position. Price is the bankruptcy price, not an executable spot price. The [February 2025 changelog](https://bybit-exchange.github.io/docs/changelog/v5#2025-02-20) states that the replaced feed emitted only one liquidation per second. Do not merge old and new liquidation totals as consistent history.

[Tardis](https://docs.tardis.dev/historical-data-details/bybit) records the replacement feed from February 25, 2025. Free first-of-month samples cannot supply continuous history. Its [CSV schema](https://docs.tardis.dev/downloadable-csv-files/data-types#liquidations) uses sell for liquidated longs (unlike raw Bybit naming), with UTC microsecond timestamps.

[CryptoHFTData](https://www.cryptohftdata.com/datasets/bybit-liquidation-data) advertises free history from June 28, 2025. Individual files are public; its SDK uses an account key. Side mapping, quantity units and placeholder columns require verification against source data or another provider. Bankruptcy prices must never be used as fill prices.

Its [gap register](https://www.cryptohftdata.com/docs/data-gaps-and-corrections) lists missing Bybit hours November 26, 2025 10:00 through December 26, 2025 19:00 UTC and April 13, 2026 15:00 through April 14, 2026 21:00 UTC (inclusive partitions). Further collection limitations and pending corrections exist. Corrections can overwrite the same filenames: hash files and retain retrieval dates. Missing files do not establish zero liquidations.

[Kraken candle archives](https://support.kraken.com/in/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data) are documented through June 30, 2026, with minute/hour intervals. These were not downloaded. Kraken prices are needed for execution evidence; Bybit prices are only a preliminary proxy. Hourly candles cannot resolve stop/target ordering or stress spreads.

## Costs and recommendation

Public samples required no purchase. Free bulk access, completeness and future terms remain unverified; hosting/storage costs are unknown.

[Tardis pricing](https://tardis.dev/#pricing) displays Solo Perpetuals at USD 700/month. [Billing terms](https://docs.tardis.dev/faq/billing-and-subscriptions) provide four months of prior history with monthly billing, twelve with quarterly billing, and four years with yearly billing for Solo. A monthly payment does not unlock the full proposed history; one-off exports are not offered under these rules. No subscription or quote was requested. Do not buy this solely for the small-balance experiment while a free route remains plausible.

## Proposed next phase (not approved or started)

Audit the free BTC/ETH archive first, compare overlapping windows with independent samples, verify timing/side/units and build a gap mask. Exclude incomplete event windows and contaminated rolling baselines. Do not silently replace unavailable liquidations with zero or assume an open-interest decline proves forced selling.

Only if data quality passes, freeze the original liquidation-confirmed recovery study: explicitly define unusually large liquidations, confirmation expiry, overlapping-event handling and information-availability delay before evaluating returns. Compare against similarly sized sell-offs without liquidation confirmation; retain a later chronological evaluation period. Primary outcome: net 72-hour return, with downside and independent-event counts. Sparse coverage may make the conclusion inconclusive.

A full execution/portfolio backtest, strategy implementation or continuous collector requires separate approval. No thresholds have been optimized and no strategy returns estimated in this phase.
