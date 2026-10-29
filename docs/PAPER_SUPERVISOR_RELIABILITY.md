# Paper supervisor reliability: deployed October 29

Release `c7f816d` is deployed to Railway and the PC supervisor. At 2026-10-29T12:02:03.390395+00:00, the worker was healthy, entries were enabled, both error counters were zero and all eight wallet reports matched their settled batch. 8 consecutive scheduled supervisor runs completed with no failures after schedules resumed.

The repair addresses two reproduced timing faults: ordinary database leases lasted longer than the previous ten-second retry window, and a new wallet report could appear just before its matching heartbeat. The supervisor now recollects the complete snapshot within a twenty-second budget for these specific conditions. All health, source, identity, account, risk and publication validation remains mandatory. Persistent invalid state still fails closed within the existing thirty-second process boundary.

Two read-only probes beginning at real database-lock onset now passed in 11.054 and 11.756 seconds; all three predecessor probes had failed at 9.709–10.000 seconds. A native synthetic eight-wallet regression also verifies that the report/heartbeat gap recovers without accepting mismatched evidence.

The exact frozen release passed full Linux discovery: 1,749 tests, zero failures or errors, three Windows-specific skips. Focused checks passed 225 cases on each platform, including one Windows skip. The initial read-only Windows-mounted full attempt had five fixture-write failures and a twenty-minute interruption. A writable, verified Linux copy resolved those harness limitations; no product changes were needed for that rerun.

## Cutover and interruption

The owner explicitly authorized deployment and immediate supervisor-halt recovery. Checked acknowledgment `5ffdc443b5a7419fbfcb82aa2d5aa091` cleared only the two documented supervisor-latch fields at 06:50:34 UTC. Deployment then preserved that exact safety state. One runtime module changed; launchers, strategies, fees, risk limits, all eight wallets, the original October23 opening, cost clock and deadline were retained.

Railway deployment `ccc1e87a-de24-4876-98b2-57aa3baa2945` started the new worker at 07:08:36 UTC. Fresh worker and PC references and both checked recovery copies were verified on E: before all six release bindings were updated. SQLite itself reconciled a verified cold journal without changing logical data or schema. The actual PC matched all 129 runtime module hashes.

The restart exposed stale adaptive cost coverage during the operator pause. Existing free-access and usage-import routines restored complete accounting; no inference or paid call was made. The usage import completed at 11:43:51 UTC and the original schedules resumed at 11:44:51 UTC. This extended interruption and the unknown observations remain recorded. Nothing was replayed and no experiment clock was reset.

The daily backup task is enabled. Its October29 00:00:35 UTC backup was verified on E:; it was already completed before cutover, not a new post-cutover capture. The fresh closed cutover copy is separate evidence. Discord delivery markers confirm later natural summaries; remote message IDs are not retained.

[Native operational acceptance](../research/runs/paper-reliability-v1/live-cutover/OPERATIONAL_ACCEPTANCE.json) records the exact observation window, wallet state, backup hashes and test evidence. [Full-suite receipt](../research/runs/paper-reliability-v1/linux-full-native-20261029T070022Z-89efa4cf/receipt.json).

This establishes the recorded operational window. It does not guarantee zero future errors or prove profitability. LLM reviews remain due after five new closes; no review is forced by this repair.
