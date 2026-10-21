# Paper publication implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Publish a pending one-variable paper candidate with immutable prior-version history, version checks and retry-safe evidence; complete the supervisor only after its receipt is verified.

**Architecture:** An existing-package helper runs on the paper worker's filesystem, under an exclusive SQLite publication lease. The request binds the review ID, experiment/owner policy, goal and base-strategy hashes, candidate, full review material and required writer-guard source hashes. A durable intent precedes the prior-byte archive and atomic active-strategy replacement. A durable receipt confirms the candidate hash. Retrying the same intent recovers an interrupted publication without repeating a model request; a changed base, goal, owner, release or conflicting archive refuses publication.

**Spec:** adaptive-paper-recovery-goal.md criteria1–4, global-review-staging.md and reflection-supervisor-control.md.

## Constraints and gates

- Paper/training runtime only. Tests use isolated directories and fake model commands; no deployment, remote invocation or paid call in this task.
- Remote helper requires the explicit external owner and source hashes matching the staged worker writer guards. The controlled release must reconcile the actual running worker with those files; matching files alone does not prove loaded runtime behavior.
- A configured external owner blocks the legacy multi-asset/fallback writer. A missing policy preserves existing behavior until release.
- The publication lease coordinates cooperating helpers on one filesystem. It cannot stop arbitrary manual/uncooperative writes; rollout must disable old mutation before activation.
- Revalidate that the candidate differs by exactly the declared variable and next version using existing bounds/max-step checks. Preserve fees, risk policy and open-position state; existing entry contracts need separate release validation.
- Archive original bytes without overwriting an existing differing version. Commit a publication intent before changing strategy. On retry, only exact base or exact candidate bytes are accepted.
- Keep historic hypotheses untouched. The publication ledger retains immutable complete review material and receipts; do not upload a local replacement hypotheses.jsonl.
- Complete the five-trade review only after verified publication, atomically with receipt evidence. Unknown outcome remains pending and blocks model calls.

## Tasks

- [x] Add failing publication tests for exact prior-byte archival, stale base/goal/owner/release rejection, conflicting archive, multi-variable tampering, paper-only mode, publication retry after interrupted receipt, immutable review identity and receipt-to-watermark completion.
- [x] Implement hermes_trading/paper_publication.py and helper CLI; add detached pending access and receipt-bearing completion to ReviewLease.
- [x] Add publish_pending_review coordinator around a caller-supplied transport, preserving pending on transport/receipt/ledger failure. Block legacy external multi-asset execution when policy is configured.
- [x] Run focused/full tests, independent review and graph update. Record staging/rollout limits.
- [ ] Next wire the reviewed helper through the watcher and reconcile the deployed release before activation.

## Implementation evidence

Seventeen focused publication tests pass, including a real helper subprocess against isolated test state. A committed remote intent retains the full request and exact original bytes. An immutable history archive precedes atomic strategy replacement; receipt commit follows verification of the active candidate hash. Identical requests recover after receipt failure without another replacement. Completed retries restore a missing prior archive from the intent and refuse a conflicting archive or diverged active strategy.

The local supervisor freezes the entire publication request before transport and reuses it after a lost reply. A false/missing/pre-review receipt cannot consume trades. An actual injected SQLite completion-write failure after successful publication keeps the review pending; retry verifies the existing receipt without another replacement, then commits the receipt and five-trade watermark together. The helper also revalidates model provenance, the decision's base hash, goal, owner and expected writer-guard source hashes. Tests reject coordinated candidate/review tampering and concurrent publishers after intent commit.

The configured external owner now blocks the legacy multi-asset reflection entrypoint before YAML/model work; the global coordinator remains the intended writer. Independent review identified coverage/binding gaps, which were reproduced and fixed. Follow-up scoped review found no additional blocker.

These are staged local preparations. Operational owner policy/ledger remain absent, the external maintenance marker remains active, and no source/helper deployment or real remote publication occurred. Matching on-disk fingerprints does not verify loaded worker code. The publication lock serializes cooperating helpers, not manual/uncooperative writers. Watcher routing, actual loaded-release reconciliation, existing-position contract verification, wallet accounting and market/cost checks remain before an adaptive/control paper launch.

Final verification:667full-suite tests passed in61.989seconds(session22496exit0). Graph64464exit0:3033nodes8592edges172communities. All verification handles are terminal; source hashes/test output preserved in research/runs/paper-publication-v1. Git diff whitespace checks passed.
