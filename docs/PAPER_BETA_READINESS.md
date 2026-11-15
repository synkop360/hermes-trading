# Paper beta readiness

Updated October 21, 2026. The controlled experiment has not started. No candidate is qualified as profitable.

The active goal is a safe paper launch as soon as the six gates below are met. Archive/history expansion, full capacity qualification, installed archive recovery and profitability proof are deferred. Preserve the disabled archive/checkpoint code and completed research; neither their completeness nor a winning backtest is a launch prerequisite.

The [verified local release](../research/runs/paper-launch-gate-v1/release-final-verification.json) covers **145 modules,1,618 checks:1,614 passes,4 Windows/platform skips and0 failures/errors/ResourceWarnings**. It composes the completed frozen suite with eight new standalone operator checks, preserving the exact standard-discovery catalog and rechecking all 364 source/config/build hashes. The corrected Docker assertion and slow module were rerun; earlier failed/timed-out attempts remain preserved. Worker/supervisor integration, entry halts, retained protective exits after restart and daily copies during storage monitoring are locally verified. Installed/off-host behavior remains unverified. Paper remains0 days; overall progress is a 55% judgment.

## Offline preparation check

Run `uv run --no-sync --python 3.11 python -m scripts.check_paper_candidate` from a clean, installed checkout to check the disabled bundle before cutover. The [command and byte-identity notes](PAPER_CANDIDATE_CHECK.md) explain its JSON report, Windows LF checkout policy and corrected goal pins. A passing report is preparation evidence only: it never authorizes activation or replaces fresh release, model, cost, venue or installed-runtime checks.

## Current six launch gates

| Gate | Status and remaining work |
| --- | --- |
| 1. Verified local candidate; cutover pending | The disabled 49-file candidate and integrated MVP pass the final local release checks and are packaged in this source revision. Preserve the former worker's evidence and reconcile its stop at approved cutover. |
| [ ] 2. Fee and execution consistency | Use prospective taker execution and declare published estimates: maker 0.004 (0.40%), taker 0.008 (0.80%), slippage 0.001 (0.10%). Reject unsupported maker execution and exclude legacy maker configuration from the launch path. Private fee tier remains unverified. |
| [ ] 3. France/EUR feasibility | EUR200 primary; retain/report EUR50/100/150 skips. Bind fresh public France market rules, quantities and EUR pairs without changing risk limits to force eligibility. |
| [ ] 4. One writer and learning control | Exactly one adaptive strategy writer; five unique new closes trigger supervised review, `no_change` is valid and a change affects at most one variable. Preserve hypotheses, review evidence and prior YAML. The fixed control shares common market inputs and trading-cost assumptions; report arm-specific operating/LLM costs explicitly. |
| [ ] 5. Minimal storage safety and daily backup | Integrated bounded `paper_runtime.log` rotation, an all-file 70% alert for the 500,000,000-byte declared volume and daily consistent backup pass local checks. Daily copies continue during storage entry halts. The completed-copy pull has eight offline synthetic passes and independent review; installed copying remains unverified. Keep archive/checkpoint features disabled. |
| [ ] 6. Accounting and entry-only stops | Show net equity, open inventory, all costs with unknowns kept unknown, drawdown and daily Sharpe. Complete entry-only halts for drawdown, repeated errors, staleness and volume pressure while protective exits remain available. |

Approved deployment and installed cutover verification remain. Commits are authorized; deployment/activation, paid actions, private exchange credentials and risk-limit changes require user approval. Explicitly sourced expense estimates are sufficient for paper; private Kraken credentials, invoices, archive completeness and profitability qualification are not additional launch gates. The review implementation consumes all pending unique closes once the count reaches five; recovery/risk-stop closes pause steering for reconciliation. Durable supervised SQLite review records retain hypotheses/reasons, and publication archives prior strategy YAML.

