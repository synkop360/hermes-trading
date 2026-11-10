# Cloud beta: current resumption checkpoint

Updated November 10, 2026. The user cannot upgrade capacity. Stay on the
existing 500 MB volume; use verified compression and bounded migration backups.

## Production, verified

Project f78f42d9-20ec-44f6-ae3f-f8bb79ee7000, production environment
fdcc71a3-00a9-410c-8d6f-59af9bc98d36, service8b94cbc9-293e-4222-a8a7-698b656985f7.
Volume0ae9532a-b9cf-4df6-a911-f51d4b9daa6d remains at /app/state,500MB.
Live deployment a85513c6-53cb-4dc9-9ed3-9a401e763526 is SUCCESS, one replica.
Runtime fingerprint19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed
is the original installed37e1be8 package.

User approved retiring fixed obsolete root logs only after exact compressed
copies were verified. Deployment01a2c0fb reclaimed40,111,226bytes from seven
logs. Their original bytes remain restorable in .legacy-history-compact.
All eight active wallets, economic histories, limits, counters and clocks remain.
The archive-only path remains the compactor default; original retirement requires
an explicit literal opt-in. Initial automatic review rejection was followed by
the user's explicit accept and a successful reviewed deployment.

Fresh closed preflight: state_bytes267819995, free_bytes174321664,
reserve_bytes134217728, max_state_bytes500000000, storage_ready=true,
all_positions_closed=true, pending_batch=false, last_batch_storage_stop=false,
worker_running=true. The old storage_stop remains latched. Stale supervisor
health also blocks new exposure. Do not claim the beta functional yet.

## Reviewed implementation

PR18 https://github.com/synkop360/hermes-trading/pull/18
The prior complete candidate35dad760b304c1fc78e593258e8878272cd2be80 passed270
Linux tests (run42862964474). Runtime/supervisor release remains
e55a303b2fa9e50aef8b4ac0287a62da84be649e0134b030487441f31f3cd606.
Its source map points to actual runtime/launcher code commit
f3daae5350d92f3e33d8a99aacdd2d0e1389b8e2.
The following storage phase changes scripts and tests, not that runtime map:
- paper_compact_history.py: fixed legacy logs only, actual worker lease,
  bounded streaming gzip, exact decoded SHA256, durable receipts and safe replay.
- paper_release_transition.py: within explicit owned-volume context retain full
  preimages of only six fixed mutable release-binding components. Every untouched
  financial/history file stays in place under the full frozen protected map.
  Ordinary callers retain full copies. Completed reconciliation now verifies
  both rollback evidence and full history, including corruption regression.
- paper_cloud_install.py: reserve only outstanding component preimages and the
  relocated supervisor journal, avoiding two full-state temporary duplicates.
- paper_storage_recovery.py: one original-source-pinned native recovery for this
  closed-position, nonterminal, still-running storage pause. Requires fresh
  headroom, unchanged boundary and no pending publication under real worker and
  publication leases. Retains byte-exact original halt plus full lifecycle
  preimage, compare-and-sets only storage fields, moves the marker into its
  audit directory and seals a durable receipt. Other safety reasons stay intact.
  Actual native interruption/reconciliation and negative admission tests exist.

Independent reviews report no blocking findings in these storage changes.
Require the exact storage candidate's Linux CI before its production recovery.
No storage recovery, checkpoint upload, image-source switch or Nous login has
yet been performed at this checkpoint.

## Workspace/private checkpoint

Workspace E:/workspace/hermes-cloud-supervision-20261109.
Python E:/workspace/hermes-python311/python.exe, TMP/TEMP
C:/Users/thoma/AppData/Local/Temp.
Private archive state/railway-supervisor-transfer-20261109/checkpoint.zip:
19files,252335bytes,SHA256
8ab807671ece52022eb954886cc9b4266820671b991e8bca0701cd33bfe80be6.
Captured with original PC supervisor disabled and hashes unchanged.
Never publish its contents, financial state, access tokens or refresh tokens.

## Continue activation

1. Verify storage candidate CI. Execute reviewed one-shot recovery on the OLD
   image, then observe fresh native storage_ready and storage_stop=false.
2. Quiesce old image after an actual settled batch with pinned handoff helper,
   then privately transfer the checkpoint with bounded variables.
3. Stage exact CI-passed GitHub commitSha, Dockerfile, cloud start command and
   unchanged volume. Inspect all staged changes and deploy. Never use the older
   default branch or enable arbitrary push-driven image changes.
4. Observe durable installation receipt and clean transport payload variables.
   Relay the real fresh Nous device verification link immediately. Never reuse
   the PC rotating refresh token.
5. Verify actual free personal access/catalog, original pinned model, current
   cost/model evidence, worker/supervisor health and paper entry admission.
   Never reset the retained experiment, raise risk, clear unrelated halts,
   place real orders or claim profitability.
