# France paper-market preflight

October10,2026. Public market metadata and asks observed at 2026-10-10T08:59:00.228073+00:00. No account authentication, orders, deposits or strategy change.

Kraken continues to prohibit USDT trading for EEA residents including France. Proposed recovery accounts therefore use EUR spot pairs. Public EUR listing is evidence of an available market, not proof that this particular account is permissioned. Sources: [EEA restrictions](https://support.kraken.com/articles/stablecoin-offerings-for-eea-clients), [market metadata documentation](https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs).

The published lowest spot fee tier is0.4%maker/0.8%taker; authenticated fees remain unknown and public pair fee arrays were empty. Preserve these explicit conservative assumptions until authenticated read-only fee evidence is available. [Fee schedule](https://www.kraken.com/features/fee-schedule).

| Pair | Approximate minimum quote at snapshot | Fits EUR50 cap | Fits EUR100/150/200 cap |
|---|---:|---|---|
| LINK/EUR | EUR6.37 | False | False |
| SOL/EUR | EUR5.89 | False | False |
| ETH/EUR | EUR2.23 | True | True |
| BTC/EUR | EUR3.70 | False | True |
| XRP/EUR | EUR2.07 | True | True |

Sizing here retains5%symbol exposure and EUR5maximum order. ETH/EUR and XRP/EUR pass the quantity/notional cap check at all four wallet sizes; BTC/EUR first passes within this range at EUR100. SOL/EUR and LINK/EUR exceed EUR5even for the larger wallets, so raising capital alone does not permit them under the existing order cap. Do not silently raise that cap to manufacture feasibility.

These are optimistic snapshot quantity/notional checks, not final entry authorization: risk budget, stop distance, modeled fees/slippage, cash, quantity precision, partial-close minimums and fresh quotes must all pass at each opportunity. No profitable minimum deposit follows from this table. No FX conversion is required for these EUR virtual wallets; no claim is made that the earlier USD50 has actually been converted or funded.

Next concrete setup: retain EUR50/100/150/200virtual accounts with common contemporaneous EUR spot observations and prespecified minimum-size skips. Provisional EUR100primary can consider BTC/ETH/XRP; EUR50may skip BTC. A common ETH/XRP comparison can be used across all wallets. This operational paper setup does not make the prior nine-asset quiet-volume reversal protocol feasible, or validate the RSI starting rule. Pair selection and candidate/control protocol must be pinned before launch.

Evidence: research/runs/france-spot-preflight-v1/assetpairs.json, ticker.json and minimums.json. Prices and minimums are perishable; revalidate for release and entries.
