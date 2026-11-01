# Why the current paper strategy has not entered

October 24, 2026. This is a read-only diagnosis of the primary account's recorded 17:00 UTC closed-candle decisions, captured at17:30UTC. It does not evaluate alternate parameters, simulate fills/returns or estimate a winning rate. All129 local runtime module hashes match the actual successor runtime observed at16:54UTC; no strategy or runtime file was changed.

The primary journal has17 distinct evaluated hourly candles per asset: BTC/EUR, ETH/EUR and XRP/EUR. That is51 recorded decisions, each `no_trade / entry_condition_not_met`. Worker batches and the eight virtual wallets must not be counted as independent entry opportunities. The six-hour cutover observation gap remains part of coverage.

The three latest decision IDs were matched to their original finalized common batch `0389e479-4e55-4bbb-a5a9-2a2bd187e497`, created at17:00:47.682057UTC. Its original input hash validates. Each recorded input has255 closed prices and255 OHLC rows, enough for the native indicators. Pure native indicator arithmetic on those exact inputs gives:

| Asset | Previous RSI | Current RSI | Required recovery | EMA20 | ADX14, required ≤40 |
| --- | ---: | ---: | --- | --- | ---: |
| BTC/EUR | 18.23126 | 31.88070 | Previous ≤33, current >33 | Falling | 54.81655 |
| ETH/EUR | 9.95025 | 17.72727 | Previous ≤31, current >31 | Falling | 62.23456 |
| XRP/EUR | 25.50082 | 34.97223 | Previous ≤35, current >35 | Falling | 63.04802 |

All three had a rising last close, but current RSI remained below its recovery threshold, EMA20 fell and ADX exceeded40. Each therefore failed three configured entry predicates. The observed rejection is consistent with the original input; missing warmup does not explain this cohort. Asset overrides make the effective thresholds33/31/35; the global26 is not the actual threshold for any of these three assets.

The native entry function returns early when RSI recovery fails. EMA/ADX here are independent diagnostic checks on the same frozen input, not a claim that the runtime continued through those branches. This decomposes the latest three decisions, not the other48. Independent verification using AST-extracted pure helpers matches all15 exact Decimal values,24 component checks, three decision mappings and255-row price/OHLC alignments. [Independent verification](../../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/entry-diagnostic-20261024/independent-native-verification.md).

These values use the implementation's RSI over14 price changes, separately seeded shifted20-close EMA windows and rolling-DX ADX. Values from a different chart formula are not interchangeable evidence. The strategy requires all filters together on the evaluated closed candle. A low RSI alone is insufficient.

The generic rejection occurs before sizing, fees, exposure and venue checks. This cohort therefore establishes neither that EUR50–200 can execute a qualifying order nor that a larger wallet would produce profitable trades. One cohort and fewer than one elapsed day also establish no expected trade frequency, financial improvement or trading edge. The source review found no static contradiction, which is weaker than proving that entries will occur often enough to cover costs.

Next evidence comes from later closed candles, eligible entries/closes and the native five-close review. Thresholds, risk limits, fees, original clocks and fixed control remain unchanged. The owner subsequently approved the separate disabled local Discord repair, now implemented and under final release verification; deployment/live messages remain unapproved. No notification request was sent by this analysis.

Evidence: [original recorded input and decision bindings](../../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/live-cutover/observe_recorded_signal_inputs.native-result.json), [exact diagnostic values](../../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/entry-diagnostic-20261024/native-indicator-diagnostic.json), [installed source pins](../../hermes-paper-operational-corrections/research/runs/paper-operational-corrections-v1/entry-diagnostic-20261024/source-pins.json), [native predicates](../hermes_trading/strategy.py#L180), [effective configuration](../config/paper-beta-v1/adaptive_200/strategy.yaml).
