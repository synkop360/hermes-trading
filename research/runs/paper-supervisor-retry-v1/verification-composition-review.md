# Snapshot repair verification composition review

Read-only review on October 26, 2026. No tests, services, network, models, product source, test source, frozen exports or operational state were run or changed. Only this review was written. Project `AGENTS.md` and the existing graph were consulted first; the graph lacked the terminal run detail, so the saved receipts, catalogs, outcomes, runner text, Git blobs and raw file bytes were inspected directly.

## Conclusion at this review boundary

The saved evidence supports **1,707 distinct Windows-passing cases from the original 1,712-case catalog**, composed from the failed original full run and the successful restored-input rerun. The other five cases are Windows platform skips. Linux v4 is terminal **15 passed / one failed**, so those five cannot yet all be described as resolved. A corrected Linux harness result and the parent's separate resource-warning investigation remain pending at this boundary.

The historical input repair has exact, durable provenance, and the new frozen package preserves all 608 original inventoried file bytes. No source/data identity gap was found in the bounded composition. This is suitable evidence for a concrete **draft proposal with explicit outstanding qualifications**, not a claim of a single fresh passing full suite, deployment approval, live installation, recovery of historical continuity, or entry resumption.

## Catalog and outcome accounting

| Saved run | Catalog / executed | Pass | Error | Failure | Skip | Unrun |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `verification/full-frozen-windows-v1` | 1,712 / 1,712 | 1,698 | 9 | 0 | 5 | 0 |
| `verification/restored-fixtures-windows-v1` | 11 / 11 | 11 | 0 | 0 | 0 | 0 |
| `linux-verification-v4.json` | 16 executed | 15 | 0 | 1 | 0 | Not separately cataloged |

Both Windows catalogs contain unique IDs and exactly equal their respective outcome-key sets. The restored eleven IDs comprise **all nine original error IDs plus exactly two original passing IDs**; no new or missing IDs occur. Their union therefore yields 1,698 + 11 - 2 = **1,707 distinct passing IDs**, not 1,709 or 1,723. The eleven are exactly six venue-candidate, two primary-candidate and three current-venue-review cases, as confirmed against the unchanged test source. All nine original tracebacks terminate in `FileNotFoundError` while reading/copying the absent historical fixtures.

The five original skipped IDs are:

- `test_archive_capacity.CapacityTests.test_added_symlink_or_reparse_storage_blocks_before_stage`
- `test_archive_store.ArchiveTests.test_fifo_at_object_or_manifest_is_rejected_without_blocking`
- `test_archive_store.ArchiveTests.test_source_or_destination_symlink_not_followed`
- `test_paper_safety.PaperSafetyTests.test_volume_symlink_is_not_followed_and_reports_unknown`
- `test_snapshot_plus_names.SnapshotPlusNameTests.test_trailing_dot_guard_remains_on_filesystems_that_preserve_it`

Linux v4 selects those five plus the same eleven restored cases. Its FIFO test fails because its child, started with the tests directory as working directory, cannot import `hermes_trading`: the harness inserts paths into only its parent interpreter. This records a harness failure, not a demonstrated FIFO product defect, but remains a failed run. The other fifteen report passes. A future terminal corrected harness run must preserve the same product/test bytes and demonstrate all sixteen, including the exact five skip IDs, before closing that gap.

The original full run is terminal `status: failed`; its one implicit `TemporaryDirectory` cleanup `ResourceWarning` remains part of the result. The restored run explicitly forces garbage collection inside its warning-capture context and records zero warnings. That focused result does not by itself attribute or erase the original warning. Attribution remains qualified pending the parent's negative-control evidence.

Full/restored Windows logs and runners match their recorded SHA256 values. The earlier Windows20, Linux21 and Linux v4 logs also match their receipts. Earlier Windows20/Linux21 results substantiate the unchanged new program paths; they do not replace the catalog accounting above or the outstanding FIFO evidence.

## Source and package identity

Independently recomputed from raw bytes:

