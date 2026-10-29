from pathlib import Path
import json
RUN=Path(__file__).resolve().parent;ROOT=RUN.parents[2];LIVE=RUN/'live-cutover'
a=json.loads((LIVE/'OPERATIONAL_ACCEPTANCE.json').read_text());n=a['completed_scheduled_checks']
text=f"""# Paper supervisor reliability: deployed October 29

Release `c7f816d` is deployed to Railway and the PC supervisor. At {a['observed_at']}, the worker was healthy, entries were enabled, both error counters were zero and all eight wallet reports matched their settled batch. {n} consecutive scheduled supervisor runs completed with no failures after schedules resumed.

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
"""
(ROOT/'docs/PAPER_SUPERVISOR_RELIABILITY.md').write_text(text,encoding='utf-8')
p=ROOT/'docs/GOAL_PROGRESS.md';old=p.read_text(encoding='utf-8');historical=old[old.index('## Historical prelaunch status'):]
p.write_text(f"""# Goal progress

Last updated: 2026-10-29 (Europe/Paris).

**Overall estimate: 65%**

```text
[#############-------] 65 / 100
```

**Paper operation: deployed and verified.** Release `c7f816d` runs on Railway with matching PC supervision. The latest acceptance verifies entries enabled, a healthy single worker, zero error counters, all eight matching wallet settlements and {n} consecutive successful scheduled checks. Normal supervision and daily backups are enabled; the legacy watcher remains paused. [Current deployment and verification](PAPER_SUPERVISOR_RELIABILITY.md).

The correction fixes two reproduced snapshot timing faults while preserving all risk limits, original history and the October23 experiment clocks. Full Linux discovery passed 1,749 tests with zero failures/errors and three platform skips. Both deliberately sampled live database-contention windows now validated successfully. The extended deployment pause and stale-cost observations are retained and disclosed in the deployment record.

This percentage is an engineering and forward-evaluation estimate, not a probability of profit. The original recovery goal remains active: operational reliability must persist, LLM changes need actual qualifying closes, and the longer forward comparison must establish performance after costs. No profitable strategy or minimum profitable EUR50–200 wallet has been demonstrated. Archive expansion remains deferred.

"""+historical,encoding='utf-8')
print(json.dumps(dict(status='current_deployment_and_progress_recorded',scheduled_checks=n)))
