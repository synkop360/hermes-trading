# Strict review model implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Provide the fixed-model execution contract required by the approved adaptive paper recovery goal, without issuing any model calls during implementation.

**Architecture:** Extend the existing ReflectionModelRunner with an explicit strict pinned mode. Existing callers retain their behavior. A strict caller validates one explicit provider/model and payment authorization before dispatch, respects that model's cooldown, and never enters automatic selection, provider rotation, local fallback or OpenAI fallback. Usage written by the Hermes subprocess is recorded even if the subprocess fails; missing usage remains unknown.

**Tech stack:** Python stdlib, existing ProviderRouter and CostLedger, unittest with fake commands.

**Spec:** docs/superpowers/plans/2026-10-09-adaptive-paper-recovery-goal.md, criteria 2–4; docs/superpowers/plans/2026-10-09-reflection-supervisor-control.md activation limits.

## Constraints and review focus

- No operational model call, paid service, deployment or real order.
- Strict mode requires a nonempty explicit provider/model and literal boolean authorization; names such as auto cannot establish a pinned experiment.
- A confirmed free candidate can be invoked by strict mode; a paid candidate requires separate explicit authorization. Catalog labels alone do not establish actual free access for release.
- Failure or malformed output cannot trigger another provider. Cooldown must block dispatch before a command starts.
- Cost evidence emitted during a failed request must be preserved. An absent usage file must not be reported as an actual zero-cost successful call.
- This adds an execution primitive, not a global review or safe remote publisher. External maintenance remains active until all integration gates pass.

## Task 1 — fixed-model dispatch

- [x] Add failing tests for strict no-fallback failure, absent/opaque pin rejection, paid authorization rejection before dispatch, successful explicit authorization, strict cooldown and successful fixed-model provenance.
- [x] Implement strict_pinned and paid_calls_authorized controls in hermes_trading/model_runner.py using existing router/cooldown behavior.
- [x] Run tests/test_model_runner.py and preserve legacy tests.

## Task 2 — failed-call cost evidence

- [x] Add a test in which the fake subprocess writes usage and then raises, requiring the cost event to survive without fallback.
- [x] Record usage in the subprocess cleanup path without inventing unavailable prices or usage.
- [x] Run focused/full tests, independent code review and graphify update. Record inactive deployment status and outstanding global-review/publication integration.

## Verified implementation details

Twenty runner tests pass. Strict execution rejects unapproved paid candidates, opaque pins and non-boolean controls before dispatch, observes pinned cooldown, and stops after one failed or malformed response. Existing legacy fallback behavior is preserved until strict execution is explicitly selected.

Raw stdout/stderr are captured through a durable callback before parsing; subprocess timeout/error output is retained when available. Capture failures have a distinct uncertain-request classification. Combined capture/accounting/cleanup failures preserve that classification so the supervisor cannot silently clear a pending request and repeat it.

Missing, malformed, nonobject, boolean or negative token reports produce explicit missing cost evidence. A failed accounting write preserves the temporary usage file and reports its path. Independent review findings were reproduced and fixed; the follow-up review found no scoped blockers. No operational model call or paid authorization was granted by these tests.

Final verification:650full-suite tests passed in61.921seconds, session22474exit0. Graph18337exit0:2993nodes,8445edges,166communities. All verification handles are terminal. Operational owner policy and initialized ledger remain absent; watcher maintenance remains active.
