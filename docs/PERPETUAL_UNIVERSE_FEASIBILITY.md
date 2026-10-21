# Broader small-account perpetual market audit

September29,2026. **SOL and XRP were not an exhaustive small-account universe.** Five additional contracts pass a preliminary current-market liquidity screen without increasing the USD50 account's risk caps. This is a research-feasibility audit, not a strategy recommendation or authenticated eligibility check.

The saved public instruments response contains278 PF-prefixed contracts;265 match explicit minimum quantities in the captured EEA specifications. All265 have usable positive, uncrossed quotes in the saved ticker response.237 fit the USD2.50 per-symbol budget with a1% minimum-order buffer. The13 API symbols absent from the captured specification table are excluded, not assumed eligible.

For a manageable current research shortlist, apply minimum-order feasibility plus at least USD1million24-hour quote volume, at most5 basis points spread relative to midpoint, and at least USD25 displayed notional on each side. These are exploratory liquidity thresholds, not optimized profitability parameters or evidence of long-run fill quality. Seven contracts pass:

| Contract | Minimum notional at ask | Spread, basis points | 24h volume, USD |
|---|---:|---:|---:|
| SOL/USD perpetual | 1.1827 | 1.69 | 74,240,278 |
| XRP/USD perpetual | 1.5064 | 4.65 | 35,796,136 |
| DOGE/USD perpetual | 0.09381 | 3.20 | 11,149,888 |
| ADA/USD perpetual | 0.24376 | 3.69 | 7,436,339 |
| WIF/USD perpetual | 0.2359 | 4.24 | 3,365,418 |
| GALA/USD perpetual | 0.002254 | 4.44 | 1,645,053 |
| TRX/USD perpetual | 0.33466 | 2.99 | 1,082,482 |

The table reports raw minimum notionals; the screen applies the1% buffer. All seven have sufficient displayed depth under the stated threshold at this snapshot. This does not prove orders would fill, minimum fees are absent, or a correctly risk-sized position is possible. Stop distance, fee/slippage reserves, rounding, order type, funding, margin and total correlated exposure still matter. Both legs of a hedge count toward gross exposure. No cap or live configuration changed.

Primary regional minimum quantities come from [Kraken's EEA contract specifications](https://support.kraken.com/articles/perpetual-contract-specifications-for-clients-in-the-eea), cross-referenced to public instrument/ticker responses. Individual French-account access remains unconfirmed. Derivatives availability is not permission to increase leverage or place orders.

Artifacts: `research/runs/perpetual-universe-v1/` contains public responses, rendered source excerpts, minimum-quantity mapping, all265 screened rows, aggregate summary and SHA256 manifest. The direct support-page HTML request returned403 after the API files had been saved; the readable web-tool version supplied the specification excerpts. The reconstructed manifest explicitly labels file-save times and preserved server times rather than inventing retrieval timestamps. Excerpts cover the specification table continuously;314 PF rows were parsed. No account credentials or private API calls were involved.

September30 verification checkpoint: separate Decimal arithmetic recomputed all265 classifications from the raw inputs, and all five source hashes verified. This is local verification, not an independent reviewer approval; the reviewer request failed with a usage-limit notification. The audit's current-market selection must not become the historical universe for a backtest.

Implication for further work: the earlier SOL/XRP failures do not establish failure across every affordable market. However, selecting this present-day shortlist for an old backtest would introduce survivorship and liquidity-selection bias. A new cross-asset strategy needs a separately frozen economic hypothesis and a historically reconstructed universe using past listing status, volume, costs and minimums. This audit justifies inspecting that data availability, not rerunning rejected strategies on five new assets until one appears profitable. No returns, parameter search, training or paper positions were generated.
