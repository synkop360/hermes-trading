# Partial public market-data failure handling

Continue approved release recovery. Preserve one common batch with an explicit snapshot for every configured asset. Fetch/missing-key/malformed-price or closed-history errors become unavailable per-asset snapshots with no candle or imputed price. A whole client/fetch failure returns an unavailable batch; asyncio cancellation still propagates. Preserve successful assets' normalized closed history and quotes. Do not echo exception text that might contain provider request details.

Existing common-source validation blocks all new entries on any unusable asset. Known received quotes still permit protective processing on available assets; unavailable assets retain recorded inventory and unknown liquidation equity. A later valid batch may recover health without restarting the finite worker. Frozen pending batches replay the original unavailable/available facts without recollection. No schema relaxation, invented fills, old-price substitution, paid/model call or strategy change.

Tests first: one failed/missing/malformed asset does not discard a valid peer; whole transport failure produces a complete unavailable set; cancellation propagates; an actual two-asset running worker exits the quoted asset, retains unavailable inventory, blocks entries and reports degraded, then processes recovery. Run focused/full suite and independent review. No deployment or experiment activation.

Verified locally:25worker tests and920full tests pass; scoped review clear. Evidence research/runs/partial-market-data-v1. No deployment, activation, inference call or real order.
