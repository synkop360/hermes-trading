# Combined storage admission contract

Proposed local implementation under the approved archive integration scope. This is not an implemented guard or a capacity certificate. Preserve the 500 MB Railway allocation, all eight accounts and original cycle/deadline/cadence. No paid resize or deployment is authorized here.

## Evidence and domains

The version3 bounded diagnostic ended with a 4,263,936-byte receiver. Nonce main/WAL/SHM occupied 4,341,240 bytes before restart, despite expiry leaving very few active rows. Its 8,638,520-byte temporary-tree peak excludes simultaneous native account roots, which had already been removed. The existing receiver-only budget does not cover this whole footprint.

The October17 Railway status receipt reports 500 MB nominal volume size and 227.401728 MB use. Assuming decimal MB, subtracting the existing 128 MiB reserve leaves 138,380,544 nominal bytes before a new cohort or staging. This is arithmetic from infrastructure metadata, not verified filesystem free space or application state.

Each storage domain must have its own counted roots, limit, protected reserve and observed free space. Railway includes the entire allocation: preserved legacy state, all eight account databases, primary publication journal, worker runtime/cache, cost/history/projection/log files and local capture stages. PC receiver/archive storage includes receiver and nonce databases, all SQLite sidecars, supervisor/review/attempt state, archive objects/manifests, retained exports and every transfer/capture/export/restore/recovery stage. Reject links/reparse points, overlapping counted roots and unregistered staging destinations. Free space on E: cannot offset Railway's allocation.

## Admission and ownership

For an operation's additional peak, require both:

```text
existing_counted_bytes + additional_peak_bytes + reserve_bytes <= domain_limit_bytes
observed_free_bytes >= additional_peak_bytes + reserve_bytes
```

Count retained exports and incomplete stages as existing bytes. Derive the effective receiver allowance from shared headroom, rather than treating its unallocated virtual maximum as current usage or letting it grow without room for the next required export.

Use a retained cross-process capacity owner per domain. Participating operations acquire capacity ownership before receiver ownership and SQLite transactions. Hold admission through publication or cleanup. Commit nonce SQL before entering receiver SQL; never retain a nonce transaction while waiting for the receiver. Two database commits remain separate, and an accepted nonce must survive a subsequent receiver failure.

Admission must precede durable nonce insertion. Bound/decode/scope/freshness/MAC checks can run without writing first; then acquire capacity ownership and reserve the whole permitted peak before calling the durable nonce adapter. Reads, exact-operation retries and failed CAS requests also consume nonce work and require admission. A fresh transport nonce with the original operation ID remains the recovery pattern after a lost reply.

## Enforced nonce envelope

The existing migrated WAL repository has no suitable whole-footprint ceiling. A dedicated adapter must preserve accepted nonce history until legitimate expiry, with finite verified pages, active rows and transaction sidecars. A candidate synthetic policy is 4 KiB pages, at most 256 main pages and at most 256 unexpired rows; these are proposed test parameters, not measured production limits.

If WAL is retained, a controlled sole writer must complete a bounded checkpoint/truncation before admission, disable uncontrolled cache spill and enforce the maximum dirty-page transaction. For 256 dirty pages, reserve 1,054,752 bytes for the WAL header and frames, plus a verified finite SHM allowance. Reject a busy checkpoint or unsupported page/journal configuration before nonce insertion. Auto-checkpoint or journal-size settings alone are insufficient. A separately purpose-bound rollback-journal nonce adapter is an alternative if the WAL envelope cannot be proved. Never clear accepted nonces to create room.

## Export, recovery and failure

Receiver rollback writes retain the conservative three-main-extents plus 64 KiB envelope. Shared budgeting adds nonce and all other domain costs. A future coherent version2 receiver/nonce export reserves approximately two additional snapshot extents for backup plus ZIP, certificate/staging overhead and retained prior exports. Restore reserves its new copies and verification intermediates in the destination domain. Hard-link publication does not make an independent copy free. Version1's exporter does not certify version2 or nonce recovery.

Admission failure creates no new stage and persists no nonce, reservation or grant. Preserve original source evidence and frozen packets; block new entries and retirement. A live owner can use an existing one-use protective authorization only if the separate all-eight-state protective-write admission passes. Existing reserve and two-packet checks remain unchanged until the combined bound is proved.

Required checks: oversubscribed concurrent admission; nonce rejection before insertion; busy checkpoint rejection; nonce survival after receiver failure; no stage created on failed export admission; Railway reserve preservation; links/overlap rejection; and complete cohort/staging/export/restore peak accounting. Newer prepared-receiver latency tests do not satisfy these requirements.

Source references: `archive_protective_transport.py`, `control_auth.py`, `persistence/repository.py`, `archive_witness.py`, `archive_receiver_export.py`, `paired_storage_admission.py`, `paired_worker.py` under `hermes_trading/`. Evidence: `research/runs/production-archive-integration-v1/protective-capacity-probe-v3.json` and `railway-readonly-status-2026-10-17.json`.

## Local execution step

First implement a disabled retained domain-admission API with a fixed owner record under its anchor root. The record binds the complete registered root inventory and finite policy; it is initialized explicitly, never lazily by a request. Registered roots must be disjoint directories on the same filesystem, and the owner record itself is counted. A bounded nonblocking OS file lock on that original record serializes participating processes before receiver/nonce ownership. Changed policy, missing owner, links/reparse points, hard-linked files, unknown free space, excessive inventory and unsupported files block admission.

Hold the owner through the operation. A lease admits a caller-derived additional peak, allows only paths inside registered roots, and rechecks actual growth, remaining peak and reserve before release. It is bound to the original process/thread and cannot be reused after release. This is cooperative admission, not an OS disk quota: each integrated writer must still enforce its SQLite/page/object envelope. Installation must prove that the inventory contains all state/staging and every writer participates; a standalone API cannot prove those deployment facts.

Test actual competing processes, accepted growth followed by denied oversubscription, crash-release of the owner, complete eight-account/legacy/sidecar/staging counts, independent free-space rejection, no stage or owner creation on failed admission, policy tampering, expired leases, unregistered paths and bounded scans. Then integrate a separately bounded nonce adapter before receiver work. Existing V1/default paths and candidate activation remain unchanged.

Prepared receiver ownership requires a retained outer `StorageDomain.ownership()` session, acquired before preparing SQLite handles. `StorageOwner.admit()` grants sequential bounded operation leases without releasing that outer owner between requests. Close all prepared handles before releasing the outer owner. The short `StorageDomain.admit()` convenience path is for operations without pre-owned prepared handles. Ordinary admission defaults to a ten-second ceiling; explicit bounded preparation/export may select its original ceiling up to120seconds. Separate supervisor/export processes must submit work to the same retained owner/coordinator or remain excluded while it is held; they must not retain their writer locks while waiting for it. The broker/runtime wiring remains a separate integration requirement.
