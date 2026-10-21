# Global review staging implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Connect the durable five-trade ledger to one strict pinned-model decision, preserving its inputs and response, and stage at most one bounded change for version-checked publication.

**Architecture:** Add a focused global reflection coordinator reusing ReviewLease, ReflectionModelRunner and the existing strategy proposal validator. It performs exactly one model request globally, with the current strategy, goal and last 25 distinct causally available closed trades. A confirmed no-change or rejected proposal completes the review; a validated changed candidate remains pending for the publisher. No candidate is written to active strategy.yaml in this task. Global review will replace the old multi-asset entrypoint in the controlled release, not become another concurrently enabled supervisor.

**Spec:** docs/superpowers/plans/2026-10-09-adaptive-paper-recovery-goal.md criteria 2–4; strict-review-model.md and reflection-supervisor-control.md.

## Constraints

- No actual model requests, deployment, operational policy initialization or strategy changes during this task.
- Validate strict runner identity and payment authorization before creating a pending attempt.
- Commit pending identity and request inputs before dispatch. Capture the response before interpreting or validating it. A capture failure leaves an unresolved pending request; do not automatically retry an uncertain request.
- Never call per-asset generators or deterministic accessibility overrides. Exactly one changed variable means exactly one proposal item globally.
- Reuse existing bounds/max-step checks; reject inert simulated position_size_r and changes that do not change the effective value.
- Preserve goal/risk limits and entry contracts. Candidate publication, existing-position contracts and immutable experiment bounds still require release validation.
- An already pending review blocks further calls. Failed known requests retain eligible trades and existing backoff. Proposed changes cannot consume the trade window before publication succeeds.

## Tasks

- [x] Extend the ledger to record immutable request/response/decision material transactionally with the pending audit record.
- [x] Add failing coordinator tests: five unique closes trigger one global call; no-change persists material and consumes the window; one change stages without mutating active strategy; multi-change/mismatched provenance/bounds reject; failure/backoff and crash-pending block duplicates; future trades cannot enter the prompt.
- [x] Implement hermes_trading/global_reflection.py using existing proposal validation and strict runner.
- [x] Run focused/full tests, independent review and graph update. Record release limitations.
- [ ] Next implement version-checked publication and route the existing watcher through this coordinator; this release integration remains outstanding.

## Verified implementation details

Sixteen coordinator tests pass, including a newly initialized wallet without any trade journal. One strict model request covers all configured assets; the durable request retains the current strategy/goal hashes, prompt, and last25distinct causally available closed trades. No-change and rejected decisions consume only the captured new-trade identities. A valid one-variable candidate is stored inside the pending audit record, with an incremented version and base strategy hash; active strategy.yaml remains unchanged until publication.

Changed strategy/goal bytes during the request require reconciliation. Interrupted request capture blocks dispatch; interrupted response capture blocks duplicate requests. Raw malformed output and timeout fragments survive parsing failures. Review material is detached and cannot be overwritten. Noncanonical variable paths, multiple changes, bounds violations, inert paper sizing changes and effective no-ops are rejected.

Independent review findings on inert-variable normalization and raw-response loss were reproduced and fixed. Combined accounting/cleanup failures that could mask uncertain requests were reproduced and fixed. The final scoped review found no remaining blockers. This coordinator is not yet selected by the legacy reflection CLI/watcher. Model approval/access, version-checked remote publication, prior-version archives, existing-position contracts and wallet metrics remain release gates; maintenance stays active.

Final verification:650full-suite tests passed in61.921seconds, session22474exit0. Graph18337exit0:2993nodes,8445edges,166communities. Source hashes and test output are preserved in research/runs/global-review-staging-v1. No actual model, deployment or order action was performed.
