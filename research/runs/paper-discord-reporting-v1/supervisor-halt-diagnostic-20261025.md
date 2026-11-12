# Sanitized supervisor-halt diagnostic — October 25, 2026

Local observation at **10:23:20 UTC** (11:23:20 UTC+01:00), using read-only Hermes cron metadata under `C:/Users/thoma/AppData/Local/hermes`. Job `2f6c5088c6ff` remains enabled, `no_agent=true`, and scheduled. No watcher, model/provider, API, SSH, or other network operation was executed; no latch, schedule, configuration, runtime, test, or script was changed. This is not a fresh remote-health inspection. [Sanitized structured receipt](supervisor-halt-diagnostic-20261025.json).

The correct output location is the **flat job directory** `cron/output/2f6c5088c6ff/`, with completion-time filenames in local time. For example, the failed 09:35 UTC execution is `2026-10-25_10-35-23.md`, not a `09-35-23` directory or UTC-named file. The retained files for the recent failures are:

| Output filename | Local start, UTC+01:00 | Cron duration | Retained typed result |
| --- | --- | ---: | --- |
| `2026-10-25_09-51-21.md` | 09:51:10.786 | 11.181 s | `child_failed`, launcher exit 1 |
| `2026-10-25_09-59-22.md` | 09:59:11.146 | 11.018 s | same |
| `2026-10-25_10-21-25.md` | 10:21:12.124 | 13.599 s | same |
| `2026-10-25_10-29-25.md` | 10:29:12.454 | 13.548 s | same |
| `2026-10-25_10-33-26.md` | 10:33:12.626 | 13.779 s | same |
| `2026-10-25_10-35-23.md` | 10:35:12.709 | 11.088 s | same |
| `2026-10-25_11-19-33.md` | 11:19:15.894 | 17.642 s | same |

Each retained failure contains only the native launcher result `status=child_failed`, `exit_code=1`, `wakeAgent=false`. The 10:29 filename is one second earlier than the execution database's final timestamp of 10:29:26.001; file output precedes the database completion update. Fifty Markdown outputs are currently present, from 09:43:24 through 11:22:07 local time. Earlier output files, including the three-failure streak below, are not in this retained directory; their execution metadata remains available.

For the local calendar date October 25, the point-in-time database sample contains **372 executions: 311 completed, 60 failed, one running**. Failed durations range from 9.081 to 17.642 seconds, median 12.004 seconds. Aware UTC ordering, preserving both DST offsets in the source timestamps, finds one maximum three-failure streak:

- 06:13:00.553 → 06:13:12.269 UTC+01:00, 11.715 seconds, exit 1.
- 06:15:00.645 → 06:15:09.726 UTC+01:00, 9.081 seconds, exit 1.
- 06:17:00.755 → 06:17:14.773 UTC+01:00, 14.018 seconds, exit 1.

The latest failure in this sample is at **10:19:33 UTC**, after the root's supplied 10:00 UTC remote observation. It is followed by a completed local execution at **10:22:07 UTC**, duration 50.646 seconds; job metadata then reports `last_status=ok`. These local snapshots are not an atomic cross-file view and do not establish current remote admission or successful model review.

The sticky state is consistent with the existing contract: `paper_safety.py:283` preserves a latched `entry_halt`, and line 285 keeps its error count after later success. Thus `consecutive_errors=3`, `entry_halt=true`, and latest `success=true` can coexist intentionally. The local three-failure streak is compatible with reaching that threshold, but cron metadata alone cannot prove which attempts were received by remote supervisor-health accounting or when that latch was set.

The underlying failure cause remains **unknown**. `scripts/native_hermes_watch.py:35` captures child stdout/stderr, but line 39 returns only `completed` or `child_failed`; it discards those captured streams and normalizes every nonzero child exit to launcher exit 1. The retained files do not classify these cases as `launcher_failed` or the wrapper's `child_outcome_uncertain`. The exact child exit code, exception, and whether SSH, provider, model, transport, or another stage failed cannot be recovered from this evidence. No such cause is inferred, and no halt reset is proposed or performed.

## Supplement at 10:29 UTC — verified-copy chronology

The root agent separately read the small safety-state JSON from the already [verified October25 E: copy](daily-copy-verification-20261025.json) and supplied these sanitized values. That copy was captured at **00:00:26.323709 UTC**. Its supervisor state has `consecutive_errors=0`, `entry_halt=false`, `success=true`, last seen at October24 **23:59:00.260041 UTC**; its worker was last seen at October25 **00:00:26.304742 UTC**. The reviewer did not repeat the E: verification or read another actual-state source.

The latch was therefore absent in the captured copy and present by the root's later **09:59:45 UTC** heartbeat observation. The local 06:13–06:17 UTC+01:00 cron failure streak falls within that interval. This narrows the chronology; it does **not** identify the qualifying remote health events, prove that this particular streak caused the latch, or reveal the underlying child exception. The exact onset and cause remain unknown. The structured receipt records this supplement separately from its original local-cron snapshot; no action or state change occurred.
