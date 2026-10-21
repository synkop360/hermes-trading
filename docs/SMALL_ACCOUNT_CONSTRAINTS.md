# Confirmed account constraints

2026-09-28: user confirmed **USD50 starting capital, account registered in France**, and discretion to consider spot or derivatives. This does not authorize paid services or real-money orders. Paper readiness still requires credible net evidence and executable sizes.

The current code defaults cap symbol exposure at 5% and total exposure at 10% (`hermes_trading/risk.py`). At USD50, that is USD2.50 per symbol and USD5 total. Runtime overrides have not been reverified in this checkpoint; these are repository-default calculations, not authenticated account settings.

Kraken public AssetPairs and contemporaneous spot quotes retrieved September28 show:

| Pair | Minimum coin quantity | Approximate minimum notional | Equity required at a 5% cap, before buffers |
|---|---:|---:|---:|
| BTC/USD | 0.00005 BTC | USD4.1973 | USD83.95 |
| ETH/USD | 0.001 ETH | USD2.6957 | USD53.91 |

Both exceed USD2.50 at this snapshot. Fees, rounding and safety buffers increase the practical requirement. Prices vary; these are not recommended deposits or balances that ensure profit. Raw metadata and quote responses are in `research/runs/small-account-feasibility/`.

Kraken explicitly lists USDT as non-tradable for EEA clients, including France. Existing USDT research remains a historical proxy, not a deployable Kraken pair choice for this account. Future executable candidates must use a supported quote currency such as EUR or USD and verify the actual account's available markets. Do not silently reuse USDT deployment defaults. Source: https://support.kraken.com/articles/stablecoin-offerings-for-eea-clients .

Kraken has an EEA derivatives eligibility process; eligibility for this individual account has not been established. Global Bybit derivatives histories and published global fees do not establish access through a French/EEA account. Source: https://support.kraken.com/articles/derivatives-eligibility-requirements-eea .

Research priorities: determine executable market universe and total costs at USD50 before another heavy signal study; favor no paid data or per-decision AI cost; do not loosen risk limits solely to force orders or assume leverage creates an edge. Increasing capital solves some order-size constraints but cannot rescue failed expected returns. No current candidate supports doubling USD50 in three months.

## Broad market-size audit

Public AssetPairs and all-ticker snapshots acquired September28 cover 625 online USD pairs and 500 online EUR pairs with usable positive bid/ask quotes. This public list is not an authenticated French-account eligibility list. Minimum notional is the larger of `costmin` and `ordermin * ask`; the size screen also reserves 1% above that minimum. EUR notionals use the contemporaneous EUR/USD ask of 1.13749; actual conversion costs are additional. This is an initial size screen, not a complete executable order calculation including lot rounding, stop sizing and fees.

Only six USD pairs fit the USD2.50 symbol cap: LAPTOP, AIN, BTR, TMX, CHILLHOUSE and NOBODY. Only BTR/EUR fits among EUR pairs. Their USD-pair 24-hour quote volumes range from approximately USD2,317 to USD121,456; spreads range from 22.8 to 294.1 basis points. These modest volumes and, in some cases, wide spreads give no basis to select them merely because their minimums fit. Current listings selected by today's metadata would also introduce survivorship bias into historical research.

USDG/USD has a special published zero-maker-fee schedule, but its five-coin minimum is approximately USD5, above the current symbol cap. Its observed one-basis-point spread would yield only USD0.0005 on a USD5 completed spread capture before adverse selection and other costs. A fee exception alone does not make a useful small-account strategy.

Kraken linear perpetuals provide a potentially more practical research universe without increasing account notional exposure. Official EEA specifications give SOL a 0.01-coin minimum and XRP a one-coin minimum. Earlier same-day public quotes imply about USD1.19 and USD1.48 respectively, within USD2.50. This checks size only, not trading eligibility or profit. The published base futures taker rate is 0.05% per leg; funding, spread and execution slippage must also be modeled. The account's futures access remains unconfirmed: Kraken requires verification, an appropriateness questionnaire and a TIN. Do not request the TIN or credentials in this chat.

For scale, USD1 of monthly operating cost consumes 2% of a USD50 account per month. If only USD5 total notional is working, it requires 20% monthly net return on that deployed notional just to pay that bill. This arithmetic does not predict achievable returns. Incremental paid infrastructure or repeated paid AI decisions would be difficult to justify.

Next step: inspect free same-venue history for SOL/USD and XRP/USD perpetuals and define one causal hypothesis only if adequate history exists. Investigate financing/crowding information and price response rather than repeatedly refitting failed spot momentum rules. Keep gross notional within current caps; do not treat an exchange's leverage allowance as a target. A candidate must include funding, order minimums and the actual USD50 portfolio in validation.

Evidence: `research/runs/small-account-feasibility/universe/` contains raw pair/ticker responses, USD/EUR size-screen outputs and hashes. Primary references: https://www.kraken.com/features/fee-schedule ; https://support.kraken.com/articles/perpetual-contract-specifications-for-clients-in-the-eea ; https://support.kraken.com/articles/derivatives-eligibility-requirements-eea .