Fee declaration source: [Kraken published spot fee schedule](https://www.kraken.com/features/fee-schedule). Slippage is the declared simulation estimate; it is not an exchange fee or a measured fill guarantee.

## Results after actual inception

| Point | What we can establish |
| --- | --- |
| Before activation | Correct accounting and execution, minimal volume/log safety and daily backup, one strategy writer, feasible quantities under declared virtual capital, complete opening expense declarations, and a concrete reviewed release. Synthetic tests establish behavior, not returns. |
| From the first settled paper batch | Prospective net equity, costs, skipped entries, risk stops and operational reliability. Report these as provisional observations. |
| After five unique new closes | The first eligible LLM review. A valid review can choose no change; a change affects at most one global variable. This is a trade-count trigger, not a promised date. |
| Day 30 after actual inception | The first planned operational and learning checkpoint, including adaptive versus fixed-control results. A stopped or inactive account must still be reported. Thirty days cannot establish a durable trading edge. |
| Day 90 after actual inception | Report the first three-month net wallet outcome against the doubling aspiration and fixed control, including costs, stops and inactivity. One observed block does not establish a durable edge or satisfy the longer qualification protocol. |
| At least day 180 | Assess the prespecified two 90-day blocks, at least 30 completed original entry lineages per block in each primary arm, full-cost and doubled-cost outcomes, drawdown and statistical evidence. These are necessary conditions; reaching a date or trade count does not guarantee qualification. |

“More efficient” needs two separate measurements. Engineering improvements can demonstrate reliable steering, fewer invalid fills and correct cost accounting. Better financial performance requires contemporaneous net-return evidence against the fixed control on the same inputs and cost basis. The legacy simulation is not a valid substitute for that control. No improved return or win rate is established yet.

The saved-point [economic hurdle calculation](PAPER_BETA_ECONOMIC_HURDLES.md) also shows why sizing eligibility is insufficient: a hypothetical target-boundary ETH winner nets about EUR0.186 at the current quantity and fees. Thirty-eight identical winners with no losses cover only the retained EUR6.90 monthly hosting estimate, before unresolved expenses. This is conditional arithmetic, not a forecast or a new prerequisite to collecting paper evidence.

## Historical entry-feasibility observation (October 18)

A single anonymous public Kraken observation at **2026-10-18 14:51:11 UTC** was passed through the existing native sizing and cost-reserve guards, using the disabled current-venue candidate. The calculation used an empty wallet and zero accrued operating expenses, so it is optimistic. It evaluated sizing only, without evaluating an entry signal or simulating returns.

| Virtual wallet | ETH/EUR | BTC/EUR | XRP/EUR |
| --- | --- | --- | --- |
| EUR50 | Rejected | Rejected | Rejected |
| EUR100 | Rejected | Rejected | Rejected |
| EUR150 | Rejected | Rejected | Rejected |
| EUR200 | Sizing eligible: 0.00236054 ETH | Rejected | Rejected |

The historical EUR100 primary could not support an entry at this observed point under the approved risk reserve. The approved disabled protocol now preregisters EUR200 as the primary; it retains all eight scenarios and the historical EUR100 evidence. EUR200 ETH still needs fresh paper feasibility checks and is not a minimum profitable wallet or a deposit recommendation. Prices, volatility, operating expenses and available signals can change the result. The pre-revision static trace found EUR100 fixed across reporting, risk, worker ownership, supervision and archive identities; the implemented versioned resolver now selects EUR200 consistently in those paths. A future primary change requires another coherent preregistration before inception. Preserve the original risk caps and historical protocol interpretation.

Evidence: [native admission observation](../research/runs/beta-feasibility-2026-10-18/native-admission/verification.json), [raw public-data receipts](../research/runs/beta-feasibility-2026-10-18/native-admission/receipts.json). An earlier necessary-bound check omits stop distance; it cannot substitute for this native calculation. Account permission and actual fees remain unverified.

## Historical activation criteria and engineering evidence

The criteria and archive milestones below are retained historical evidence. The October 20 launch-first direction supersedes their archive/capacity/recovery launch dependencies; only the six current gates above govern the paper launch. No profitability or full-storage qualification is asserted.

1. Resolve a trade-feasible primary and complete current, source-bound operating-cost coverage. Paper inception accepts explicitly declared estimates; missing prices must not become invented zeros. Confirm fresh public France venue metadata, declared fee assumptions, the fixed model and its authorized usage, and virtual-account launch bindings. Anonymous public-data paper execution does not require private Kraken credentials or invoices. Private trading permission and actual account fee-tier checks remain necessary before any real-money use; estimated expenses and unverified fee assumptions also limit financial conclusions. Hosting costs alone can consume the current drawdown budget: at the retained EUR0.23/day estimate and zero trading P&L, EUR200 reaches 3% in about 26 days. That estimate is not an invoice or a forecast; a stopped day-30 report remains possible.
2. Complete whole-cohort recovery, independent archive/claim anchors, the installed coordinator/transport and participation by all storage writers. Qualify combined storage and transfer peaks for the intended bounded beta on the existing Railway volume and local archive. A small exact-cut receiver/nonce copy does not yet establish this. The first 30-day run must fit; a full 180-day economic claim must not be made from a 30-day capacity test.
3. Freeze the complete candidate, finish whole-release checks and review, and prepare a concrete cutover that reconciles the deployed worker and supervisor ownership. Refresh remote facts before claiming the current Railway application is healthy. Obtain deployment/activation approval for that concrete candidate.
4. Start the paper experiment and its clocks at actual inception. Publish failures, costs and inactivity as well as trades. Preserve the 30-day checkpoint and longer qualification protocol rather than waiting for a winning backtest before beginning paper learning.

The previous disabled archive milestone passed **287 affected checks, with one Windows privilege skip**, at 243 frozen runtime/test sources. It verifies a full worker/separate-supervisor capture with a restored V2 receiver/nonce pair, the actual cached ancestor and original unacknowledged protective close. The selected cut is externally pinned, but independent recovery anchors, fencing of a former worker and reconciliation of later activity remain unproved; resumption and retirement stay disabled. A crowded-domain export hit its original 30-second ceiling. [Verification and limits](../research/runs/production-archive-integration-v1/cohort-cut-verification.json).

A disabled capture fix separates read-only ownership/integrity progress from full capacity checkpoints. On the identical256-file synthetic fixture, export time improved from19.5to7.328seconds and full inventory scans from97to36, with original limits and source/retained bytes preserved. This does not establish total beta capacity or resolve the earlier larger-domain counterexample. Full Windows discovery at244frozen sources passed1,448checks with3platform skips andzeroResourceWarnings. [Bounded comparison](../research/runs/production-archive-integration-v1/crowded-pair-export-comparison.json), [full verification](../research/runs/production-archive-integration-v1/capture-progress-full-verification.json). A former worker can retain offline authority after a remote change, so an initial recovery path must require verifiable former-host/process stop and complete final-claim reconciliation; ambiguous recovery remains blocked. Automatic failover is not required to begin the bounded paper trial.

The overall [recovery progress estimate](GOAL_PROGRESS.md) remains separate from elapsed trial time and measured performance. No paid service, inference, economic backtest, training, real-money order or activation was performed for these observations.

User approved the [versioned EUR200 paper-primary design](superpowers/specs/2026-10-18-primary-wallet-protocol-design.md) and disabled local implementation. The strict resolver/risk foundation and selected financial path are implemented locally:86financial composition checks pass, with an overlapping strengthened selected native-fill/source check. Worker/supervisor/archive integration and a separate32-file disabled successor are now implemented. Independent review found and verified fixes for report risk identity and local-halt supervision. Full Windows discovery passed1,479tests with3platform skips andzeroResourceWarnings at253frozen sources; the current Linux engine remains unavailable. The current successor is `research/runs/primary-wallet-protocol-v1/candidate-reviewed`; the earlier inactive candidate is retained as superseded pre-review evidence. The original EUR100 candidate remains unchanged. [Financial-stage evidence](../research/runs/primary-wallet-protocol-v1/finance-verification.json), [implementation plan](superpowers/plans/2026-10-19-primary-wallet-protocol.md).

Current [full verification](../research/runs/primary-wallet-protocol-v1/full-release-verification.json) and [independent review](../research/runs/primary-wallet-protocol-v1/independent-review.json) establish the approved disabled primary revision, not installed archive integration or launch readiness. The [next dependency map](../research/runs/primary-wallet-protocol-v1/next-beta-dependencies.md) starts with the30-day combined footprint and permits retaining intact account history if it fits.
