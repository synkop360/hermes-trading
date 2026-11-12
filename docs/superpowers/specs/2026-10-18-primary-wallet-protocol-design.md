# Versioned primary-wallet protocol

**Status:** User approved the design and disabled local implementation. Authorization recorded October19 in `research/runs/primary-wallet-protocol-v1/authorization.json`. Disabled local implementation verified October19:1,482Windows tests,3platform skips and independent review; no activation or launch-readiness claim.

**Purpose:** Let the controlled paper experiment explicitly preregister a primary virtual wallet that can admit quantities under its existing risk/cost guards. Keep all eight standalone adaptive/control accounts at EUR50/100/150/200. Restore the intended five-new-close LLM learning loop without increasing risk caps or selecting outcomes after observing P&L.

## Evidence and proposed choice

The October18 14:51:11UTC native sizing observation rejected all three assets for EUR50/100/150; EUR200 ETH alone admitted a quantity. It used zero accrued operating costs and evaluated no entry signal. It establishes neither a profitable minimum nor sustained opportunity. [Retained evidence](../../../research/runs/beta-feasibility-2026-10-18/native-admission/verification.json).

The current EUR100 primary is provisional, but its implementation is embedded in execution, supervision, risk, statistics and recovery. A manifest-only change would leave those systems operating on EUR100. The proposed separate disabled revision selects **EUR200** as primary before any prospective experiment outcomes exist. All wallet scenarios remain present; no real deposit is proposed. Fresh costs, venue observations and signal feasibility still require launch validation.

## Contract and compatibility

Add one authoritative resolver for a versioned primary-selection contract. Its new contract contains a strict integer wallet in `{50,100,150,200}`, a fixed purpose and protocol version, and prespecified selection rationale. The manifest, reporting identity, supervisor review wallet and virtual-account launch bindings must agree. Reject booleans, floats, strings, conflicting fields and unsupported versions before collection or cost/model work.

The new manifest adds this exact selection object while retaining its existing container schema:

```json
{
  "schema_version": 1,
  "purpose": "paired_primary_selection_v1",
  "wallet_eur": 200,
  "rationale_sha256": "<SHA256 of the retained pre-inception selection rationale>"
}
```

The object is stored as `primary_selection`; `rationale_sha256` must be a lowercase64hex digest, not the placeholder above. A new reporting protocol uses schema2, the selected capital and the selection object's canonical SHA256, while copying every existing method parameter. New risk state explicitly binds the selected capital and selection hash under its new version. Legacy schema1 risk/report state remains EUR100-only. The original archive identity still has seven fields: its original cohort digest already binds the new manifest and prevents reuse of an earlier selection.

The existing manifest container and legacy EUR100 reporting-method constant retain their meanings. Legacy manifests without the new contract keep their historical EUR100 interpretation, including existing qualification restrictions. A non-100 legacy label must not silently opt into the new protocol. New reporting identities explicitly bind the selected primary and protocol version while preserving the original method parameters. Do not mutate a global legacy constant into a different method.

The resolver supplies selected adaptive/control profile paths, names and declared capital. Every primary-sensitive consumer uses this result. Do not move an existing experiment's publication database, migrate active strategy ownership between wallets, reinterpret archived packets or change selection after inception. A new selection requires a fresh experiment identity, release, common activation and inception receipt.

## Resulting behavior

- The chosen primary pair supplies the common journal/publication database, owner, heartbeat, source strategy epochs and adaptive supervision mirror. All four adaptive wallets continue to receive the same causal global strategy changes on common market inputs.
- LLM review occurs after five unique newly closed entries in the selected primary adaptive account. Preserve latest25 prompt context separately from all unresolved IDs, valid no-change decisions, one changed global variable, original attempt/cost controls and complete archived versions/outcomes.
- The same 3% drawdown rule applies. A breach in either selected primary arm halts all entries/steering; a nonprimary breach halts that wallet pair. Existing stops remain irreversible and protective processing continues. Selecting EUR200 changes which pair supplies the global trigger; it does not raise any threshold.
- Each virtual scenario retains its full standalone operating-cost allocation. No costs or capital are shared away to make a small wallet appear profitable. Trading fees, slippage, max order size, exposures, position count, ATR/target rules and doubled-cost reserve remain unchanged.
- Reports select the chosen pair's original opening receipt, canonical orders/settlements, net equity and stress capital. Initial drawdown peaks use declared capital. Mixing EUR100 data or risk state into a EUR200 report fails.
- Preserve the 30-day operational checkpoint, at least180-day assessment, two90-day blocks, original-lineage requirements, fixed control, cost stress, statistical assumptions, seed/resampling settings and financial qualification flags. A primary choice is not a performance result.
- Archive identities, captured-cut certificates, account/controller links, witness source views and recovery reads resolve the same selection. Legacy EUR100 evidence remains tied to its original protocol/release and is never rewritten.

## Implementation boundary

Add a small shared primary-selection module and use it in `paired_paper`, `paired_worker`, `paired_inception`, `paired_risk`, `paired_source_validation`, `paired_performance_report`, `paired_statistics`, `supervisor_launch`, `archive_certificate`, `archive_coordinator`, `archive_witness`, `archive_protective_source` and `archive_worker_boundary`. Update new candidate builders/launch drafts through the same contract. Preserve prior drafts and evidence as immutable history.

Prepare a **new disabled candidate revision**, rather than changing `current-venue-execution-v1/candidate` or `approved-cost-reserve-v1`. Bind the chosen primary, reporting method, supervisor paths, release and selection rationale together. Experiment/account/owner activation remains unassigned until a separately reviewed cutover. No network listener, deployment, paid call, backtest, training or real-money operation is included.

## Verification

1. Exercise each allowed primary with all eight accounts; reject contradictory types/contracts before collection. Retain historical EUR100 fixtures and protocol behavior.
2. Verify either chosen primary arm globally halts at exactly3%, other pairs halt locally, and original peaks/halts survive restart. Exercise selections below and above EUR100 to expose hidden capital assumptions.
3. Verify report/opening/stress/settlement inputs use selected capital and keys; reject cross-wallet data and mismatched risk-state primary.
4. Verify one selected-primary review/publication propagates one common causal strategy version to all adaptive scenarios, with complete unchanged standalone usage costs.
5. Reject changing primary after inception, a pending batch, lifecycle or witness identity. Replay original pending packets without collecting a new quote.
6. Verify selected-primary full-cohort capture, separate supervisor, witness and restored cut agree, while original EUR100 evidence/protocol remains unchanged. Record original-verifier compatibility restrictions explicitly.
7. Freeze runtime/tests, run the relevant correctness regression and full-release checks before calling the deployment candidate ready. Use independent review and retained source hashes. Synthetic checks do not demonstrate trading returns.

## Authorization boundary

The user's original request asks for approval before heavy changes. This proposal changes several coupled runtime contracts and which wallet's losses trigger the global halt, so implementation requires that approval. Existing approved archive correctness work can continue independently. Approval here covers the written design and disabled local implementation/synthetic verification only; activation/deployment, extra paid services/model usage, economic backtests/training and real-money trading remain excluded.
