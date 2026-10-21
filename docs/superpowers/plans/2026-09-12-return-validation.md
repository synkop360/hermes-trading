# Return validation implementation plan

**Goal:** Apply the user's approved review recommendations and evaluate profitability without enabling real trading.

**Architecture:** Share stop construction, trailing updates and cost screening between simulation and live execution. Use market execution as the supported common denominator. Preserve current state as an immutable research input; compare fixed strategies chronologically with production signal/position code.

**Tech stack:** Python, Decimal, SQLite, unittest, existing market-data adapters.

## Constraints and decisions

- User approved all proposed corrections and isolated backtests on September 12, 2026.
- Use 50 USDT starting equity and an 8% drawdown research limit, preserving existing configuration until validation. These are assumptions, not newly accepted financial risk limits.
- No real orders, service restarts, deployment, leverage increases or paid AI training.
- Current published Kraken tier-one fees (0.004 maker / 0.008 taker) are conservative research assumptions; an account-specific fee tier remains unverified.
- Existing strategy snapshot is immutable. Reflection is disabled for fixed evaluation and must not mutate these candidates.

## Tasks

- [x] Add regression tests for the unused ATR-target gate, consistent ATR stops and trailing ratchets, partial live exits and restart accounting, fee assumptions, and frozen reflection.
- [x] Implement shared execution policy; preserve protective stops and partial fill reconciliation; run position/live/execution/reconciliation tests.
- [x] Create frozen baseline and corrected candidate files, a deterministic trend/pullback candidate, and chronological research harness using production strategy/position evaluation.
- [x] Test research isolation, no future data, portfolio sizing, full-position statistics, costs, drawdown limits, and rolling 90-day metrics.
- [x] Run historical comparisons using available data; acquire recent public data if feasible; disclose exchange and date coverage, historical selection contamination, operating-cost sensitivity and sample limitations.
- [x] Review changes and run the complete test suite. Update graph and write the research report and operating instructions.
- [x] Apply verified code changes to the shared checkout without altering running services or enabling real trading.

## Verification

Use `python -m unittest` for focused tests and `python -m unittest discover -s tests` for the final suite. Record actual failures and outcomes. Evaluate fixed candidates only, report completed positions separately from partial legs, mark portfolio equity including open positions, and report failure to meet the target without increasing risk to force it.

## Completion record

Applied 24 code/configuration/document files to the main checkout after isolated implementation; runtime state and services were preserved. Final main-checkout suite: 539 passing tests. Ran 24 fixed-strategy evaluations plus cash and basket benchmarks across eight chronological windows; repeated final run reproduced results exactly. No candidate supports the doubling objective; report records costs, minimum-order constraints and retrospective-data limitations. AST knowledge graph updated successfully; JSON configuration files are retained as evidence even where the graph parser produces no code nodes.
