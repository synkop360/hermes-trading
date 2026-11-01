# Dated public feasibility observation

Observed **October 21, 2026, 21:19:58 UTC**. Candidate: approved `6c9d520`, EUR200 primary and EUR50/100/150 comparisons, each with adaptive/fixed arms. This is an anonymous public minimum-size check, with no strategy execution, market backtest, model call, Railway access, risk change or order. It does not start the experiment or bind future activation.

**Finding:** at the returned EUR quotes, none of the three selected assets could fit the €50 or €100 wallet's enabled stress-risk admission budget, even before adding a positive stop distance. The tested necessary conditions do not rule out €150/200; those wallets have **not** passed actual native admission. ATR history, payoff filters, signals, inventory, actual sized quantity and future exit feasibility were not evaluated.

## Public inputs

Three bounded HTTPS GET requests returned HTTP200 and empty API error arrays: France-filtered AssetPairs for BTC/ETH/XRP EUR, their Ticker data, and server Time. The asset records passed the existing native venue parser at their joint receipt time. Metadata age at that observation was about0.007seconds; the server's integer time agreed with the local receipt clock. HTTP response dates and cache headers are retained. Ticker supplies no exchange quote timestamp, so receipt time alone does not establish exchange-side freshness or guaranteed execution.

Native venue records require age **<300seconds**; exactly five minutes is stale. Prospective quotes separately permit age up to five minutes inclusive, subject to their signal-time checks. This document records the collection-time result and supplies no fresh evidence for a later launch.

Kraken documents `country_code` as filtering market listings by country/region. The FR response listed all three pairs online, with eight quantity decimals, a €0.45 cost minimum and the quantity minimums below. This is public market evidence; private account permission remains unverified. [Kraken AssetPairs documentation](https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs), [Ticker documentation](https://docs.kraken.com/api-reference/market-data/get-ticker-information).

| Asset | Returned ask EUR | Effective minimum quantity | Base fill value at minimum EUR | Stressed fill value at minimum EUR |
| --- | ---: | ---: | ---: | ---: |
| BTC/EUR | 75,327.50000 | 0.00006 | 4.52416965 | 4.52868930 |
| ETH/EUR | 2,218.63000 | 0.002 | 4.44169726 | 4.44613452 |
| XRP/EUR | 1.23112000 | 4 | 4.92940448 | 4.93432896 |

Base fill uses `ask × 1.001`; stressed fill uses `ask × 1.002`, holding quantity fixed. Both values exclude the separate fee cash debit. The stressed reserve is an admission guard, not doubled charges in the base account.

## Why the smaller wallets fail

The native initial risk budget is `.0025 × position_size_r .5 × wallet = .00125 × wallet`. In stress admission, the minimum quantity already requires a conservative linear risk allowance of:

```text
minimum stressed fill value × (positive stop fraction + .036)
```

Omitting the positive stop gives a necessary lower bound. The resulting cost-only allowances are €0.1630328148 / €0.16006084272 / €0.17763584256 for BTC / ETH / XRP. They already exceed the €50/100 budgets of €0.0625/0.125. For €50, the 5% symbol-exposure cap also fails.

| Initial wallet | BTC | ETH | XRP |
| --- | --- | --- | --- |
| €50 | Necessary condition failed | Necessary condition failed | Necessary condition failed |
| €100 | Necessary condition failed | Necessary condition failed | Necessary condition failed |
| €150 | Not ruled out by tested bounds | Not ruled out by tested bounds | Not ruled out by tested bounds |
| €200 primary | Not ruled out by tested bounds | Not ruled out by tested bounds | Not ruled out by tested bounds |

The corresponding initial-wallet **risk-only necessary lower bounds** are strictly greater than €130.42625184 for BTC, €128.048674176 for ETH and €142.108674048 for XRP. These omit stop distance and other restrictions; they are neither sufficient starting balances nor profitable deposit recommendations. All figures are conditional on the returned quotes and initial ordinary/stressed equity equal to the stated wallet, with no prior costs or inventory. Costs and price changes can worsen feasibility.

The smallest mathematical quantity combines the stricter of configured/current venue quantity and quote minimums, rounded **up** to the common precision grid. This only asks whether any quantity could fit the interval. The actual bot sizes from risk and rounds **down**; it must skip rather than increase an order to meet a minimum. Passing this preliminary bound cannot authorize a native entry. No proposed order or quantity was submitted.

## Implication for launch

Retain all eight scenarios and the unchanged €200 primary. Record skips for the smaller wallets; do not increase risk, order caps or spend to manufacture activity. At actual inception, collect new market rules/quotes and run the full native opportunity checks. These saved observations are perishable and must not be installed as fresh activation evidence. A valid minimum-size entry would still not guarantee a later valid liquidation; future bids, minimums and partial-close rules must be checked separately.

This observation supersedes the practical relevance of the older [October10 preliminary cap-only table](FRANCE_PAPER_MARKET_PREFLIGHT.md): that historical snapshot remains preserved, but its optimistic quantity/notional result is not current admission proof and did not establish the present stress-risk result. The [cost envelope](PAPER_WALLET_COST_ENVELOPE.md) remains conditional arithmetic; this separate observation supplies dated quotes for its minimum-size question.

No profitable wallet, improved win rate, feasible learning-review frequency or successful doubling path is established. The beta has **0 actual paper days**. A complete skip/inactivity result is an honest operational outcome of the approved protocol.

Evidence: [raw request receipts](../research/runs/paper-public-feasibility-v1/20261021T211957886871Z/collection.json), [full-precision necessary-condition matrix](../research/runs/paper-public-feasibility-v1/20261021T211957886871Z/necessary-matrix.json), [analysis-only native venue records](../research/runs/paper-public-feasibility-v1/20261021T211957886871Z/venue-records.analysis-only.json), [independent contract review](../research/runs/paper-public-feasibility-v1/independent-review/contract.md).
