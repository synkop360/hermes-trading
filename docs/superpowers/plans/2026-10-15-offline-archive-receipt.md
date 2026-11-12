# Offline archive acknowledgement prototype

Scope: previously approved archive design and synthetic offline prototype only.

Railway cannot read the PC archive directly. Test a bounded, purpose-bound acknowledgement using existing command HMAC authentication and durable nonce storage. The caller must independently pin the experiment, runtime, shared cohort, archive digest, retirement ordinal/prefix digest, observed head and checkpoint revision. Compare the entire canonical request before consuming the nonce. Generate ephemeral test keys only. No transport, key installation, deletion or production integration.

- [x] Reject malformed, oversized, noncanonical, incorrectly bound or stale receipts before nonce consumption.
- [x] Verify authenticity and replay rejection across receiver restart without archive access.
- [x] Bind a receipt to a verified retained synthetic eight-wallet archive and specific prefix.
- [x] Review, record evidence and describe acknowledgement-loss/retirement transaction limitations.

This proves authentication of a trusted publisher's claim, not independent durability or atomic retirement. Existing nonce consumption commits separately from retirement; a production protocol must persist acknowledgement/operation state and handle acknowledgement loss before it can authorize deletion. Never invoke retirement from this prototype.
