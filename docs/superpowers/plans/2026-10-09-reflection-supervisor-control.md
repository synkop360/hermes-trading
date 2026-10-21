# Reflection supervisor control

## Design and scope

Implement the first recovery deliverable: an explicit strategy-writer policy and independent LLM review ledger. This is local preparation; the external watcher maintenance marker stays active and no model, deployment, strategy or remote configuration action occurs in these tests.

`state/reflection_owner.json` will identify one owner (`external_llm` or `deterministic_worker`), an experiment ID, five-trade cadence and pinned provider/model. A missing policy preserves existing behavior for compatibility until the reviewed release; it is not evidence that ownership has been enforced. A configured policy rejects the wrong reflection actor before model calls or strategy mutation, including the fallback CLI path. The paper release must disable the old remote deterministic writer before external supervision is enabled.

`hermes_trading/reflection_supervision.py` will keep `llm_review_state.sqlite` as a durable supervisor-only ledger. Explicit initialization establishes the experiment activation time; it never derives the review watermark from `hypotheses.jsonl`. Count distinct closed-position identities after activation, not repeated rows, open positions, future timestamps or generic diagnostics. Review eligibility is five newly closed trades globally. Completed review IDs preserve the input snapshot, outcome and pinned-model provenance.

A SQLite exclusive lease serializes cooperating supervisors on the same state directory. Pending review is committed before a model request; a process crash leaves it unresolved and prevents automatic duplicate calls until reconciliation. Failure keeps trades eligible and adds bounded retry spacing; only a confirmed changed/no-change/rejected LLM review consumes its captured trade IDs. Trades arriving during a review remain unreviewed. This ledger alone does not prevent a second host from overwriting remote state; remote compare-and-swap/append-only publication and one-owner configuration remain required before release.

## Implementation tasks

1. Add focused tests for ownership rejection, deterministic logs not consuming reviews, five unique closes, duplicate/missing/future identities, no-change completion, failure/backoff, pending-crash behavior, competing leases and late trade arrivals. Observe failures before implementation.
2. Implement the policy/ledger with stdlib SQLite, exact schema validation and crash-released leases. Verify explicit initialization cannot reset an existing experiment.
3. Wire owner checks into both reflection entrypoints and mark the worker's deterministic actor explicitly. Preserve default compatibility until configured; do not change strategy selection or data/state in this task.
4. Run related/full tests, independent review and AST graph update. Record local-versus-deployed limits. Next integrate the global one-variable LLM review and atomic remote publication; do not activate the external watcher until all release gates are met.

## Implementation evidence — October 9, 2026

The staged owner checks and supervisor ledger are implemented. Nineteen focused tests pass, including an actual subprocess exit without cleanup, lease contention after the pending request commits, retention of detached trade inputs, conflicting duplicate rejection in both input orders, failed writes at preparation/completion, both reflection entrypoint guards and training-worker dispatch for each owner.

Injected SQLite failures initially reproduced split audit/watermark writes: a completed audit record could coexist with a pending counter, or a second request could be orphaned. Every ledger transition now explicitly commits the audit record and supervisor state in one transaction and updates in-memory state only after commit. On write failure, rollback and a durable-state reload retain the unresolved request or eligible trade window. Canonical input snapshots, not only their hashes, survive source changes and restart.

Independent review identified missing snapshots and conflicting duplicate payloads. Both were reproduced and fixed; the second review found no remaining blocking defects within this module's scope. These checks do not qualify performance or prove deployed ownership.

No operational owner policy or initialized supervisor ledger has been installed. The external maintenance marker remains active, and the remote older worker still owns its deterministic changes. Next work must connect this ledger to one global LLM proposal and versioned publication. The current model runner permits local/OpenAI fallbacks even with a pinned candidate: provenance checks after a call do not prevent an unauthorized paid call or establish fixed-model execution. A strict, approved-provider call path must precede activation. Remote compare-and-swap publication, wallet metrics, market reconciliation and prospective adaptive/control evidence remain outstanding.
