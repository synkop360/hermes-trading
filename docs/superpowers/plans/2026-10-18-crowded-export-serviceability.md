# Crowded V2 export serviceability

> **For agentic workers:** Use superpowers:executing-plans inline. This is a refinement of already approved archive implementation tasks2/5/6, not a new deployment or strategy scope.

**Goal:** Diagnose and remove avoidable work behind the original 30-second export failure without weakening storage accounting, source validation or deadlines.

**Architecture:** First measure the unmodified real exporter on disposable fixtures with retained synthetic files. Count actual complete inventory scans and their elapsed time. Only change the proven hot path, with correctness tests and before/after evidence; a local improvement is not full-horizon capacity qualification.

**Tech stack:** Existing Python/unittest/SQLite archive runtime; no services, market data or model calls.

**Spec:** 2026-10-16-archive-integration-implementation.md, tasks2/5/6; original V2 export/capture contracts and cohort-cut-first-green.log.

## Constraints and review focus

- Preserve original30-second export/120-second restore ceilings, artifact/nonce/domain bounds, retained exclusive owner and all registered roots.
- Preserve every source byte, all eight accounts, source fingerprinting and staging/publication checks. Do not exclude crowded retained evidence from inventory.
- Links/reparse paths, changing directories or owner, deadline/clock regression, out-of-envelope growth and disk-free loss must still stop the operation.
- No new primary selection, deployment, activation, paid action, real order or economic replay.
- Review repeated full scans versus read-only work, write admission/actual-peak enforcement, low-space behavior, source swaps and cleanup after timeout.

## Steps

- [x] Run `measure_crowded_pair_export.py` once: original0/256 retained-file cases take1.203/19.500seconds with97full scans each; scan time0.811/19.234seconds. Sources/retained files unchanged, both exports succeed, original30-second limit retained. This reproduces the scaling cause without claiming the earlier crowded failure disappeared.
- [x] Select the change: `CaptureBudget.progress()` performs only the original clock/deadline and live lease-owner checks. `check()` remains a full capacity measurement and delegates progress validation first. Read-only receiver/nonce capture checks use progress, preserving source stamps, exclusive handles and complete fingerprint validation. `backup_database` keeps full admission before creation, adds full checks immediately after the known-size SQLite backup and after final sync/hash. Its per-page source/target identity and size checks remain. Both capture contexts perform full checks at normal exit. Nonce recovery/acceptance checks, generic capacity semantics, ZIP write checks and publication checks remain unchanged.
- [x] Add failing behavioral/complexity checks: missing progress guard and100redundant scans during repeated read-only validation reproduced. The new3guard checks pass; original capacity/capture/export/recovery checks are included in full discovery.
- [x] Repeat the identical diagnostic with separate immutable output:0/256file exports0.688/7.328seconds,36full scans each. Source/retained bytes and original limits preserved. Full Windows discovery1451tests/1448pass/3platform skips,994.490runnerseconds,zeroResourceWarnings at244frozen sources. Independent scoped static review found no important issue. Full beta capacity/recovery authority remain unresolved.

The pending primary-wallet protocol is independent. Until the safety argument is established, this task may complete a diagnostic without changing runtime; it must not misreport a passing small fixture as beta aggregate capacity.

Safety argument: progress checks grant no write allowance. A backup has the original exclusive source, fixed source-page byte preflight, destination identity/size ceiling and admitted copy peak; full domain checks bound the beginning/end of that growth phase. Read-only row/hash work cannot spend additional storage. Other writers must still participate under the retained owner, as in the original cooperative capacity contract. No hard quota or safety against every uncooperative external filesystem writer is claimed. Tests must reject growth introduced during sync, an already overgrown domain before copy creation, expired/released/foreign ownership and corrupted original policy. Keep all original source-change/export/restore failure coverage.
