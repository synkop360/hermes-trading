# Missing historical fixture provenance — October 25, 2026

Read-only bounded review while the original full regression continues. No tests, network/provider calls, source/test/export edits, copies, process interruption or actual-state actions were performed. Only this review document was written.

## Conclusion

The missing `research/runs/approved-cost-reserve-v1/manifest.json` belongs to the **same 57 historical correctness inputs / 67,167 bytes** restored on October 23. All 57 present files in the main checkout, current correction checkout and original restored effective-stop checkout match the dated receipt's exact SHA-256 hashes. None of the 57 is tracked at candidate `a79fb93`, and none is present in the current frozen `hermes-snapshot-a79fb93` package. This is a reproducible source-packaging omission, not evidence that snapshot retry runtime behavior failed.

The old fixture restoration did not make ignored files part of later Git archives. Source identity alone therefore does not establish that a test package contains these dependencies.

## Authoritative evidence and scope

The original restoration record is in the main checkout:

`hermes-trading/research/runs/paper-effective-stop-fix-v1/full-regression/fixture-restoration-20261023.json`

Its raw SHA-256 is **`36e0ce92728b31dd7c42d4b4ce2b35ea845538055f2925ff4c602a72839b46e7`**. This hash was independently recomputed in this review and matches the `artifact_sha256` entry in adjacent `composed-offline-verification-20261023.json`. The restoration record provides every exact relative path, raw SHA-256 and byte count, and records main checkout → `hermes-effective-stop-proposals` at October 23 08:33:53 UTC. That provenance is a dated local file inventory; **it does not establish a Git source commit for the 57 ignored inputs**. The effective-stop commit `3e785e1` and approved parent `6c9d520` named by the composed result describe code, not tracked fixture blobs.

This review checked all 57 hashes at:

- `hermes-trading`: 57/57 match, 67,167 bytes.
- `hermes-paper-operational-corrections`: 57/57 match, 67,167 bytes.
- `hermes-effective-stop-proposals`: 57/57 match, 67,167 bytes.
- `hermes-snapshot-a79fb93`: 0/57 present.

`git ls-tree -r --name-only a79fb93` contains none of those 57 paths. No content substitution, newline normalization or regeneration was used in the comparison.

## Minimum complete known input set

| Exact path group | Files | Bytes | Consumer |
| --- | ---: | ---: | --- |
| `research/runs/approved-cost-reserve-v1/` | 26 | 22,809 | `VenueCandidateTests.setUp`: `manifest.json`, `authorization.json`, plus `strategy.yaml`, `goal.yaml`, `wallet_account.draft.json` in each of eight directories. |
| `research/runs/venue-refresh-2026-10-17/` | 2 | 9,635 | Same setup: `asset-pairs.raw.json` and `verification.json`; dated saved venue facts are parsed locally. |
| `research/runs/current-venue-execution-v1/candidate/` | 28 | 34,038 | `PrimaryCandidateTests.setUp`: copies the entire original disabled candidate. Exactly these 28 files exist, with no extra candidate files outside the receipt. |
| `research/runs/primary-wallet-protocol-v1/authorization.json` | 1 | 685 | Same primary-candidate setup: original implementation approval input. |
| **Total** | **57** | **67,167** | Complete union for the three known dependent modules. |

The eight directory names are `adaptive_50`, `adaptive_100`, `adaptive_150`, `adaptive_200`, `control_50`, `control_100`, `control_150`, `control_200`. The current-venue candidate contains those same 24 per-account files plus `manifest.json`, `authorization.json`, `venue-receipt.json`, `venue-revision.json`.

Static inspection identifies exactly the earlier eleven cases across three modules: six `test_venue_candidate` cases, two `test_primary_candidate` cases and three `test_current_venue_review` cases. Only the current-venue adaptation case uses the venue-candidate setup; its other two cases do not. The earlier composed result records nine missing-input errors and two overlapping passes in the eleven-case rerun. Those historical test outcomes were read, not rerun here.

## Bounded package correction recommendation

1. Leave the running full-regression session and its current frozen package untouched until its authoritative terminal result is collected. Retain this failed original package/run as evidence.
2. Use the dated 57-entry allowlist above. Verify every source file against its original hash and byte count before any future copy. Do not copy entire research trees, synthesize replacements, regenerate dated market data, refresh approvals, normalize newlines or change tests/checks to accommodate missing inputs.
3. Make these exact input files explicit durable package dependencies: preferably commit the 57 original ignored files intentionally, retaining the dated provenance inventory, then generate a separately named frozen export. Alternatively an explicit frozen test-input overlay must have its own full inventory/provenance and must not be described as an unmodified Git archive. In either case, preserve the existing candidate's program/test/config bytes.
4. A fresh package containing the prior 608 files plus these 57 distinct missing paths would contain **665 files**, before adding any additional provenance document. Verify that union and all original source pins rather than accepting a count alone.
5. Re-run the eleven affected cases on the corrected frozen package, preserving the current full-run errors separately. A focused rerun proves restored fixture composition only; it is not a single passing full suite. Any remaining failures require their own evidence and classification. Do not skip tests, relax deadlines or expand disabled archive engineering.

This restoration supplies original historical inputs for local correctness checks only. It grants no deployment, activation, strategy/risk change, actual-state migration or profitability authority.
