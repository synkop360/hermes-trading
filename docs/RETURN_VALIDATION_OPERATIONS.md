# Return validation: operating notes

The September 12 review and subsequent approval authorize implementation corrections and isolated research. They do not enable real orders. The exchange cycle retains its validation-only restriction. Running services and active state files are not restarted or overwritten by this work.

## Implemented behavior

- Market execution is the common evaluated policy. Defaults are 0.8% taker, 0.4% maker and 0.1% slippage per leg. Explicit maker simulations remain available for historical compatibility; maker configurations cannot silently enter through the market-only exchange path. Account-specific fees remain unverified.
- Simulation and exchange validation share initial ATR stop construction, ATR target construction and trailing stop ratchets. Entry stop distance is persisted in the order intent and adjusted to the reconciled fill price. Existing initialized protection is not reset to the percentage fallback.
- A live partial-exit signal sizes a partial sell, rejects unplaceable legs/remainders, cancels existing protection and protects the reconciled remainder. Stop fills observed during preparation or cancellation require reconciliation before another order is sized. Exchange validation remains restricted to validation-only gateways.
- Reconciliation reconstructs cumulative exit proceeds and proportional entry-fee allocation from persisted orders, avoiding double allocation across distinct exit orders and restarts. Partial exit count survives reconciliation.
- Risk sizing includes fees and slippage. Simulation supports symbol/portfolio/order caps, order minimums and quantity precision; it does not round small positions up to meet a minimum.
- With an ATR target and trailing stop configured together, the entry cost screen uses the trailing distance rather than the inactive profit target. This is a volatility filter, not a prediction of expected profit. Reflection cannot raise an inactive target to bypass it.
- `research.frozen: true` stops both local and Hermes reflection before reading trade evidence or calling models. Research candidates and original strategy bytes are retained with SHA256 hashes.
- The new `trend_pullback` signal requires a rising slow moving average, a fast average above it and a price recovery across the fast average following a shallow pullback. The fixed comparison uses 4-hour candles and 20/100 periods; no parameters were fitted to the reported windows.

## Reproduce the comparisons

From the project root, using its existing Python environment:

```powershell
.venv/Scripts/python.exe scripts/run_return_validation.py
```

The default original is the preserved research snapshot, not the changing runtime strategy. Historical cached data is read from `market_data_cache/`. Recent converted CSVs and the source manifest are stored under `research/runs/return-validation/recent_data/`. Add `--fetch-recent` to acquire or verify the bounded September 2025–August 2026 Binance monthly archives and their published checksums. The downloader records unavailable months rather than inventing observations. Changing a frozen candidate requires creating a new version; the script rejects conflicting existing candidate contents.

Outputs are in `research/runs/return-validation/`; rerunning replaces these result files. Preserve an output copy when beginning a different experiment. The frozen candidate manifest is in `research/candidates/manifest.json`.

## Assumptions and limits

Research assumes 50 USDT, nominal pre-cost risk budget `0.0025 × 0.5 = 0.125%` per position, at most two positions, 5% per symbol, 10% total exposure and a 5 USDT order cap. These reflect the stricter exchange-path defaults, not the old simulator's 50% per-position allowance. The 8% research halt is an assumed evaluation limit from the existing goal; exchange-path default daily-loss and drawdown restrictions remain stricter (0.5% and 3%). No financial risk limit was increased.

Current Kraken public minimum quantities, minimum cost and quantity precision are applied retrospectively. At a 50 USDT balance, many valid signals produce orders below those minimums. This is a feasibility finding, not evidence that a dormant strategy has an edge. Account-specific trading eligibility, fee tier and regional pair availability have not been verified.

Research entries use the next observed candle open with prior closed indicator history. Stops use the adverse opening price when the candle gaps through protection. Signal exits are approximated at the candle close with slippage. OHLC data cannot reproduce order-book depth, actual latency or intrabar event ordering; simulation/live parity does not mean exact fill equivalence.

Portfolio drawdown uses estimated net liquidation value at candle closes. An 8% trigger cannot guarantee losses stop at exactly 8%; gaps and exit costs can overshoot. A missing asset candle retains a forced liquidation obligation until its next observed open. The live path retains its own reconciliation and protective-stop safeguards.

Operating-cost sensitivity is an ex-post subtraction for stated hypothetical monthly budgets, not measured bills or a cost-driven re-sizing simulation. Main performance tables include trading fees and slippage but exclude unverified operating costs and taxes. Benchmarks are a fully invested equal-weight basket and idle cash; the basket intentionally has different exposure and no strategy drawdown halt. Its idealized fractional allocations are a price benchmark, not a validated exchange order plan.

The 2022 sample was previously used for extensive selection. Current strategy development and live reflection may also overlap the recent retrospective windows. Window labels describe chronological partitions; they do not establish pristine unseen holdouts. Overlapping rolling 90-day returns are descriptive observations, not independent probability estimates.

## Deployment status

The code and frozen candidates are reviewable locally. Active `state*/strategy.yaml`, running processes, Railway deployment, exchange credentials and live-trading approvals are preserved. No candidate is promoted on these results. See `RETURN_VALIDATION_RESULTS.md` for the observed returns and the reason no return-maximizing configuration is being declared.
