# Wallet observation integration plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Record prospective paper-wallet observations from each worker cycle and expose honest account metrics to its heartbeat and LLM supervisor.

**Architecture:** Preserve fetched quote snapshots in CycleResult and add a quote receipt timestamp at the price adapter. An explicitly configured wallet_account.json binds currency, capital, inception and experiment identity. Its cost records are cumulative accruals through a stated timestamp, with provenance and explicit unknowns. The observer reads authoritative positions from the repository, values current inventory at received bids with configured liquidation-cost estimates, appends the real observation to WalletJournal and writes wallet_metrics.json. The configured worker reports this account gate instead of interpreting closed-trade sums as wallet performance. The supervisor snapshot carries the report and the LLM prompt preserves it as input, including incomplete history and unknown costs.

**Spec:** adaptive-paper-recovery-goal.md criterion5 and wallet-metrics.md.

## Constraints

- Maintenance stays active. No model request, deployment, real trade or heavy historical experiment.
- Missing wallet configuration leaves legacy operation unchanged but explicitly unconfigured; it is not restored account measurement.
- Existing pre-experiment positions cannot be silently imported into a fresh virtual wallet. Use a reconciled account or isolated experiment state before activation.
- Positions, quotes and costs must match declared wallet currency. Fresh received bid is a paper liquidation estimate; do not pretend last-trade ticker time is the age of a current bid. Record unknown exchange quote age explicitly.
- Cost coverage timestamp must cover the valuation within a fixed freshness interval. Cumulative zero values require an explicit source/quality; missing categories or prices remain unknown. No USD/EUR conversion without verified data.
- Worker valuation time follows quote acquisition/trade processing, not the earlier loop-start timestamp.
- Observations are prospective only. Calendar history remains incomplete until it exists. A30-day operational report cannot qualify profitability; the new gate requires independent evaluation evidence rather than inheriting a legacy PASS.
- Keep closed-trade score as a diagnostic. Configured accounts size entries from a fresh pre-cycle net equity valuation. Unknown/nonpositive equity or incompatible account data blocks new entries while exits continue. Unconfigured legacy sizing remains unchanged. Existing-position contracts still require explicit final release validation.

## Tasks

- [x] Add tests for observer baseline/open-position quotes, cumulative costs, stale/missing accrual coverage, incompatible historical positions and restart.
- [x] Implement wallet_observer.py with atomic report writing and repository positions; preserve marks in CycleResult and stamp quote receipt time.
- [x] Wire paper worker heartbeat and supervisor report input, with tests showing configured metrics cannot inherit legacy profitability qualification.
- [x] Run focused/full verification, independent review and graph update; record remaining sizing/contract/market/release gates.

## Verification checkpoint — October10,2026

32 wallet-focused tests and716 full-suite tests pass (45.901s). Actual inventory, cumulative costs and quote receipt times feed prospective observations; no month of equity is synthesized. Configured entries use marked net equity for risk and a separate cash cap reconstructed from committed positions, including same-cycle fills and remaining entry fees. An initialized wallet refuses missing configuration instead of falling back to legacy sizing. Fixed-account identity is checked before entry sizing; validated activation time stays attached to that observation. Independent review identified these issues; regression tests reproduce and verify the fixes. The initial re-review was interrupted by reviewer usage limits; a later read-only retry confirmed both blockers resolved and found no additional important blocker within this stage. No configuration was operationally installed, no paid strategy-model request was made and no release was deployed.

Evidence: research/runs/wallet-observation-integration-v1/verification.json and full-tests.log. Remaining release gates include existing-position contracts, permitted markets/fees/minimums, actual or explicit estimated cost coverage, deployment reconciliation and prospective adaptive/control evidence.
