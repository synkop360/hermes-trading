# Persistent Railway installation

Previous phase: PR 18 at 8581b776f3e3adb641aeed49b2b4803225fdbd55.
Its Linux CI passed 240 tests; imports of the pinned editable Nous client pass.
Current candidate successor map: e55a303b2fa9e50aef8b4ac0287a62da84be649e0134b030487441f31f3cd606.

The user repeatedly authorized continuation and deploying the paper beta.
This phase must complete the actual volume cutover, preserving the experiment.

1. Persistent owned auxiliary namespace `.paper-cloud-cutover` on the existing
   volume, so native transition plans, rollback copies and receipts survive a
   container restart. Ordinary transition callers retain disjoint-root rules.
   RED observed: native restart test failed for missing context. GREEN: same
   test passed after adding the strict owned paths and policy/account marker.
2. Validate and privately transfer the captured 19-file supervisor checkpoint.
   Never put checkpoint contents in GitHub, app logs or a public artifact.
3. Bootstrap using the native c7f816d -> 37e1be8 and 37e1be8 -> cloud transitions,
   reuse durable intents on restart, then relocate journal/launch without resetting
   counters, clocks or histories. Publish an installation receipt only after checks.
4. Launch the normal worker, finite supervisor and a single dedicated Nous device
   login. Use official pinned auth helpers and a new persistent auth home; never
   copy the PC rotating refresh token. Relay its real verification URL immediately.
5. Review, CI, deploy the exact branch, verify source and installation status,
   then verify fresh supervision/model readiness and actual paper entry admission.

Private local checkpoint (ignored state/): archive has 19 files, 252335 bytes,
sha256 8ab807671ece52022eb954886cc9b4266820671b991e8bca0701cd33bfe80be6.
It was captured with the original PC job disabled and source hashes unchanged.

Native Windows test runtime now works at E:/workspace/hermes-python311/python.exe.
Use TMP/TEMP=C:/Users/thoma/AppData/Local/Temp for fsync tests; E: is much slower.
The broader historical native suites were interrupted/timed out on slow SQLite
writes; do not claim they passed. The actual Linux relevant suite did pass 240.

## November 10 resumption

Windows workspace and private checkpoint recovered. Production remains on its
original 37e1be8 runtime. Deployment dded251b-887e-4c1b-a031-f25a1f3aa6ec
adds only typed storage telemetry alongside the normal worker; settled SUCCESS,
one running replica, no pending environment changes.

Observed native storage telemetry: state_bytes=307926797,
free_bytes=134189056, reserve_bytes=134217728, max_state_bytes=500000000.
The native durable storage_stop is true, in addition to stale supervision.
No halt flag or retained history has been cleared. No checkpoint variables,
new source image, authentication session or bootstrap have been installed.

The existing 500 MB volume cannot hold the two full rollback copies. Requested
the user resize that existing volume to 2 GB through Railway; the connector
does not expose resize. Keep the logical state budget and reserve unchanged.
After actual capacity is available, inspect and recover the durable storage
halt through a reviewed native procedure before claiming paper entry readiness.

Fresh pre-review validation: 30 Windows tests passed in 64.062 seconds. Review
identified two restart defects, reproduced and corrected: reserve only the
rollback copies still to be created, and let the existing protective worker
start after supervisor expiry without creating further supervisor/auth cycles.
The new read-only allow_expired launch option preserves identity, selection,
policy, source, path, activation and exact expiry checks; normal supervisor
invocations retain strict lifetime admission. Canonical verification now proves
126 of 129 runtime modules unchanged from the pinned 37e1be8 Git blobs.
