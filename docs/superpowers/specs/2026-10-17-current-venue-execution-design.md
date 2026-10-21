# Current venue constraints for paper execution

Status: user approved the design and disabled implementation on October17 (`research/runs/current-venue-execution-v1/authorization.json`). Disabled local work and invented correctness checks only. No deployment, trading activation, paid provider/service, backtest/training or real order is authorized.

## Purpose and evidence

The recovery goal requires credible adaptive-versus-fixed paper P&L for eight EUR50/100/150/200 wallets, with original entry contracts, costs and five-close review eligibility preserved. A simulated exit must not claim liquidity that the current observed venue rules would reject.

October17 FRpublic metadata raises BTC/EUR quantity minimum0.00005to0.00006, ETH/EUR0.001to0.002 and XRP/EUR1.65to4. Exact saved source and24stale draft fields: `research/runs/venue-refresh-2026-10-17/verification.json`. Public metadata does not authenticate account eligibility or actual fees.

The one-shot invented reproduction `research/runs/venue-refresh-2026-10-17/exit-minimum-gap-v1.json` proves the current engine closes an ETHposition of0.0015under its original0.001minimum even when the supplied current strategy mirrors0.002. Both ordinary stop-loss and paired_archive_stop produce one FILLEDexit; fees/version remain original and restart replay adds no duplicate. It exercises the shared engine, not all eight worker paths end to end. The actual collector currently has no independent dated venue-rule field.

The current full Windows baseline passes1335checks with3platform skips, but emits56implicit temporary-directory cleanup warnings and does not cover this missing execution constraint. Passing that suite is not proof the gap is fixed.

## Options

Recommended: carry one separately validated dated venue-constraint record per asset in the frozen common input, and apply it before every new paper fill. It preserves deterministic replay, common adaptive/control observations and entry-rule provenance.

Updating strategy YAML alone is insufficient: an existing position resolves its archived strategy and therefore discards newer simulation minima. Venue facts also must not become a discretionary LLM parameter or create artificial strategy versions.

Fetching rules independently at each arm/fill would make paired outcomes depend on different observations and make recovery nondeterministic. Do not use that approach.

## Data and compatibility boundary

Add a typed, purpose/version-bound venue-constraint record to new frozen common market inputs. It binds exchange, canonical symbol, quote currency, queried country, observed/received time, public source identity/digest, market status, positive finite minimum quantity/quote and supported quantity precision. Capture once for the common collection, shared by all eight accounts. Validate matching symbol/currency/source, nonfuture observation and the original finite freshness policy before admitting a new fill. Unavailable, malformed or stale metadata is explicit, never replaced by an old strategy minimum or zero.

The observed metadata is a paper execution assumption; it cannot guarantee that a real venue will accept a later order or establish private account permission. Public API responses have receipt times, not an independently proved time at which a rule changed.

New-input protocol/version and activation policy must make required constraints explicit. Historical frozen inputs retain their original bytes and semantics for audit/recovery; do not silently attach today's rules or recollect a frozen packet. A historical legacy fill without this evidence cannot become proof of execution under the new protocol. The proposed trial remains unactivated, so its new baseline can require the corrected protocol before first collection.

## Execution behavior

Resolve the original position's strategy triggers, sizing lineage, version, fees and slippage exactly as before. Validate execution separately against the current input's venue constraints. The LLM cannot relax those facts; five-close cadence and at most one global strategy-variable change remain unchanged.

For entries, retain the economic risk/cash/exposure limits. Round down only to the current supported precision; reject an order that then fails quantity/quote minima. Never increase quantity to force a fill.

For full exits, validate the original remaining quantity and executable bid/fee assumptions. If current minimum, precision, market status or metadata validation fails, write no order/fill/closed position, retain original quantity and trigger, and leave the exit pending. Do not silently round a full exit into an untracked remainder.

For partial exits, validate both proposed leg and retained remainder against current quantity/quote/precision constraints. An invalid leg/remainder produces no fill and retains a pending exit. Existing full protective-stop precedence remains intact. Operational archive/storage/recovery exits use the same guard; they retain P&L but remain excluded from qualification and ordinary five-close review eligibility.

Missing history may still allow an exit from a valid retained original entry contract and fresh quote/venue rules. Neither history failure nor an outage authorizes inventing executable liquidity.

## Reporting and recovery

Preserve original financial rows, fill IDs, cash/basis, partial lineage, cost books and source hashes. Reopening or replaying the same frozen input must add no duplicate record and must not recollect prices/rules. A pending exit can be reconsidered only on a subsequent new valid common input, with the original trigger and entry contract preserved.

Continue showing marked open-position value separately. If current constraints prevent liquidation, report that liquidation is unavailable/incomplete and block qualification; do not report a flat wallet or silently write the inventory down to zero. Source validation and fixed-order cost stress must verify the constraints used for every new filled leg, not infer them from a current strategy file.

Archive/restore must retain the original constraint evidence in the immutable input and preserve it across all eight accounts. New fields/packet sizes must be included in final storage/peak validation. No independent recovery floor or complete capacity claim follows from this correction alone.

## Affected boundaries and acceptance

Expected existing boundaries: public collection/market snapshot serialization, frozen paired input and replay, PositionEngine prospective ordinary/protective/partial execution, pending-exit persistence, source validation/net-liquidation reporting and cost-stress readers. Avoid unrelated strategy/model changes.

Required invented correctness checks:

1. Reproduce the known quantity-minimum increase; ordinary and operational exits remain open/pending with no new financial rows.
2. All eight accounts consume the same dated rules; a market-status failure, missing/stale/future/wrong-symbol record blocks fills.
3. New entries and partial legs/remainders respect current minimums and precision without rounding up.
4. An executable exit keeps original strategy version, trigger and nonzero entry/exit cost assumptions after a strategy update.
5. Restart and uncertain-reply replay preserve original packet hashes, pending trigger, quantities, both cost books and no duplicate fills or review IDs.
6. Marked but unliquidatable inventory cannot pass profitability qualification or be reported as flat.
7. Legacy frozen evidence remains unchanged; new trial activation requires corrected-input semantics and freshly reconciled candidate minima.
8. Relevant affected checks and final Windows/Linux-if-available verification, fixture cleanup, source fingerprints and whole-release review remain required.

The complete disabled correction and synthetic verification are approved. Existing archive/storage/nonce work remains approved independently. Implementation uses the existing inline workflow and preserves historical research; no commit/push or live action is implied. Do not ask again for successive local steps within this approved design.
