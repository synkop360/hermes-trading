# Cloud beta: resumption checkpoint

The Windows execution environment disconnected before the final private state transfer.
Do not treat the local installation changes as deployed.

## Remote state verified

- PR 18: https://github.com/synkop360/hermes-trading/pull/18
- Published code commit: 8581b776f3e3adb641aeed49b2b4803225fdbd55.
- Linux CI run 42711384997: 240 tests passed in 10.890 seconds.
- Published supervisor release: 5a302bb9df4f3289cc3b0b16c233ff281cf8c33964abb70603e0fee2e50240c1.
- Production Railway deployment: 3047dfe7-cedd-479e-b00b-8d6d2e89062b, SUCCESS, one running replica.
- Its installed runtime hash: 19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed.
- The existing worker is collecting with eight simulated accounts. Latest verified entry blocker: supervisor_health_stale; model_ready=false.
- No Railway source switch, checkpoint variable upload or volume cutover was performed in the disconnected phase.

Railway project f78f42d9-20ec-44f6-ae3f-f8bb79ee7000; service 8b94cbc9-293e-4222-a8a7-698b656985f7;
production environment fdcc71a3-00a9-410c-8d6f-59af9bc98d36.
The existing volume is mounted at /app/state (500 MB); container memory limit is 2 GB.

## Local work to recover from the Windows workspace

Workspace: E:/workspace/hermes-cloud-supervision-20261109.
The local changes below are not included in the published commit above:
- scripts/paper_cloud_install.py: validated private ZIP input, durable owned cutover namespace, native source-transition replay, journal relocation, backed-up SQLite checkpointing and verified audit compaction.
- scripts/paper_cloud_auth.py: a new dedicated Nous device session using the pinned official client; never copy the PC rotating refresh token.
- scripts/paper_cloud_service.py: normal worker plus finite supervisor/auth children, bounded process-tree ownership and allowlisted event relay.
- scripts/paper_handoff.py: use ONLY on the installed predecessor runtime to leave through native finally blocks after a settled batch. It emits code-only status and does not latch an operator stop.
- scripts/paper_release_transition.py: explicit volume_cutover_layout context. Ordinary callers retain disjoint-root rules; only the new owned auxiliary namespace is excluded from the worker role, while all original account/history data and the supervisor role remain protected.
- scripts/paper_local_transport.py: bounded streaming observer support.
- scripts/paper_supervisor_service.py: wait for dedicated authentication without consuming cycles or emitting artificial failed health observations.
- New tests for installation, authentication, service coordination and handoff; extra native restart/volume-layout tests.
- Updated Docker command, CI, local source maps and copied allowlisted paper_service health observer.

The private supervisor checkpoint is at:
E:/workspace/hermes-cloud-supervision-20261109/state/railway-supervisor-transfer-20261109/checkpoint.zip
with its receipt.json beside it. It contains 19 retained files and was verified against the unchanged, disabled PC supervisor.
Never publish the archive or its contents to GitHub or application logs.

Local pending supervisor release after the last pin regeneration:
c0e4a6e681cc5a0955411166030cdd0906aef7900c748a924ecf33d54eb371b6.
Recompute all hashes from recovered files; this is an unshipped candidate, not a production receipt.

## Validation actually observed locally

- 99 targeted tests passed under Python 3.11.9 in 42.239 seconds after the first installation integration.
- A later 16-test installation/auth/service/native-transition run passed in 34.575 seconds.
- The latest 14 handoff/auth/service/supervisor tests passed with a canonical writable temporary directory.
- SQLite crash/WAL test first exposed a Windows orphan SHM; the fix backs up raw journals, proves unchanged logical rows, switches to DELETE under an exclusive lock and only then removes orphan SHM metadata. Its focused tests passed.
- None of these local results establishes a pass for the new Linux candidate or a deployed beta.

Native test runtime: E:/workspace/hermes-python311/python.exe.
Set TMP and TEMP to C:/Users/thoma/AppData/Local/Temp; the default application temp path can be a rejected reparse path, and E: fsync is much slower.

## Remaining work before activation

1. Reconnect the Windows environment and recover the local changes/checkpoint.
2. Review the complete installation phase, including failure/restart cases and the bounded auxiliary namespace. Keep economic state, clocks, review rows and counters unchanged.
3. Check actual state footprint and filesystem free space from native storage telemetry. Railway reported DISK_USAGE_GB=0.392, but this alone was not established to be the size of /app/state. The current installer reserves room for two full native rollback copies before compacting them; verify this fits the 500 MB volume before quiescing production. Do not silently raise storage budgets or discard historical evidence.
4. Freeze the complete candidate to a real source commit, update both release-map copies and native script pins, and run Linux CI. Current local source maps still require the new real commit reference.
5. For the source switch, quiesce using the OLD installed image/runtime first, after a native settled batch, so no pending old-release batch crosses the image change. Do not introduce a diagnostic sleep service. Observe actual quiescent status before switching.
6. Transfer the checkpoint privately into the existing Railway service, with bounded variable chunks or another supported private path; clean the transport payload after successful installation. It must never enter public logs, GitHub or model prompts.
7. Stage the exact new GitHub commit using Railway commitSha pinning, the cloud service start command and the existing volume. Do not deploy the older default branch or follow arbitrary future pushes.
8. Relay the actual Nous verification URL immediately when the dedicated cloud login emits it. No access/refresh token should appear in logs. Wait for real personal/free-catalog verification.
9. Verify fresh worker and supervisor health, model readiness, native cost receipts and paper entry admission. Only then call the beta functional.

The user authorized development and launching paper trading on Railway, and explicitly selected supervision entirely on Railway. No real orders, reset of the retained experiment, fabricated free-access/cost evidence, or arbitrary risk increase is authorized.
