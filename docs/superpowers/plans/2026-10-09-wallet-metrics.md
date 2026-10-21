# Wallet metrics implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Record and calculate actual calendar-based paper-wallet net liquidation performance without synthetic per-trade compounding or double-counted costs.

**Architecture:** Add a Decimal valuation and durable SQLite observation journal, separate from the closed-trade diagnostic score. For the existing long-only spot engine, starting capital plus closed net P&L plus open liquidation P&L minus remaining entry fees and accrued operating costs gives net liquidation equity. Quotes must be explicit fresh bids with a conservative slippage/exit-fee model; missing marks/costs produce unknown net equity. Calendar30-day returns require an observed boundary, continuous observations and daily boundary valuations. Missing history is never backfilled from trade percentages or carried silently through a data gap.

**Spec:** adaptive-paper-recovery-goal.md criterion5 and four fixed-capital wallet scenarios. No deposits/withdrawals are planned; nonzero external flows require explicit flow-adjusted valuation support and cannot qualify this fixed-capital report.

## Method and constraints

- Existing Position.realized_pnl is already net of allocated entry/exit fees; fill prices already include executed slippage. Do not subtract these trading costs again.
- Partial closes count their net P&L once, and the remainder's allocated entry fee is deducted once. Open inventory's basis is not lost cash; report free cash separately from equity.
- All positions and costs must match account quote currency; conversion cannot be assumed. This module initially supports the current long spot domain, never substitutes its accounting for derivatives.
- Accrued hosting/AI/tool/funding totals are explicit account-currency inputs with known categories/provenance. Any unknown applicable total keeps net equity unknown, while known-cost equity remains labeled provisional. Funding receipts can be signed; expenses cannot be negative.
- Valuation rejects malformed, future, duplicate/conflicting identity data, negative quantities and stale/nonpositive bids. Use Decimal monetary values without binary floats.
- A UTC rolling30-day boundary and31calendar endpoints are required. Report drawdown from all observed equity samples, daily return mean/sample volatility and annualized daily-return ratio using365 days (explicit zero risk-free reference, not a validated risk-adjusted performance claim). Constant returns have undefined ratio, never an invented high score.
- Gaps exceeding the configured maximum observation interval or missing daily endpoints keep performance incomplete. Bankruptcy/nonpositive equity is reported explicitly; no logarithm or division through insolvency.
- SQLite FULL-sync append-only observations survive restart and reject conflicting timestamp/experiment data. History inception is explicit and cannot be reset/reconstructed from existing trade history.
- Keep maintenance active. No model, deployment, actual trade or heavy historical experiment in this task. Worker integration and removal of the old economic-gate dependency follow after valuation/journal review.

## Tasks

- [x] Write failing tests for partial-close fee reconciliation, long open losses, missing cost/quote, stale/future/invalid marks, currency/identity conflicts and no double-counting.
- [x] Implement valuation and SQLite journal, with finite Decimal serialization and append/restart guards.
- [x] Add rolling-window tests for exact calendar return/drawdown/daily statistics, incomplete boundaries/gaps, no synthetic history, constant-return uncertainty and insolvency.
- [x] Run focused/full checks, independent review and graph update.
- [ ] Stage worker/LLM report integration next; do not claim original scoring or deployed economic gating is already replaced.

## Focused evidence and limitations

Seventeen focused tests pass. A bounded fee-split invariant covers108combinations: four wallet sizes, three quantities, three closed fractions and three exit-fee models. Splitting a position and realizing a leg at the same modeled bid preserves total net equity; remaining entry fees and executed costs are counted once. This is bounded enumeration, not a full-domain randomized property campaign; no dependency was added.

Valuation requires an explicit currency and cost/mark provenance. Missing applicable hosting/AI/tool/funding totals or liquidation quotes leave net equity unknown. Executed fee/slippage ledger categories cannot be passed as additional accrual deductions. Identity, currency, side, finite monetary values and position/quote timing are validated; current short/derivative and unsettled-position states require separate accounting/reconciliation.

Rolling performance requires31distinct observed calendar endpoints with bounded tolerance, no excessive observation gap and known net equity throughout the observed window. Maximum drawdown is explicitly sampled from observations. The annualized daily-return ratio uses a stated zero reference and365-day scale; zero variance or unsupported numerical range cannot become a fabricated high score. Insolvency is explicit. Journal startup capital, experiment identity and inception are immutable, and observations carry explicit matching capital/zero external flow. Actual injected insert failure rolls back and permits clean retry.

Independent review found a starting-capital binding gap and overly broad boundary tolerance. Both were reproduced and fixed; follow-up review found no module-scope blocker. The module is not connected to deployed worker sizing, LLM reports or the legacy economic gate yet. No operational journal was created, and no historical performance was synthesized. Next collect prospective bid/fee/cost observations in the worker and route account-level metrics through the supervisor/gate while retaining closed-trade diagnostics as such.

Final verification:699full-suite tests passed in103.384seconds(session39309exit0). Graph49595exit0:3116nodes8869edges173communities. All verification handles are terminal, and whitespace checks passed. Source hashes and test output are preserved in research/runs/wallet-metrics-v1. Maintenance remains active; no deployment, actual model call, heavy backtest/training run or real order occurred.

## Source

CFA Institute/GIPS handbook on calculation methodology: https://www.gipsstandards.org/wp-content/uploads/2021/04/gips-standards-handbook-firms.pdf. The implementation uses fixed-capital paper portfolios and daily geometric returns; it does not claim GIPS compliance or infer unobserved flow valuations.
