# Finish retained paper volume maintenance

Goal: keep the existing500MB allocation, original clocks/counters and all eight
wallets. User authorized lossless historical condensation, fewer wallets only
if needed, and the exact private supervisor checkpoint transfer.

Actual deployment7d8019e8 installed the original native source transitions and
relocated supervision successfully. The30temporary transport values were cleared
without redeployment. Fresh Nous login remains a user action. Entry admission is
still blocked by storage and stale supervision; do not claim a functional beta.

Approach: avoid changing the active wallet protocol. Deduplicate only unreferenced
identical payload archives left by failed bootstrap retries, retaining every
referenced keeper and alias evidence. Archive the unused root legacy trading.db
with an exact gzip roundtrip; all eight current wallet databases remain active
and unchanged. SQLite VACUUM showed essentially no free pages, so cannot provide
meaningful savings. Reducing wallet count would require explicit active/retired
protocol and historical-reader support; do not fabricate current observations.

- [x] Add referenced-archive-safe deduplication to completed owned installation.
  Replay aliases safely; retain prior canonical keepers when new copies appear.
- [x] Add source-pinned legacy database archival mode, disabled by default,
  requiring paused worker, true owner lease, eight declared independent active
  databases, no aliases, no sidecars and a closed exclusive legacy SQLite check.
  Verify exact compressed bytes and durable receipt before explicit retirement.
- [x] Give the new storage pause a bounded separate recovery audit identity.
  Preserve old recovery evidence and every original deadline/counter. Completed
  recovery never automatically clears a later pause.
- [x] Add startup maintenance orchestration. On failure, retain the worker and
  place only an owned supervisor maintenance marker so failed cycles are not
  consumed while space is unresolved. Preserve other operator maintenance.
- [ ] Require full Linux CI and publish exact source. Stage maintenance entrypoint
  and pinned commit on existing service/volume, never print raw private data.
- [ ] Verify actual archive bytes reclaimed, fresh storage admission, model access,
  supervisor receipts and paper entry gates. Relay a fresh real Nous URL if needed.

Independent review reports no blocking findings; focused real SQLite, alias,
sidecar, restart, referenced-deduplication and native repeated-pause tests pass.
