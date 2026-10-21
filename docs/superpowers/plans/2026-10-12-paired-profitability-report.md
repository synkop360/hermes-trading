# Paired profitability report implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce the preregistered primary EUR100 paired performance report from authoritative prospective observations, without promoting incomplete evidence.

**Architecture:** Read completed shared settlements and canonical orders/positions in read-only snapshots. Extract aligned daily net liquidation equity and complete entry lineages; calculate fixed-original-order2x-cost sensitivity and paired circular block-bootstrap statistics. Report unresolved evidence/coverage limitations explicitly and never enable real trading.

**Tech stack:** Existing SQLite/Decimal/NumPy, unittest. No new dependency, provider request, economic backtest or deployment.

**Spec:** docs/superpowers/plans/2026-10-10-paired-paper-runner.md and research/runs/adaptive-paper-setup-v1/manifest.json.

## Frozen method decisions

Before activation, freeze seed20261010, explicit PCG64,10000replications,7-dayprimary/14-daysensitivity, one-sided95%percentile lower bound (5thpercentile, linear quantile). Resample both arms with identical circular block starts; truncate the final block to the original daily-series length. Report standalone adaptive growth separately from adaptive-minus-control improvement. Chronological90-day blocks are consistency checks, not independent replications. No early qualification or secondary-wallet selection.

Daily boundaries use actual common settled observation timestamps at/before each boundary, tolerance2minutes, maximum within-window gap5minutes. Missing/unknown/estimated expense coverage, external flows, positive-equity failures and inconsistent provenance are explicit blockers. No interpolation or zero-cost substitution.

Stress preserves original filled orders, quantities, entry contracts and strategy epochs. Reconstruct unslipped execution price from the original fill and retained slippage; double fees/slippage and accrued operating/funding costs. Charge cross terms once, use original marks for remaining inventory, and report cash feasibility/insolvency and drawdown. This conditional fixed-order sensitivity is not a new optimized strategy or a rerun of the LLM.

Group partial legs under the unique original buy-position identity using retained asset/opened_at/entry_price/entry-version linkage. Count only fully closed, quantity-reconciled lineages at their final close. Missing/ambiguous linkage or recovery outcomes cannot qualify.

## Tasks

- [x] Add test-first pure paired bootstrap and aligned calendar extraction in paired_statistics.py. Validate constant series, exact small circular distribution, paired dependence, final-block truncation and malformed input.
- [x] Add test-first fixed-order cost stress and complete entry-lineage grouping. Validate hand-calculated fee/slippage cross terms, remaining inventory, cash infeasibility, partial independence and recovery exclusions.
- [x] Add read-only shared-settlement/canonical-order report and CLI. Check account/manifest/protocol, pending/missing/inconsistent data; distinguish checkpoint/final assessment and retain blocking reasons. No real-order promotion. Runtime prelaunch protocol enforcement remains incomplete.
- [x] Run focused and full tests, independent review, AST graph update. Refresh unactivated release bindings/package after production changes; preserve earlier package/build receipts as historical.

## October13 verification and remaining scope

985 full unittest checks passed in102.344s. Thirty focused reporting/statistics/stress checks cover the pure kernels and selected source failures; no complete positive180-day source-path fixture has yet been validated. Independent review required fixes for operating-cost cash reservation (including temporary intrabatch deficits), future-window failures, omitted settlements, original packet hashes, YAML/Decimal exceptions and evidence retention on interruption. Final scoped review found no remaining important reporting issue.

Receipts:research/runs/paired-report-v1; complete118-file source package:research/runs/paired-report-release-v1. The partial context from the first failed packaging import is incomplete and unsuitable for deployment. New package passes archive/source hashes, staged import origin, offline lock check and unactivated startup refusal without writes. Earlier Linux image tests apply only to their earlier source; the new package has not been built/tested as a new container.

These completed implementation tasks are not completion of the profitability method or overall goal. Before activation, implement an authoritative common inception observation without fabricated historical data, freeze/enforce the protocol in the actual manifest/runtime, validate full reporting and statistical assumptions/coverage, finish stressed venue/risk feasibility and actual operating-cost scope, and resolve capacity/supervisor uptime. Paid5GBapproval remains pending. No provider inference, market collection, economic backtest, paper activation, deployment, paid resize or real order occurred.

## Review focus

Actual common-data provenance and complete-cost coverage; baseline/inception endpoint coverage; original versus current strategy contracts; partial/root ambiguity; intraday stressed cash or drawdown breach hidden by daily endpoints; finite drawdown/insolvency behavior; nonstationarity/selection and bootstrap coverage assumptions. Numerical unit checks alone cannot establish statistical coverage or future profits. Qualification stays blocked until the complete method and evidence meet these requirements.
