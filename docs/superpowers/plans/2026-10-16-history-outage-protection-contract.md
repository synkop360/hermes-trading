# Counted protection during archive outages

Implementation detail for Tasks4/5/6 of the approved October16 archive integration plan. Local disabled implementation and invented correctness testing remain authorized. This document grants no deployment, activation, live retirement, paid usage or real-order permission.

## Verified gap

`research/runs/production-archive-integration-v1/outage-exit-probe.json` records the current native behavior. After the first common batch opens positions in all eight accounts, an invented receiver outage stops the next collection. The heartbeat degrades, but all eight positions remain open even though the next executable quote would trigger their original stops. Restoring the receiver lets the original second batch close all eight positions and reach the original two-cycle bound. Disposable roots are removed. No market/model/network request or strategy backtest occurs.

The completed batch's reservation has already been consumed. An ordinary unfrozen reservation is also intentionally not a recovery entitlement: missing original packets require explicit reconciliation. A fallback must not invent a new ordinary reservation or reinterpret a frozen original packet.

## Ruling

Implement an explicitly disabled second protocol with a durable protective authorization issued by the receiver before ordinary entries can be admitted. Keep the existing protocol and its immutable evidence intact. Bind the authorization to the original cohort, release, policy, identity, maximum cycle count and activation-derived deadline.

Reserve protective capacity **inside** the original maximum: ordinary admission must leave one unused common-batch slot. A protective packet consumes that slot and cannot increase the maximum or extend the deadline. This is resource admission for the common eight-account cohort; it does not change a strategy variable. With fewer than two slots, the new mode cannot admit ordinary entries.

This choice preserves bounded authorization. Continuing ordinary work without the receiver, borrowing a consumed reservation, or silently cancelling an ambiguous unfrozen reservation would break existing invariants.

## Protocol and runtime requirements

1. Use distinct version/purpose bindings. Existing version1 identities, head validation, transport and archives must never acquire an implicit protective allowance.
2. Persist a verified receiver authorization and local consumption state before admitting entries. A missing, foreign, expired, spent or regressed authorization cannot authorize collection. Integrity/authentication failures are distinct from transport unavailability.
3. On an eligible outage, retain the real worker owner and freeze at most one common protective packet. Block all entries; process retained positions with their original entry contracts, original fees and fresh executable quotes. Unknown/stale quotes leave positions pending. Do not claim protection after the original deadline or without remaining original capacity.
4. Preserve exact replay of an already frozen ordinary packet. Do not modify its input hash, decisions or account progress to make it protective. An ambiguous missing/unfrozen packet remains a reconciliation case.
5. The receiver must reconcile the bounded possible suffix: one original reserved ordinary completion followed by one authorized protective completion, or only a protective completion. Validate intermediate sequence/prefix/reservation evidence; never accept an arbitrary lifetime jump or an unreserved ordinary completion.
6. Persist protective consumption and stop new ordinary collection while acknowledgement is uncertain. Reconnection must acknowledge the original operations idempotently. Any renewed protective allowance must be a new receiver-authorized generation, within the remaining original count/deadline; a stale generation cannot reset consumption.
7. Retain all exit fees/cash effects in wallet equity and drawdown. Mark these exits as operational protection, excluding them from strategy-review cadence and completed-entry profitability qualification. Do not turn unavailable history or missing costs into zero.
8. Keep offline authority conditional on coherent owned local state. An older snapshot after post-cut activity, changed ownership/identity, lost original packets or an unverified external high-water floor cannot be repaired by assuming the grant is unused. Preserve the existing recovery stop where evidence is insufficient.

The source adapter must distinguish a durable grant cache from a current owner fence. After a process restart, a cached grant alone cannot prove that the source snapshot is current or the grant unspent. Obtain a fresh authenticated receiver reconciliation for that owner before admitting new collections, unless an independently verified recovery floor supplies equivalent evidence. An unavailable receiver at startup must not silently enable the cached-credit fallback. In a live owner, transport failure may trigger the bounded protective path; malformed/authentication/CAS failures remain integrity stops.

## Required evidence before launch

- Native all-eight-account outage after an entry and after a lost normal completion acknowledgement; protective exits use original contracts and fees, count once, and reconcile once without another market collection.
- Crash before/after freezing and after individual account writes; original packets replay without recollection or fresh model dispatch.
- Refusal on changed/expired/spent/absent grants, stale/unknown quotes, original count/deadline exhaustion, malformed authenticated heads and source rollback. No automatic cancellation of ambiguous reservations.
- Normal healthy runs remain bounded; adaptive/control accounts share the same outage admission. Operational exits retain wallet P&L but cannot inflate strategy evidence.
- Exact-current Windows regression, Linux checks where available, updated long-history receiver/export measurements, total footprint/peak-space evidence and the final independent whole-integration review. Earlier version1 capacity measurements do not qualify the new protocol.

This is the next implementation contract, not evidence that outage protection already works. The current candidate remains disabled and not launch-ready. The native probe demonstrates reconnection recovery, not protective execution while the receiver is unavailable.
