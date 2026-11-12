# V2 cohort recovery cut implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline with TDD. This refines already approved archive tasks2/4/6; preserve the dirty candidate, previous sources and evidence. No deployment or live operation.

**Goal:** Verify one exact full worker/supervisor snapshot together with a restored V2 receiver/nonce pair and the original cached offline protective claim.

**Architecture:** Recompute original cohort/financial/review certificates and validate both restored receiver/nonce copies against caller-pinned artifact/certificate bytes. Reuse the live session's canonical cache and claim validators, check the cached head is an actual ancestor in the selected receiver chain, and dry-run the original source transition. Return a deterministic cut record and digest; verification requires a caller-retained expected cut digest. This is local cut coherence, not an independent backup, latest-cut service, stopped-owner fence or runtime-resumption authorization.

**Tech stack:** Existing Python/sqlite3/unittest and retained capacity ownership; no dependency or paid API.

**Spec:** October16 approved `2026-10-16-archive-integration-implementation.md` tasks2/4/6 and `2026-10-18-v2-export-recovery-contract-notes.md`.

## Constraints and rulings

- Existing archive approval and autonomous instruction authorize this disabled internal composition. Do not request approval again for successive local steps. Original full objective stays active.
- Preserve all eight accounts, original7-field identity, legacy primary100 protocol, review cadence, fees, strategy histories, risk caps, cycle/deadline limits and all original evidence. No primary-wallet change is part of this task.
- Require exact caller pins for cohort inventory, V2 archive, certificate, receiver head and external nonce generation. A later external cut cannot be inferred from restored files.
- No source snapshot or pair is modified. Retain domain ownership and admit nonce recovery allowance before opening SQLite. All inspected paths belong to the registered domain. Aggregate inspected bytes are finite and at most the original128MiB cohort bound; use one absolute deadline at most120seconds and original10second source-view ceiling.
- Revalidate complete inventories and sealed input hashes after fault hooks and before returning. Accept no extra pair member, forged completion marker, mismatched chain ancestor, missing original claim packet, changed original file or stale externally pinned cut.
- Offline activity after the selected cut remains unknown. Always return independent_backup_verified=False, post_cut_activity_reconciled=False, runtime_resumption_authorized=False and retirement_authorized=False. This cut must not become startup authority.

## Task1: Shared original claim validation

- [x] Write missing-helper tests proving canonical checksums/types and lost-claim rejection on real worker state.
- [x] Extract existing session cache and claim checks into shared pure functions; live session uses the same functions with unchanged semantics. Keep ownership/authentication in the live session.
- [x] Run original session tests plus new helper tests; verify exact previous behavior.

## Task2: Full-cohort exact-cut inspection

- [x] Write native eight-wallet outage tests that prepare a real separate supervisor, coordinated full capture and V2 pair restore, then call missing `inspect_cut`/`verify_cut` APIs.
- [x] Implement disabled `archive_recovery_cut.py`: bounded registered input inventory; exact artifact/certificate/member hashes and original V2 structural checks; recomputed full cohort certificate and original review reconciliation; worker cache plus authentic receiver ancestor; original source view and claim reconciliation; deterministic original-state dry-run proof.
- [x] Compare the selected cohort journal's complete/checkpoint point with its financial certificate. Bind all original evidence and unresolved claim to the cut digest without recording the verifier's wall-clock execution time in the cut identity.
- [x] Verify stored expected cut SHA against recomputed evidence; reject stale/mixed/tampered artifacts even where receiver head and nonce time are equal. Keep all authorization flags false.

## Task3: Verification and preservation

- [x] Run actual native outage coherence, selected-cut rejection, claim loss, unchanged full cohort/nonce bytes, post-inspection mutation, budget/deadline and disabled checks. Historical fixtures may use invented fees and quotes; they are correctness inputs only.
- [x] Freeze changed runtime/tests; run affected archive/session/coordinator/account/supervisor checks and one fresh read-only integration review. Preserve241previous source map and15original prototype/26draft fingerprints. Refresh AST graph.
- [x] Record this limited result and remaining external latest-cut/owner-fence/transfer/coordinator/all-writer/capacity/release gates. Do not mark the full goal or full recovery complete.

Scoped verification: `cohort-cut-verification.json`,288checks/287pass/1Windows privilege skip,624.375seconds,243pins,zeroResourceWarnings. These checked items cover local composition only. All external lifecycle/owner-fence/integration/total-capacity/full-release requirements above remain unresolved. Static review receipt `cohort-cut-review.json`.
