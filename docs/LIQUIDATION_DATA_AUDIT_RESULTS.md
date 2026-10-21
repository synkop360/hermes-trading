# Liquidation archive audit — 2026-09-27

**Data quality gate failed. No signal study was run. The strategy remains untested.**

The user approved a free data audit and a signal study only if quality passed. Three dates were selected before comparison: July 1, 2025; January 1, 2026; August 1, 2026. BTCUSDT and ETHUSDT daily Tardis samples were compared with 24 hourly CryptoHFTData files per asset/date. No returns were calculated.

The fixed gate requires 99% bidirectional multiset agreement on timestamp, exact quantity, bankruptcy price and normalized side. Multisets preserve repeated counts. Neither provider is exchange ground truth: disagreement prevents certification but does not establish which source is always correct.

| Date | Asset | Reference rows | Retrieved archive rows | Unique archive event keys | Matched rows |
|---|---|---:|---:|---:|---:|
| 2025-07-01 | BTC | 1,340 | 3,665 | 1,338 | 1,338 |
| 2025-07-01 | ETH | 1,318 | 3,194 | 1,316 | 1,316 |
| 2026-01-01 | BTC | 266 | 263 | 263 | 263 |
| 2026-01-01 | ETH | 117 | 115 | 115 | 115 |
| 2026-08-01 | BTC | 482 | 482 | 482 | 482 |
| 2026-08-01 | ETH | 538 | 538 | 538 | 538 |

July contains 4,205 excess rows relative to unique event keys across both assets. Many events appear three times. Examples have identical event fields with receipt timestamps a few milliseconds apart; the reference contains one corresponding record. Counting every row would inflate liquidation activity. A future deduplication rule needs independent validation because the matching key is not an exchange-issued unique event ID.

Initial requests encountered 83 rate limits. A sequential retry, paced at 1.1 seconds before each uncached request, resolved those limits. Final results: 114 successful objects (six reference CSVs, 108 archive files), and 36 HTTP 404 responses. Nine reference events occur in unavailable intervals. Those missing files cannot be treated as zero liquidations. Other unavailable intervals have zero reference events, which corroborates only those specific sampled hours.

January reference agreement is 98.87% for BTC and 98.29% for ETH, below the 99% gate even without duplicates. August retrieved records match exactly, and its unavailable hours contain no reference events. That more recent agreement cannot establish full historical coverage or repair earlier discrepancies.

## Timing and schema

Normalized sides agree for matching records; inverted sides produce no matches. CryptoHFTData `trade_time` corresponds to the per-liquidation timestamp in Tardis; `event_time` is the enclosing batch timestamp. Raw Bybit Buy denotes a liquidated long, whereas normalized sell denotes a liquidated long. Bankruptcy prices were used solely for matching, never as executable prices.

Files include plain Parquet and an outer Zstandard compression layer; both were decoded. No negative receipt delays were observed; the maximum was approximately 3.83 seconds. The automated gate checks nonnegative delay but does not enforce a maximum plausible delay. Independent review identified this omission. It cannot overturn the present failure; any future passing audit must define and implement that bound in advance.

## Decision and next step

The free archive is unsuitable as delivered. It requires validated treatment of repeated records, reliable coverage metadata and resolution of missing events. Deduplication alone does not fix the sampled omissions. Do not silently fill gaps with zero, drop liquidation confirmation or start a profitability study.

Resolve these issues using independently validated records or a provider correction before spending on a backtest. If historical repair is not feasible, a separately approved prospective recorder could collect future observations, but would not provide an immediate historical profitability answer. This audit does not justify a paid data purchase or reject the economic hypothesis.

## Verification

Independent read-only review confirmed the raw-data failure. All 114 successful files passed SHA256 reconciliation. The fixed protocol, scripts, original responses, initial/retry manifests, duplicate examples, reconciled hourly evidence and artifact hashes are in `research/runs/liquidation-audit/`.

An isolated Parquet reader, pyarrow 25.0.1, was installed inside that research directory. Production dependencies and bot settings were unchanged. The code graph was updated. No portfolio backtest, training, orders, account creation or paid service was used.

Sources: [CryptoHFTData liquidation documentation](https://www.cryptohftdata.com/datasets/bybit-liquidation-data), [published gaps](https://www.cryptohftdata.com/docs/data-gaps-and-corrections), [Tardis coverage](https://docs.tardis.dev/historical-data-details/bybit), [Tardis schema](https://docs.tardis.dev/downloadable-csv-files/data-types#liquidations), [Bybit raw feed](https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation).
