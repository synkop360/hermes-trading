# Snapshot reliability: execution ledger

The owner authorized deployment and immediate supervisor-halt correction. The repair is deployed and native acceptance was recorded at 2026-10-29T12:02:03.390395+00:00. Eight consecutive scheduled checks completed without failures; entry admission is unblocked, both error counters are zero and all eight wallet reports match their settled batch.

## Reproduced causes and verification

The original read-only samples failed at database-lock onset after 9.709–10.000 seconds. A deterministic native eight-wallet fixture also reproduced a completed wallet report appearing before its matching heartbeat. Both regression tests were observed failing before the correction.

Candidate c7f816d changes only supervisor_sync.py: twenty-second bounded complete recollection for numeric SQLite BUSY or the specific report/heartbeat mismatch. Every acceptance check remains mandatory; persistent invalidity fails closed. The two post-deployment lock-onset probes validated in 11.054 and 11.756 seconds. The 180-second observation window admitted two samples, not three.

Windows affected-package checks ran 225 tests with one platform skip. Linux affected checks ran 225 with no skips. The initial full-suite attempt on a read-only Windows mount stopped after 1,200 seconds and included five fixture-write rejections. A verified writable Linux tmpfs copy subsequently passed full discovery: 1,749 tests, zero failures or errors, three Windows-specific skips. No product change was needed for that rerun. The frozen 675-file inventory and 129-module runtime hashes remain unchanged; all unsuccessful attempts are retained.

## Recovery and deployment

Fresh October28 preflight found the supervisor latch had recurred. The owner's explicit October29 instruction authorized checked acknowledgment 5ffdc443b5a7419fbfcb82aa2d5aa091, completed at 06:50:34 UTC. Only the two documented supervisor-latch fields changed. Fresh preflight confirmed that entries were unblocked and the original experiment identities were intact.

Deployment ccc1e87a-de24-4876-98b2-57aa3baa2945 runs on instance 82344b11-fe45-446c-b7e5-deedce294d0e. The predecessor's exit was confirmed by Railway's instance gateway. Only the paper supervisor and backup schedules were paused; the legacy watcher remained paused.

The closed worker copy contained 533 files and its 265,799,680-byte transfer was verified on E:. SQLite itself reconciled one verified cold zero-header journal while preserving logical data and schema. The closed PC copy contained 19 files. Both host-bound intents and recovery copies were verified on E: before changing release bindings.

The PC review database had grown to 5,009,408 bytes. Its operator-only copy bound was adjusted to 8 MiB per file and 16 MiB total, with a 17 MiB wire bound, exact member allowlist and SHA256 verification. Runtime quotas and risk limits were unchanged.

Transition 973d396433aa6d0feeb986b604fd55a641245e006ab0d9933e6e0b3c2de396ec completed on both hosts. All six bindings, 129 PC runtime hashes and unchanged launchers were verified. The exact original safety object, including the cleared halt, was preserved.

## Startup and observed operation

Readiness was released at 07:08:36 UTC. Free-access metadata renewed at 07:08:41 without inference. Stale adaptive cost-source coverage during the operator pause initially produced degraded observations. The existing usage-import path completed at 11:43:51 UTC; all wallet values and worker health then recovered. Original schedules resumed at 11:44:51 UTC. This extended pause and all unknown observations remain recorded; no history was replayed and no clock reset.

The first normal supervisor cycle completed at 11:46:50 UTC. Eight consecutive successful cycles were verified through 12:00:52 UTC. At 12:00:41 UTC the new hourly evaluation recorded entry_condition_not_met for BTC/EUR, ETH/EUR and XRP/EUR in both primary arms, with no operational entry block. No orders or positions had been created at acceptance, and LLM reviews remained not due.

The daily backup task is enabled. A manual verification returned already_completed/off_host_verified for the October29 00:00:35 UTC backup. This is not a new post-cutover capture; the fresh closed cutover copy is separate evidence. Later natural Discord summaries have confirmed delivery markers; no remote message IDs are retained.

The original October23 opening, cost clock, deadline, all eight wallets, strategies, fees and risk limits remain intact. This is operational evidence for the recorded window, not a guarantee of zero future errors, a winning strategy or a minimum profitable wallet. See live-cutover/OPERATIONAL_ACCEPTANCE.json for exact native evidence.
