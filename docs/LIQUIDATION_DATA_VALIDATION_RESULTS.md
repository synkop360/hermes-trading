# Liquidation dataset validation — 2026-09-28

**Validation completed: six of ten sampled asset/days pass; the full dataset is not validated and remains unsuitable for the proposed historical signal study.** No strategy returns were measured.

## What was checked

The original audit was preserved. A repair rule was fixed before additional comparisons: group records whose exchange fields all agree, and merge receipts within 10 milliseconds of the earliest receipt in each group. Keep that earliest receipt; retain records with different exchange fields or more distant receipts. This is deliberately narrower than deleting everything with the same price, quantity and timestamp, because no exchange-issued unique event ID is available.

The original July 2025, January 2026 and August 2026 sample dates were diagnostic data. September 1, 2025 and February 1, 2026 were selected as additional validation dates before their files were compared. BTCUSDT and ETHUSDT were compared against independent Tardis daily samples. No reference rows were inserted into the candidate to improve agreement.

## Results

| Date | BTC result | ETH result | Finding |
|---|---|---|---|
| 2025-07-01 | Fail | Fail | Repetition remains after the repair; event-containing files are unavailable |
| 2025-09-01 | Pass | Pass | Exact agreement: 2,091 BTC and 3,986 ETH records |
| 2026-01-01 | Fail | Fail | 3 BTC and 2 ETH reference events are unavailable |
| 2026-02-01 | Pass | Pass | Exact agreement: 2,706 BTC and 3,011 ETH records |
| 2026-08-01 | Pass | Pass | Exact agreement: 482 BTC and 538 ETH records |

The July repair removed 1,988 BTC and 1,471 ETH records, or **3,459 records total**. Candidate-to-reference agreement remains only 79.79% for BTC and 76.38% for ETH. The fixed 99% bidirectional agreement requirement therefore still fails. Extending the receipt-time threshold until a comparison passes would not independently validate the repair; it would need a separate test on data with the same defect. The additional dates contain no records removed by this rule, so they verify clean-date consistency but do not test removal of duplicates on new data.

All nine previously unavailable event-containing hourly files were requested again and still returned HTTP 404. These cover four reference events in July and five in January. They cannot be treated as empty hours. No rate-limit errors occurred in this follow-up: 100 objects downloaded successfully and nine returned 404.

For August, unavailable hours have no events in the independent same-day reference. This supports treating those particular sampled hours as empty; it is not a rule for other 404 responses. The additional September and February comparisons had no unavailable hourly files.

## Schema, timing and verification

All normalized samples passed checks for correct asset, valid side, positive quantity and price, and receipt delay between zero and ten seconds. The rule uses the liquidation event time for matching and preserves receiver time for information availability. Bankruptcy prices are matching fields, never simulated trade prices.

Seven focused checks passed: near-identical receipt copies collapse; receipts outside the allowed interval remain; differences in batch time, event time, quantity and direction remain; earliest receipt is preserved. SHA256 checks verified all 100 newly downloaded objects. Original raw files and earlier results were not overwritten. Independent review confirmed the failure conclusion.

Review also identified a boundary limitation: the implementation reads only the selected date's hourly files, without adjacent-day receipt partitions. It therefore does not fully validate events delivered across midnight. This cannot turn the present failures into a pass; a future passing audit must reconcile adjacent-day files before making a completeness claim. Passing rows in the table mean the sampled comparisons passed, subject to this boundary limitation.

The six passing asset/days are explicitly listed in `research/runs/liquidation-validation/sample_eligibility.json`. Every other date remains unverified or failed. This list is not permission to stitch isolated days into a continuous strategy history: the proposed strategy requires complete rolling baselines and forward outcome windows.

## Decision

Do not run the profitability study using this archive as delivered or this repair alone. Missing observations and unresolved repeated records could distort the liquidation threshold and event selection. Neither provider is an exchange ground-truth ledger; exact agreement between two recordings supports consistency, not universal completeness.

The next useful data task would be to obtain verifiable corrections or an independent continuous archive, then repeat comparisons and audit full coverage. A separately approved prospective recorder is an alternative if historical repairs cannot be obtained economically. No paid purchase, live configuration change, trading, portfolio backtest or model training was performed.

Evidence: `research/runs/liquidation-validation/protocol.md`, `validation.json`, `download_manifest.json`, normalized samples, raw downloads and `artifact_manifest.json`. Original audit: [LIQUIDATION_DATA_AUDIT_RESULTS.md](LIQUIDATION_DATA_AUDIT_RESULTS.md).

Reference documentation: [CryptoHFTData known gaps and corrections](https://www.cryptohftdata.com/docs/data-gaps-and-corrections), [liquidation endpoint](https://www.cryptohftdata.com/docs/rest-liquidations), [Tardis normalized schema](https://docs.tardis.dev/downloadable-csv-files/data-types#liquidations).

## Resumed investigation — 2026-09-28

Checked the previous day's final hour and following day's first hour for each of the ten asset/day comparisons. Retrieved adjacent files contained no events belonging to the target dates. Unavailable adjacent files remain unknown; this check does not establish complete boundary coverage. The nine unmatched reference events occur well inside their UTC dates, so midnight partitioning is not a credible explanation for those particular omissions under the ten-second latency assumption.

The representative missing object `bybit/2025-07-01/03/BTCUSDT_liquidations.parquet` returned HTTP 404 with an explicit file-not-found response from both documented `/download` and `/v1/download` paths. No authentication workaround or private endpoint was used.

The July records contain 1,169 repeated BTC event keys and 941 repeated ETH keys. All exchange fields match within each group; only receipt times differ. Receipt spans reach 2,007.14 ms for BTC and 1,730.75 ms for ETH. This explains why the fixed 10 ms repair leaves repeated observations. A larger threshold could remove more, but choosing it from these same samples would not validate the repair on unseen duplicate-bearing data and would not recover missing records.

The dataset remains unvalidated for continuous strategy research. A provider-ready issue report has been saved in `LIQUIDATION_PROVIDER_ISSUES.md`; it was not sent. Evidence and response hashes are retained under `research/runs/liquidation-validation/`.
