# Crowding reversal — rejected

Completed September29,2026. The registered rule fails in both evaluation blocks. Do not advance it to Kraken portability, portfolio simulation or paper trading.

| Period | Events | Net wins | Mean net/event | Higher-slippage mean | 95% quarter-cluster interval |
|---|---:|---:|---:|---:|---|
| 2022 development | 35 | 45.7% | -1.215% | -1.598% | unavailable: only four quarters |
| 2023–2024 evaluation | 87 | 54.0% | -0.454% | -0.862% | -1.917% to +0.959% |
| Jan2025–Aug2026 recent | 73 | 41.1% | -0.741% | -1.137% | -1.633% to +0.345% |

These are signed returns on position entry notional, not USD50 account returns or compounded gains. The model includes funding, adverse execution slippage and both notional-based trading fees. It has no stop or margin simulation and therefore cannot prove an event was survivable in a real leveraged account. No capital was allocated.

The rule uses a completed day's funding sum against its preceding 60-day distribution. Positive funding above the 90th percentile plus a falling price triggers a short; negative funding below the 10th percentile plus a rising price triggers a long. Entry is at the following 01:00 UTC and exit 72 hours later. All parameters were fixed before outcomes. Both assets were retained without choosing a winner.

Funding helped by an average +0.126% per event in 2023–2024 and +0.015% recently. Price P&L after slippage averaged -0.469% and -0.646%, respectively, before fees of approximately 0.11%. A 54% historical win fraction did not compensate for adverse tails. The worst event was an XRP short entered November14,2024 01:00 UTC, with hypothetical net return -64.756% over 72 hours. Treat this as a major failure of the unstopped event hypothesis, not a permissible loss budget.

Confidence intervals resample occupied calendar quarters jointly across SOL and XRP, 2,000 draws with seed20260928. Both evaluation blocks meet the event-count and occupied-quarter minimums, but neither the mean-return nor uncertainty gate passes. These are retrospective periods with dependence, multiple-trial and asset-selection limitations; intervals are descriptive and do not guarantee future coverage.

## Integrity and exclusions

Inputs were the previously validated Bybit perpetual/mark/funding histories. The runner verifies source-page hashes, normalized-price hashes, funding agreement with archived pages and exact funding timestamp schedules. SOL's late-2022 two-hour schedule is inferred from primary API records, with a December transition corroborated by the provider's announcement channel; it is not independently certified. That uncertainty remains, even though this candidate already fails its later-period gates.

Each asset excluded 61 days for feature warmup. Cooldown suppressed 291 SOL and 294 XRP daily decisions; valid no-signal counts were 1,255 and 1,251. No signaled outcomes were censored at period boundaries. Held-price gaps cause failure, not imputation. Raw funding observations at future times cannot enter the signal. All recorded discrete funding payments during the holding interval are included.

## Verification and artifacts

Seven focused synthetic tests passed after the initial expected missing-module failure, followed by all 580 tests in the full suite. Independent review found no material timing or accounting issues and confirmed rejection under the fixed rules. The review explicitly confirmed that stopping before the Kraken portability stage follows the protocol. The code graph was updated after implementation.

Immutable artifacts: `research/runs/crowding-proxy-v1/study-v1/` contains frozen source/protocol/input hashes, every event, exclusions and results. The runner refuses overwrite. Source: `hermes_trading/crowding_study.py` and `scripts/run_crowding_study.py`; tests: `tests/test_crowding_study.py`. The model uses Bybit global fee assumptions as research inputs, not proof of French account access or Kraken execution.

Decision: retain the failed trial in the research ledger. Do not reverse the signal, tune its thresholds or choose only favorable assets/years from these results. Further progress requires a separately justified hypothesis and appropriate independent evidence; no existing candidate has qualified for trading.
