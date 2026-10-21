# Lossless journal storage plan

Continue the authorized capacity recovery without changing strategy, cadence, sampling or finances. Apply the verified bounded lossless codec to new wallet observations, cost observations, paired account checkpoints and settled measurements. Preserve old plaintext reads; do not rewrite/delete historical rows. Use existing transactions and original timestamp/source bindings for immutable repeatability and crash recovery. No new paid capacity or operational activation.

Add a record-only codec entry point that compresses dictionaries without packet-specific reference extraction. Reuse stream/size/hash verification, and keep packet codec unchanged. Wallet reads verify row timestamp agrees with primary key; cost reads preserve original source_hash/frozen-timestamp semantics. Checkpoint/settlement reads remain compatible with old JSON; partial-batch resumes must produce identical account observations and closes.

Tests first: new compressed wallet rows, mixed old/new reopen, corruption and primary-key disagreement, immutable retries, cost producer frozen replay, compressed paired checkpoints and settlement crash/replay. Run focused/full verification and independent review, update AST graph, preserve evidence. Measure representative journal/result storage separately from return evaluation; capacity admission and sustained total-state feasibility remain required before launch.

## Verified checkpoint

Implemented with5journal/17wallet/13cost/22paired checks and851full-suite tests passing in98.234s. Independent scoped read-only review clear. Canonical-type immutability regression fixed; existing background-server test now waits for its repository-owning thread before cleanup, matching unchanged production shutdown. Evidence research/runs/journal-storage-v1. AST graph updated without model use.

Persistence-only fixture benchmark measures observation payload at about49.46%ofraw, and checkpoint/settlement/one-cost-stream payload at11.06%. Initialization/page effects and omitted source/event/trade history prevent a full-volume certification. Continue sustained growth/admission/recovery-margin work and cost-feed/deployment gates; no deployment, paid capacity or actual experiment started.