- All 608 original inventoried paths exist unchanged in both `hermes-snapshot-a79fb93` and `hermes-snapshot-20eb6af`.
- The new tree contains exactly 667 files, no extras, and every file matches its inventory. The sibling inventory raw SHA256 is `4d97c0d3ad666912846ed3fd60d16f7b6d6cdb2ca98f28021ebc4fc1c740c990`; the saved `candidate-package-v4.raw-sha256.json` has the identical mapping.
- The 59 added paths are exactly the 57 historical inputs, `.gitattributes` and `research/runs/paper-supervisor-retry-v1/historical-fixture-inventory.json`.
- The original full-run 376 source pins are an exact subset of the old 608-file raw inventory; the restored-run 667 source pins equal the new inventory. Both results record no changed pinned sources.
- All 667 export files match commit `20eb6af2a441c934618e124af30d72f411a3938a` after the documented CRLF-to-LF comparison. The export deliberately preserves prior raw bytes: 306 files differ from their Git blobs only in line endings. It must not be called a byte-identical ordinary Git archive or substituted with a normal checkout.
- The actual 129 Python runtime paths/hashes equal the new release-plan map. The sole runtime delta from the pinned deployed predecessor is `supervisor_sync.py`. Watcher/native bytes and hashes are unchanged; the helper raw SHA256 is `306da355b372cf0048b0192e7ec475d1c70740a5f60eb7ebe97b177d1c3b8ccf`.
- Canonical recomputation gives successor release `b3f89fa5028578f3c29734ccca1dca855fa652d20f75345e7765ca3635ac64c7`, runtime-map `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2`, and release-plan raw SHA256 `17a768982074206fb77254cb9e792b2c03ae541aa0272348525b47f51190baf0`.

The old export directory now also contains 71 non-inventoried files: 51 runtime bytecode files and 20 test scratch files, for 679 actual files. Its 608 original inventoried files remain unchanged. Consequently use **all 608 original raw files unchanged / all 376 full-run source pins unchanged**; an unqualified claim that the complete old directory never changed is unsupported. No extra-file cleanup was performed.

The Git difference from the program base `a79fb93` to `20eb6af` spans 108 paths because intermediate evidence/documents were committed too. The relevant program/test identity is established by the exact 608-file subset comparison; do not replace that statement with an inaccurate claim that only 59 Git paths changed.

## Historical dependency closure and provenance

The 57 original inputs total exactly 67,167 bytes: 26 approved-cost-reserve files, two dated venue-refresh files, 28 current-venue candidate files and one primary-wallet authorization. These match the static reads/copies in the three dependent modules. Searches found those direct historical input references only in the venue/primary fixtures and the current-venue test's reuse of the venue fixture. This establishes the bounded known fixture dependency set, not universal absence of any possible full-suite interaction.

Every added input's raw hash and byte count matches the dated October23 receipt and its committed `20eb6af` Git blob. None of the 57 is tracked at `a79fb93`; all are tracked at `20eb6af`. The original receipt and the committed inventory both recompute to raw SHA256 `36e0ce92728b31dd7c42d4b4ce2b35ea845538055f2925ff4c602a72839b46e7`. The frozen attributes contain 58 exact paths covering those 57 files and that inventory. This preserves dated saved inputs; it does not establish a historical Git origin for the formerly ignored files or confer current activation authority.

## Required qualification in the cutover proposal

`docs/PAPER_SUPERVISOR_SNAPSHOT_REPAIR_CUTOVER.md` still describes the full run as nonterminal at 1,275 cases/three errors, the restored eleven as pending, and source invariance as unobserved. Those statements are stale and should be replaced with the terminal/composed accounting above and, when available, the separately verified corrected Linux and warning-attribution evidence. Preserve the failed original full result and failed Linux v4 result explicitly.

Live deployment/process/guard/schedule/backup state, pending work and positions, free-model admission, fresh closed copies, transition intents/root bindings and post-cutover behavior remain execution-time unknowns. This review neither observes nor authorizes them. The retained `supervisor_error_halt` and absence of entry recovery must remain explicit. No additional product or archive engineering is warranted by this composition audit.
