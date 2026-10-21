# Offline Archive Lifetime Checkpoint Plan

> **For agentic workers:** Execute inline under the user's approved archive design/offline prototype scope. No production code or live state changes.

**Goal:** Prove that a verified archived prefix plus retained tail preserves lifetime batch counts through compaction, restart, retries and interrupted transactions.

**Design:** Bind a disposable primary database to a validated archive manifest and hash-matching restored identity/config/shared database. Store the retired prefix ordinal/count, archive digest, prefix-record digest, account/manifest identity, observed lifetime head and checkpoint revision in the same SQLite transaction that removes completed prefix rows and settlements. Count requires a contiguous retained suffix, original overlapping rows matching archive evidence, available archive binding and a nondecreasing observed head. The initial journal must begin at ordinal1; gaps are errors, not a new experiment. A trusted caller floor/revision rejects rollback of a previously observed count/checkpoint. This is a laboratory reader, not installed production _stats.

**Source mapping:** new research/runs/offline-archive-lifetime-v1/lifetime_checkpoint.py, test_lifetime_checkpoint.py, verify_real_fixtures.py; use the existing reviewed archive codec unchanged.

**Constraints:** synthetic/disposable copies only; preserve all eight account artifacts, strategy/entry history, owner/model/elapsed-time/risk/review limits; no network/paid calls/economic backtest/deploy/activation. Retiring shared rows on these copies does not authorize production retirement or certify backup durability. Existing _stats must remain unchanged and still demonstrate the earlier mismatch.

## Tasks

- [x] Red/green tests: initial/retired/appended/repeated counts, unknown/gapped/rebound tails, missing/tampered archive/identity/checkpoint, pending batch rejection, caller rollback floors, and transaction fault points before/after deletion/checkpoint/commit.
- [x] Implement bounded known-fixture evidence binding and atomic checkpoint retirement. Exact covered source rows bind IDs/input hashes/payload/completed/settlement bytes; count validates each retained packet input hash. Existing complete records are immutable.
- [x] Bind real original/reserve synthetic archives, checkpoint fresh complete journal copies, append one identical invented batch to reference and checkpoint branches, reopen and repeat retirement. Verify lifetime counts and finance/inputs/risk against uninterrupted references. Do not claim a deployed native restart.
- [x] Review, resolve important findings, record evidence/fingerprints and refresh AST graph. Report remaining native integration, populated review/closed-lineage, global capacity, durability and launch gates.

## Scope of rollback claims

SQLite transaction failures must leave either the old complete journal or the new checkpoint+tail. A crash after commit but before acknowledgement must be safe to retry. Arbitrary rollback of both the journal and every external trusted observation is outside this prototype; independent durable recovery remains required. Caller-provided floors model the existing lifecycle witness and are not fabricated financial history.


Completed laboratory scope:12checks pass, two real synthetic eight-wallet archives preserve129/3 lifetime counts after repeated retirement, and an additional paused native-worker fixture with a temporary test adapter consumes exactly its one remaining cycle, preserves deadline/config/limits and collects none on second restart. No installed adapter or transport/durability/capacity claim. Evidence: research/runs/offline-archive-lifetime-v1/verification.json.
