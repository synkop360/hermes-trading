# V2 paired capture implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline with TDD; preserve the dirty candidate checkout and freeze verification sources. Read-only parallel contract review is complete. No commit, push, deployment or live operation is requested.

**Goal:** Produce and independently validate one coherent V2 receiver/replay-evidence capture without altering its original state, then add an exact-cut local export/restore.

**Architecture:** Retain the original domain owner and prepared receiver gate, validate/commit read transactions before SQLite backup, and capture nonce state through a separate non-mutating owned API. Bind both copies to original identity/generation and a canonical certificate; restore only an externally pinned artifact to one new directory. Local correctness never asserts an independent backup or permission to resume/retire.

**Tech stack:** Existing Python/sqlite3/unittest, rollback journals, cooperative storage admission. No added dependency or paid API.

**Spec:** `2026-10-18-v2-export-recovery-contract-notes.md`, original approved `2026-10-16-archive-integration-implementation.md` tasks2/5/6 and `2026-10-17-combined-storage-admission.md`.

## Constraints and rulings

- Existing October16 approval covers disabled archive implementation and synthetic fault checks; continue within that authorization. Do not repeat approvals for successive internal implementation steps. Any external deployment/retirement/activation or paid service remains excluded.
- Preserve V1, generic authentication, original seven-field identity, all eight accounts, cadence, financial semantics and original cycle/deadline. Do not reset or prune evidence while taking a snapshot.
- Export30seconds, restore120seconds, one absolute deadline, artifact ceiling `2**31-1` bytes. Proposed nonce4MiB envelope stays unqualified for exhaustive VFS peaks; do not silently raise limits.
- Source SQLite write transactions must be committed/rolled back before `Connection.backup`; keep actual receiver gate/exclusive connection and original outer owner. Never query the backup destination from its progress callback.
- Publication must not expose half a restored pair. Atomic new-directory publication plus canonical completion evidence is required. A selected exact-cut archive digest is an input, never learned from the archive being checked.
- Main and rollback files, backup copies, archive and prior retained exports count toward admission. No staging before successful admission. Capacity inventory rejects hardlinks; do not leave two links when checking inventory.
- The first30days fits43200shared one-minute batches within original259200cycles/180days. Historical1.61GB/30day source projection does not fit the500MB Railway volume and is not an upper bound. This is a beta-launch engineering dependency, not a requirement to prove profit before beta.

## Task1: Non-mutating admitted nonce capture

- [x] Add real-file failing tests for `store.capture(lease, deadline=...)`: no arming/nonce acceptance, immutable metadata/rows, lease/owner/path/thread/deadline checks, no implicit creation, and rejection of nested acceptance/capture.
- [x] Add `NonceCapture.evidence` (copy), `backup_to(new_path)` and `verify_source()` under that context. Preserve the expected external generation and exact schema/limits. Deterministic nonce fingerprint sorts by nonce, not rowid. Backup opens/recovery are admitted; validate source and copy without modifying persisted floor or expiry.
- [x] Reject backup target replacement, unavailable headroom and expired deadline; preserve a clean source on destination failure. Real hot-journal recovery may legitimately change physical bytes, so compare logical evidence there.

## Task2: Owned prepared receiver capture

- [x] Add failing real prepared receiver tests for `receiver.capture(lease, deadline=...)`, retaining its gate and original same-domain clock. Reject unprepared/foreign ownership and request reentry; preserve pending reservation/credit/protection exactly.
- [x] Add `ReceiverCapture.evidence`, `backup_to(new_path)` and `verify_source()`. Validate and commit unchanged receiver transaction before backup. Fingerprint original schema and every event in sequence; fully verify copied original chain. Never refresh a changed source into a new accepted cut during capture.
- [x] Reuse one bounded file/backup helper where it removes duplication, with original-file identity checks before/after open/commit and no destination SQL inside progress callbacks. Preserve source readiness after destination-only faults; invalidate preparation when original source integrity is lost.

## Task3: Exact V2 pair artifact and restore

- [x] Add `archive_protective_export.py` with separate purpose/schema and three fixed ZIP_STORED members: `receiver.sqlite`, `nonces.sqlite`, `certificate.json`. Certificate binds original identity/binding/full head, both exact copy bytes/hashes/schema/row fingerprints, external nonce generation/configuration/floor/row count and random capture ID.
- [x] Derive and admit a conservative total peak before stage creation; hold original owner/gates through validation and publication. Bound member/directory parsing before ZIP allocation; no ZIP64/compression/comments/arbitrary paths. New targets only. Failed publication preserves source and prior target.
- [x] Restore requires caller-pinned archive/certificate digests and original identity/binding/nonce generation. Fully validate both copies before publishing one containing directory with completion evidence. Partial stages never become valid pairs; never create a new nonce store to repair a missing member.
- [x] Test same-timestamp nonce loss with unchanged receiver head, stale exact-cut pair, mixed copies, wrong generation, malformed ZIP, source change, deadline/full-capacity denial, partial publication and lost acknowledgement. Keep `independent_backup_verified`, `runtime_resumption_authorized` and `retirement_authorized` false.

## Task4: Integration and beta critical path

- [x] Extend actual eight-account outage/reconnect fixture with coherent copy/reopen and compare all original financial/control rows, unique closes and collection count. An exact-cut pair is necessary but does not certify whole-cohort/offline-claim recovery or independent anchors.
- [x] Freeze runtime/tests, run affected correctness checks and read-only integration review; preserve all15original prototypes and26draft sources. Update AST graph without paid labeling and keep progress estimate separate from profitability.
- [ ] Continue whole-cohort coordinator, independent cut/claim anchors, all registered roots/writers and30day operating-capacity proof. Then whole-release checks and reviewable Railway cutover/activation candidate. Day30 operational beta report precedes180day profit qualification; no profitable candidate is promised.

Review focus: snapshot while a source transaction remains open; destination identity swap during creation; same-timestamp lost nonce; capacity denial after first backup; source data changing during archive output. Each belongs to its owning task's real-file regression.

Evidence: `research/runs/production-archive-integration-v1/v2-pair-verification.json` (215checks,214pass/1Windows privilege skip,241pins) and `v2-pair-review.json`. All original15prototype/26draft fingerprints retained. Full-cohort integration, independent anchors, complete capacity and release readiness remain open. See `docs/PAPER_BETA_READINESS.md` for the newly observed primary-wallet feasibility dependency.
