# Cloud beta: current resumption checkpoint

Updated November 10, 2026. Do not treat the new cloud bootstrap as deployed.

## Production

Railway project f78f42d9-20ec-44f6-ae3f-f8bb79ee7000; production environment
fdcc71a3-00a9-410c-8d6f-59af9bc98d36; service 8b94cbc9-293e-4222-a8a7-698b656985f7.

Live deployment dded251b-887e-4c1b-a031-f25a1f3aa6ec settled SUCCESS with one
running replica and no pending environment changes. It runs the original
37e1be8 paper runtime, fingerprint
19fdcb676120b21b44d54076de08e148259e34bdfe61bbdbdffbf20b694e12ed.
Its only new behavior is allowlisted storage telemetry alongside the normal worker.

The eight simulated accounts are observed, but entry readiness is blocked.
Latest actual measurements: state_bytes 307926797, free_bytes 134189056,
reserve_bytes 134217728, max_state_bytes 500000000. The original durable
storage_stop is true; supervisor health is also stale. No halt, history,
counter, clock, risk limit or model selection has been cleared or reset.

The existing volume 0ae9532a-b9cf-4df6-a911-f51d4b9daa6d remains mounted
at /app/state with size 500 MB. It cannot fit two full native rollback copies.
The user was asked to resize that same volume to 2 GB in Railway; remote
connector tooling does not expose resize. Do not detach, replace or delete it.
Do not raise the logical state budget/reserve to hide the admission failure.

## Reviewed source candidate

PR 18: https://github.com/synkop360/hermes-trading/pull/18
Code commit: c7780dc95649a0c070a5c4e491779113acde8d2d.
Supervisor release:
e55a303b2fa9e50aef8b4ac0287a62da84be649e0134b030487441f31f3cd606.
Canonical source verification proves 126 of 129 modules unchanged from Git37e1be8.
The changed modules are model_access_refresh, proposal_transport and supervisor_launch.

The new bootstrap retains native source-transition evidence in an owned volume
namespace, checkpoints closed SQLite journals only after backing up raw evidence,
relocates the original supervisor journal, and launches the normal protective
worker beside finite supervision and one fresh dedicated Nous device session.
Private checkpoint values are not passed to children. Only typed operational
events and the real portal verification URL/code may be logged.

Independent installation review found and verified fixes for two restart defects:
space reservation now counts only outstanding rollback copies; an installed
worker can restart after supervisor expiry while no further supervisor/auth
children are started. Native supervisor launch admission remains strict by default.

Fresh Windows validation: 46 targeted tests passed in 42.426 seconds.
Independent reviewer validation: 29 tests passed; no remaining blocking findings.
The earlier published foundation at 8581b776 passed 240 Linux tests.
The new complete candidate still requires its own Linux CI result.

## Private checkpoint and workspace

Workspace E:/workspace/hermes-cloud-supervision-20261109.
Private archive state/railway-supervisor-transfer-20261109/checkpoint.zip:
19 files, 252335 bytes, SHA256
8ab807671ece52022eb954886cc9b4266820671b991e8bca0701cd33bfe80be6.
Captured with original PC supervisor disabled and source hashes unchanged.
Never publish its contents or include them in app logs or model prompts.
Python E:/workspace/hermes-python311/python.exe;
TMP/TEMP C:/Users/thoma/AppData/Local/Temp.

## Remaining activation work

1. Confirm actual expanded volume capacity/free space. Inspect the existing
   durable storage halt and perform only reviewed, evidence-preserving native
   recovery; no blind flag removal or experiment reset.
2. Require successful Linux CI for the exact published candidate.
3. Quiesce the existing image only after an actual settled native batch using
   the pinned predecessor handoff helper; observe quiescent status before switching.
4. Transfer the checkpoint privately using bounded service variable chunks.
   Stage exact GitHub commitSha, Dockerfile, normal cloud start command and
   existing volume. Inspect staged changes before committing the deployment.
5. Observe installation receipt and clean private transport variables without
   redeployment. Relay the actual fresh Nous portal verification URL immediately.
   Never copy the PC rotating refresh token.
6. Verify actual free personal access, pinned zero-price model catalog, current
   model-access/cost evidence, supervisor health and entry admission before
   calling the beta functional. No real orders or profitability qualification.
