# Cloud beta: November 12 installation recovery

The user explicitly approved the private252335-byte/19-file supervisor checkpoint
transfer into the existing Railway hermes-trading/production service. All29bounded
payload variables plus count/checksum/approval metadata were uploaded with
skipDeploys. Never print or publish their contents. Clear their contents only
after confirmed durable installation.

Project f78f42d9-20ec-44f6-ae3f-f8bb79ee7000, production environment
fdcc71a3-00a9-410c-8d6f-59af9bc98d36, service8b94cbc9-293e-4222-a8a7-698b656985f7.
Retain volume0ae9532a-b9cf-4df6-a911-f51d4b9daa6d at /app/state,500MB.
The user cannot buy capacity and authorizes verified history compression and
fewer wallets if necessary. No risk increase, fabricated costs/model access,
experiment reset, or real orders.

## Actual operations

Seven obsolete root logs were losslessly compressed and originals retired only
after explicit user approval and exact restorable-copy verification. This reclaimed
40,111,226bytes November10, retaining eight active wallets. The reviewed native
one-shot storage recovery then preserved the full lifecycle/marker preimage and
resumed only the storage latch, without changing clocks/counters/financial rows.

By November12, state growth again exhausted the134217728-byte reserve. Storage
latched with~134.2MBfree. Read-only SQLite counters found essentially no reclaimable
free pages, so VACUUM alone will not fix growth. Do not blindly clear this new halt.
Root legacy database~57MB and retained account JSONL mirrors remain potential
archival work; readers, mirror deduplication and financial/replay invariants must
be preserved. Do not silently suspend wallets or label stale wallets observed.

Source-pinned handoff65dffdf8-5141-4035-b8fc-dc5d665c61b1 verified true quiescence
under the original real worker lease, valid latest committed batch, no pending
batch and all positions closed. It did not start a diagnostic sleep worker.
The original package fingerprint was
19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed.

The service now pins GitHub source commit620e78fe413e1c29c574b5e7ba3804d110f24e47,
which passed282Linux tests in16.218s (run43171774561). Its Docker image built
successfully. Supervisor release map remains
e55a303b2fa9e50aef8b4ac0287a62da84be649e0134b030487441f31f3cd606.

Deploymentca843737 crashed after checkpoint_verified, before the closed-journal
and source-transition phases. The private-safe diagnostic patch was cancelled
twice by Railway accept-deploy despite chat approval. The user applied it directly
in the web interface. Diagnostic deployment73b4831f-1077-45e3-955b-21056fcdf30c
confirmed recover_journals line154: SQLite recovery left an open sidecar.
The exception digest matched f6fa1f1f9371daa66cc8ad021d36fec2d16620a5017b0322d953633b769fa05b.
No exception values, financial data or locals were printed.

## Exact reproduced fix

A real SQLite PERSIST-mode regression reproduces that error with a closed
zero-header rollback journal. The fix retires only an unchanged, exactly archived
zero-header original journal under the actual exclusive DB lease, with no active
transaction and DELETE journal mode. A WAL->DELETE generated journal belongs to
SQLite's pager; archive that generated artifact too, then let connection close
remove it. Never unlink a journal still owned by SQLite. Keep the mandatory
post-close absence check and full schema/row hashes before/after recovery.

Nonzero, unarchived and active journals remain untouched and fail closed.
Raw original and generated evidence remains in verified private ZIP archives.
Official protocol reference: https://www.sqlite.org/lockingv3.html.
Fresh local validation: all10 installation/recovery tests passed12.307s, including
the real native retained install/restart. Independent review passed9 targeted
tests and reported no remaining blocker. Run full Linux CI for this new fix.

## Continue

1. Publish and require full CI for the journal fix. Stage exact new commitSha
   on the same service/volume, retaining the approved private-safe bootstrap
   diagnostic command until actual installation succeeds. If accept-deploy is
   cancelled, tell the user to apply the prepared patch in Railway, not to repeat
   chat consent indefinitely. Never bypass a cancellation.
2. Observe closed_journals_verified and original native c7->37->cloud transitions,
   relocation, installed receipt and actual worker startup. Do not infer success
   from image build or platform SUCCESS alone. No source-binding transition has
   yet been observed at this checkpoint.
3. Once installed, blank all numbered checkpoint payload variables and PARTS
   with skipDeploys. Keep checksum metadata for durable replay if required.
4. Relay the actual fresh Nous device verification link immediately. Never copy
   the PC rotating refresh token. Native daemon waits for auth without consuming
   failed cycles.
5. Resolve storage pressure with evidence-preserving archival and an audited
   recovery for the new halt. Verify fresh free personal/catalog/cost/model
   evidence and entry admission before calling the beta operational.

Workspace E:/workspace/hermes-cloud-supervision-20261109, Python
E:/workspace/hermes-python311/python.exe, TMP/TEMP C:/Users/thoma/AppData/Local/Temp.
Private checkpoint state/railway-supervisor-transfer-20261109/checkpoint.zip
SHA2568ab807671ece52022eb954886cc9b4266820671b991e8bca0701cd33bfe80be6.
The PC supervisor was disabled when captured; preserve its clocks/reviews/counters.
