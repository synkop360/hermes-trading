# Intrabar prospective position protection

Reproduce the remaining launch defect: a prospective open position is skipped when the source closed candle was processed at entry or an earlier hold, so a later received bid within that bar cannot trigger a stop/target. Preserve historical qualification behavior and original closed-bar entry/ATR/bar-count semantics.

For prospective open positions, persist a stable position-and-decision-time observation identity, independent of whether an exit is pending. Align decision identifiers with that observation for holds and exits. Atomically reserve the original closed-bar identity in the existing transaction so flattening cannot permit another entry on the same source bar. No additional strategy writer, resting-order assumption, fake bar, imputed quote or duplicate fill. Pending priorities, original entry contracts, fee/lineage conservation and projection retry remain unchanged. An unavailable quote must not derive a fresh price-triggered signal from an already consumed historical close.

Test first: stop and target before a new closed bar; repeated hold/stop and reopened repository idempotency; no same-bar re-entry after closing on a new bar; invalid quote cannot invent a stale stop; partial remainder still protected; reservation failure rolls back all financial writes. Verify focused/full suites and scoped review. This is execution correctness, not a backtest or evidence of winning returns. No model call, release, activation or trade submission.

## Verified checkpoint

Implemented stable position-observation identities and atomic original-bar reservation. Initial915full tests passed; review found an exact entry-replay history gap. Added a failing reopen/replay regression and guard; final916tests pass in85.461s,22prospective checks, scoped follow-up clear. Evidence research/runs/intrabar-protection-v1. No runtime deployment or model/financial action. Full-state capacity must include new quote observations; collector resilience remains next.
