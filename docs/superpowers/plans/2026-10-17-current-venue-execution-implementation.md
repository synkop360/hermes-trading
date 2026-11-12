# Current Venue Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent new prospective paper fills and qualification from assuming obsolete venue minima, precision or market availability.

**Architecture:** Carry dated purpose-bound venue facts in each new common input, separately from original strategy/fee contracts. Validate every prospective fill and liquidation claim against those facts. Preserve deterministic replay and historical evidence.

**Tech stack:** Python3.11+, Decimal, existing httpx/SQLite/YAML, unittest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-10-17-current-venue-execution-design.md`

## Global constraints

- User approved the design and disabled implementation; `research/runs/current-venue-execution-v1/authorization.json`. Existing inline execution is preserved; no further step approval needed.
- Work in the existing dirty feat/live-hardening candidate, fingerprinting before changes. No commits, resets, worktree substitution, deployment, activation, live retirement, provider inference, paid service, backtest/training or real order.
- Preserve all eight EUR50/100/150/200 profiles, original strategy/fee lineage, one-variable maximum, five-close cadence and original worker count/deadline.
- Original source/historical draft artifacts remain immutable. Reconcile a new disabled candidate; never rewrite the October13/approved-cost-reserve evidence.
- Default prospective execution must fail closed without valid venue facts. Do not introduce an optional guard that the new trial can bypass. Legacy historical inputs remain auditable under their original bytes; absence of this evidence cannot qualify execution under the new protocol.

## Review focus

- A changed minimum or precision after entry: the original quantity remains open/pending, with no fabricated order or remainder.
- Good quote plus stale/missing/future/wrong-symbol venue record: no fill and no profitability qualification.
- Public metadata with no private permission or fee arrays: preserve account-access/cost gates, never infer authorization or zero fees.
- A pending frozen packet after crash: replay exact original rules/time/source hash without recollection or duplicate records.
- Marked dust: preserve its marked value and report unavailable liquidation rather than silently treating the wallet as flat.

## Task1: Purpose-bound dated venue constraints

Files: create `hermes_trading/venue_constraints.py`, `tests/test_venue_constraints.py`.

Interfaces: `constraint_record(pair, *, asset, received_at, source_url, country_code='FR') -> dict`; `parse_venue_constraints(record, *, asset, at, quote_currency='EUR', country_code='FR', max_age_seconds=300) -> VenueConstraints`. The frozen dataclass exposes `minimum_quantity`, `minimum_quote`, `quantity_decimals`, `quantize_entry(quantity: Decimal) -> Decimal`, and `validate_order(quantity: Decimal, price: Decimal) -> None`; failures raise `VenueConstraintsError`.

Record fields are exact: schema_version1, purpose`paper_executable_venue_v1`, exchange`kraken`, asset, quote_currency, country_code, received_at, source_url, source_sha256, source_pair. The digest binds canonical JSON of the original pair record, not claimed original HTTP wire bytes. Retain the complete bounded original pair record; derive limits from it rather than trust duplicate normalized numbers. Record ceiling8KiB. Require an HTTPS api.kraken.com/0/public/AssetPairs source, matching country/pair scope, spot unit lot/multiplier1, canonical EUR symbol, online status, strictly positive finite decimal-string minima and integer0..18quantity decimals. Reject future/age>=300seconds by default, floats/bools, wrong scope/purpose/digest and unsupported forms. Metadata is an observed assumption, not a private permission certificate.

- [ ] Write literal table-driven checks: ETH0.0015fails min0.002;0.002atEUR2000passes; quantity precision cannot round up; modified source/digest/scope and freshness boundaries reject.
- [ ] Run `python -B -m unittest test_venue_constraints -v` with PYTHONPATH=tests and watch missing behavior fail.
- [ ] Implement the pure bounded record/validator/dataclass, without networking or strategy mutation.
- [ ] Run focused checks and record pins/results; do not claim the execution gap fixed yet.

## Task2: Prospective execution enforcement

Files: modify `hermes_trading/market_data.py`, `hermes_trading/positions.py`; create `tests/venue_fixtures.py`, `tests/test_current_venue_execution.py`; update existing prospective fixtures in `tests/test_prospective_execution.py`.

Interfaces: add optional serialized `MarketSnapshot.venue_rules` for historical decoding. Every prospective or forced-risk fill in the current runtime calls Task1's parser before execution; optional field storage is not optional enforcement. Historical nonprospective audit remains separate. `PositionEngine` retains original economic SimulationSettings/entry-contract triggers and uses validated venue facts only for executable constraints.

- [ ] Add red tests for ordinary/paired_archive_stop/paired_storage_stop exits: ETH0.0015with new min0.002remains open, pending reason retained, orders/fills unchanged. Add missing/stale/future/status/precision and current-valid original-version/nonzero-fee cases.
- [ ] Add entry/partial tests: round entries down only; invalid partial leg or remainder produces no fill and preserves a pending trigger, including existing full-stop precedence.
- [ ] Implement guards for entry, partial and full exits; no upward rounding, implicit quantity change, fee substitution or strategy version change.
- [ ] Reopen SQLite and replay identical observations; assert no added order/fill/close and pending trigger intact. Run current-venue, prospective, positions and entry-contract checks.

## Task3: Common collection, new packet version and native eight-account path

Files: create `hermes_trading/venue_collection.py`; modify `hermes_trading/paired_worker.py`, `hermes_trading/paired_paper.py`; update `tests/test_paired_worker.py` common fixture and add `tests/test_current_venue_worker.py`.

Interfaces: `PublicVenueClient.fetch_many(assets, *, country_code='FR') -> dict[asset, record-or-explicit-failure]`, using one bounded public AssetPairs HTTP request per common collection, a64KiBbody limit and finite existing remaining collection deadline. Client uses public HTTPS only and closes its resources. `PublicQuoteCollector.collect` attaches Task1records to the common snapshots once, with actual receipt times. Tests inject a fake transport; do not request real quotes/OHLC for verification.

New paired packets use schema_version2 and bind the venue fields in their existing immutable input hash. Decode/audit legacy packets explicitly; do not relabel them. New native collection/replay must not manufacture fallback rules from strategy YAML. Halted liquidation events carry the original snapshot's venue evidence along with its quote. Existing archive/storage/protective grants, cycle/deadline, pending-packet no-recollection and one-source/all-eight rules stay intact.

- [ ] Add failing common-source, missing-peer metadata, all-eight ordinary/protective, partial-cohort replay and original fee/cash/basis checks.
- [ ] Implement bounded public metadata collection and new snapshot/packet propagation; preserve exact pending inputs and reject incompatible new-release old pending work.
- [ ] Upgrade invented prospective native fixtures to include explicit dated source records rather than relaxing production enforcement.
- [ ] Run current-venue worker, paired worker, archive protective worker and packet-storage tests; count new payload bytes for later capacity validation.

## Task4: Valuation, source validation and cost stress

Files: modify `hermes_trading/wallet_observer.py`, `hermes_trading/paired_source_validation.py`, `hermes_trading/paired_cost_stress.py`, `hermes_trading/paired_performance_report.py`; add `tests/test_current_venue_reporting.py` and update relevant invented source/horizon fixtures.

Interfaces: source validation consumes the same Task1record from the original schema2packet at its original decision time. Every filled entry/partial/full leg must satisfy that record; old schema1evidence can be audited but cannot pass the corrected protocol's execution qualification. `value_current_wallet` reports marked inventory separately from liquidation completeness; invalid/unexecutable current quantity cannot produce a complete net-liquidation or flat-wallet claim.

- [ ] Write red cases for internally consistent but venue-invalid FILLEDrows, marked dust, absent rules, unsupported status/precision and original partial lineage under2xcosts.
- [ ] Implement independent constraint verification in source/cost readers and liquidation completeness; do not rebuild orders or write down unliquidatable inventory to zero.
- [ ] Run current-venue reporting, wallet observer/metrics, paired source validation, cost stress, performance report and horizon checks. Preserve old immutable research runs.

## Task5: Disabled candidate reconciliation and archive continuity

Files: create `hermes_trading/venue_candidate.py`, `tests/test_venue_candidate.py`; add coverage to existing archive/account recovery tests without rewriting saved prototypes.

Interface: `prepare_draft_revision(source_root, target, *, venue_receipt, enabled=False) -> dict`. Require a new target and exactly eight unactivated approved source profiles. Produce a bounded disabled revision with new source hashes and metadata provenance, changing only venue minima/precision fields from validated facts; prove stripped economic strategies/fees/goals and original sources unchanged. New manifest records `execution_venue_protocol='dated_rules_v1'`, draft/unassigned/nullactivation. No launch selection or activation. Activation-time validation must obtain fresh matching facts; a saved research receipt is not perpetual freshness.

- [ ] Test24minimum corrections from the saved October17 facts, all-eight preservation, no source changes, no strategy/fee drift and refusal of active/partial/foreign targets.
- [ ] Implement the disabled revision builder and strict activation prerequisite; do not alter original approved-cost-reserve files.
- [ ] Test frozen schema2packet/source/archive/restore continuity, partial pending exits, cost books, reviews and payload/peak bounds with the new fields. No synthetic performance result is economic evidence.

## Task6: Integrated verification and handoff

- [ ] Resolve explicit fixture cleanup warnings using their diagnosed owners; verify forced-GC cleanup with no hidden retained DB handles.
- [ ] Freeze all current runtime/test sources, run affected checks and final full Windows discovery; inspect every skip/warning/failure and run Linux only if the existing environment is available.
- [ ] Update the AST graph without paid labeling, preserve15original source fingerprints and old gap evidence, reconcile all-eight current-source/candidate bindings.
- [ ] Complete the single fresh-context whole-release review at the integration boundary; fix substantive findings with regression evidence. This plan does not replace unfinished archive quota/nonce/export/independent-floor work.
- [ ] Publish a disabled, reviewable candidate/readiness result and update both progress bars; request deployment/activation only after all gates are concrete. No profitable-wallet or winning-strategy claim without the original forward protocol.

Plan self-review: all approved spec areas map to Tasks1–6; exact input/engine/reader interfaces share one parser, and old evidence is never upgraded by label. Runtime execution authority requires Tasks2–5, not merely the pure Task1validator. Inline execution and scope are already explicitly approved; proceed without another permission round trip.


## Verification checkpoint — 2026-10-18

Tasks1–5 are implemented in the disabled candidate. Original26draft source files and15original prototype sources remain unchanged. Stage evidence is under `research/runs/current-venue-execution-v1`; overlapping staged checks are not independent test totals.

- Frozen233source affected group:375checks,374pass/1Windowsplatformskip,zeroResourceWarnings; explicit cleanup regressions cover all six reused async-fixture owners.
- Fresh integration review found activation audit blocking legitimate adaptation and history failure dropping usable protective quotes. Both were reproduced and corrected.57focused checks passed; follow-up review is clear in that scope.
- A final native collector exchange binding prevents EXCHANGE_ID from selecting prices from another venue while applying Kraken limits.7focused checks passed; narrow review is clear.
- Final234source freeze: `final-source-sha256.json`. Full Windows discovery is running; it is not a passing result until its terminal evidence is inspected.
- AST graph updated without paid labeling. Linux, installed cross-host integration, combined nonce/storage/export/recovery capacity and whole-release launch qualification remain open.

No deployment, activation, paid inference, economic backtest/training or real order was performed. Both progress bars remain40percent estimated, with trial0days and qualification0days.
