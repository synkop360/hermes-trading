# Bybit liquidation archive discrepancies

Prepared 2026-09-28 for manual review. Not sent to the provider.

Please investigate two discrepancies in the Bybit BTCUSDT/ETHUSDT liquidation archive:

1. On 2025-07-01, many rows repeat identical exchange fields with different receipt timestamps. Retrieved BTC files contain 3,665 rows representing 1,338 unique event keys; ETH contains 3,194 rows representing 1,316 keys. Independent Tardis samples contain 1,340 and 1,318 rows respectively. Repeated-copy receipt spans reach approximately 2.01 seconds. Are these duplicate collector deliveries, and is a corrected archive or stable deduplication identifier available?

2. These hourly files return HTTP 404 despite corresponding events in the independent reference:

| Date | Asset | Missing UTC hours |
|---|---|---|
| 2025-07-01 | BTCUSDT | 03, 22 |
| 2025-07-01 | ETHUSDT | 03, 06 |
| 2026-01-01 | BTCUSDT | 06, 13, 14 |
| 2026-01-01 | ETHUSDT | 02, 13 |

Each listed hour has one reference event. Example: BTCUSDT, 2025-07-01 03:11:27.026 UTC, normalized side sell, amount 0.018 BTC, bankruptcy price 106557.9. Both `/download` and `/v1/download` return file-not-found for `bybit/2025-07-01/03/BTCUSDT_liquidations.parquet`.

Can these observations be recovered, and can the archive provide a per-hour completeness indicator distinguishing confirmed zero events from missing collection? Please also clarify whether files are partitioned by receipt time or event time and how corrected files are versioned.

These are cross-provider discrepancies, not a claim that either source is a complete exchange ledger. September 1, 2025 and February 1, 2026 samples agreed exactly for both assets, so the findings appear period-specific.
