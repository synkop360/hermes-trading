# Independent correctness/spec review

Reviewed immutable range `6c9d520da5f8473b51c912d1eb0e269ee94d4b1b` → `79de870f55793d7fd176275fb001ce2f386785ed`, October 24, 2026. This reviews the disabled local prototype and synthetic-copy workflow, not an authorized live cutover. Source files were not edited during review.

**Disposition: three P2 correctness findings requiring correction.** No defect found in the one-line runtime filename correction. The implementation admits literal `+` without changing the surrounding inventory safeguards.

## R1 — Reject unknown importer column/constraint schemas before rebinding

**P2. Location:** `scripts/paper_release_transition.py:299–302` (`_component_specs`).

The importer check compares table names and rejects triggers/views, but accepts arbitrary column/constraint changes inside those tables. `_protected` then freezes that unsupported schema as legitimate original state. A completed transition can therefore leave the successor unable to perform a native renewal or usage import.

**Executed synthetic reproduction:** Create `NativeTransitionFixture`, execute `ALTER TABLE renewals ADD COLUMN unknown_extension TEXT` on its worker `model_access_refresh.sqlite`, then call normal `prepare_plan` and explicit `apply_transition`. Both accept the unchanged expected identity; apply returns `completed`. Advance the fixture clock by 60 seconds and call `f.renew_access(f.new_release)`. Actual output:

```text
unknown_schema_transition: completed
successor_renewal: OperationalError table renewals has 4 columns but 3 values were supplied
```

The native consumer declares its schema in `hermes_trading/model_access_refresh.py:107–108` and inserts three positional values at line 120. The usage consumer similarly relies on exact columns in `hermes_trading/supervisor_usage_feed.py:101–103` and positional inserts later in that function. Existing trigger coverage does not exercise this case.

**Required correction:** Validate each supported importer schema, including ordered columns, types, keys/constraints and unsupported schema objects, against the actual native schema before publishing intent or changing any binding. Retain the same rejection during application of a frozen plan. Add meaningful negative coverage for changed renewal/usage/binding schemas and assert inputs/audit remain untouched on rejection.

## R2 — Check launch identity against the retained experiment before preparing a plan

**P2. Location:** `scripts/paper_release_transition.py:314–318` (`_component_specs`, JSON branch); identity collection at lines 258–284 does not examine launch identity.

The launch component is admitted solely because its `release_hash` matches the old release. Its account, policy, experiment and primary fields are not compared with the approved identity. A launch file with the correct runtime hash but the wrong account binding can be incorporated into an accepted plan and receive a completed transition receipt. Native startup subsequently rejects it.

**Executed synthetic reproduction:** Create `NativeTransitionFixture`, change only `launch.json`'s `account_hash` to 64 zeroes before `prepare_plan`, then prepare/apply using the fixture's original expected identity. Actual output:

```text
wrong_launch_account_transition: completed
native_launch: ValueError primary account hash differs
```

The second line comes from `validate_launch(f.launch_file, f.project_root, f.new_release)`, whose account/owner checks are at `hermes_trading/supervisor_launch.py:46–49`. This violates the design's requirement to reject unexpected identities before writing, even though the native launcher provides a later protective refusal.

**Required correction:** Statically validate the launch schema, activated/enabled status, experiment, account, policy, primary selection and target relationships against the retained manifest/accounts/owner/limits before freezing its component. Validate corresponding identity fields on both supervisor-limit copies. Keep checks compatible with foreign-host reference copies: their path strings must not be interpreted as locally authoritative locations. Add pre-plan corruption tests proving refusal before audit creation or component updates.

## R3 — Preflight the complete reconciliation/rollback set before changing components

**P2. Location:** `scripts/paper_release_transition.py:579–584` (forward reconciliation loop), with the same ordering issue at lines 617–620 (rollback loop).

Reconciliation validates component states one at a time immediately before writing them. Since `_protected` intentionally masks release guards, a conflicting release value in a later component does not stop earlier components from being advanced. The eventual refusal has already enlarged the partially migrated state. Existing receipts are similarly validated only after the corresponding component operation.

**Executed synthetic reproduction:** Interrupt apply at `component_commit/worker_renewal`; change the last component's launch `release_hash` to 64 zeroes; retry with explicit `reconcile=True`. Actual states in component order:

```text
before_reconcile: [after, before, before, before, before, conflict]
reconcile_result: ValueError component changed before checked write
after_reconcile:  [after, after,  after,  after,  after,  conflict]
```

The ordering is worker renewal, worker usage, worker limits, PC limits, PC lifecycle, PC launch. Four additional components were changed despite an already observable conflict. This is distinct from a legitimate interruption after an otherwise admissible component commit.

**Required correction:** Under all native writer leases, preflight every component state and every existing relevant component/completion receipt against the intent, plan, root binding and direction before any forward or reverse update. Unknown/conflicting values must refuse with the entire component set unchanged. Keep the individual compare-and-set checks as well, since a preflight does not replace commit-time checks. Cover late conflicts and malformed receipts for both reconciliation and rollback.

## Scope, evidence and limits

Read the approved spec and implementation plan, all ten changed files, and the relevant native archive/publication, ownership, renewal, usage, lifecycle, launch, worker and snapshot consumers. All three reproductions ran locally with `../hermes-trading/.venv/Scripts/python.exe -B -` against newly invented `NativeTransitionFixture` state, using its mocked metadata/quote/clock boundaries. Each process exited successfully after reporting the demonstrated failure. No provider request, network call, deployment, installed-state migration, backtest or training was performed.

The full frozen candidate suite is being run separately by the parent agent; this review does not claim that suite result. Existing positive tests provide substantial native preservation evidence: original opening/accounts/manifest, eight scenario histories, completed old-release packets, lifecycle counters/deadlines, renewals/usage, safety latch retention, and daily snapshot plus synthetic off-host pull. Exclusive SQLite leases and sidecar-free immutable reads address real native writer ownership and read-only inspection. The rollback implementation changes only checked release bindings and refuses changed protected history rather than replacing current databases with old copies.

The two-host test is a useful independent-host-view composition test: each host operates on its authoritative local state plus a closed original reference of the peer. It does not prove transport, real simultaneous quiescence, live filesystem durability, genuine provider renewal, or release readiness. Its operator requirement to collect both scoped receipts and then validate actual native consumers remains necessary. Those limits are not defects in the authorized offline prototype, and a binding receipt must not be represented as operational acceptance or permission to resume schedules.

All findings above apply to `79de870f55793d7fd176275fb001ce2f386785ed`. Any later fixes need their own exact-diff verification and regression evidence.

## Re-review of fixed candidate — October 24, 2026

**Latest disposition: R1–R3 addressed; no remaining material correctness/spec issue identified in the authorized offline prototype.** This disposition applies to exact candidate `49c851315b98bb33b1eb78180fb789afe2a49f3d`, with fix diff `79de870f55793d7fd176275fb001ce2f386785ed` → that commit and whole candidate range `6c9d520da5f8473b51c912d1eb0e269ee94d4b1b` → that commit. It does not authorize deployment or live transition.

- **R1 addressed.** `_importer_schema` (`scripts/paper_release_transition.py:287`) now compares the complete native SQLite schema, including its indexes, against a freshly constructed native reference schema. Both component preparation (line 355) and frozen-plan validation (line 423) call it. Extra columns, altered constraints and unsupported objects can no longer be frozen as acceptable importer state.
- **R2 addressed.** `_static_supervisor` (line 303) verifies both supervisor-limit copies, account/policy/experiment/primary bindings, activation/deadline, launch status and retained path relationships. Preparation and frozen-plan validation invoke it. Pure Windows/POSIX paths allow checking an original peer's reference copy without treating its path strings as local filesystem authorities.
- **R3 addressed.** `_preflight` (line 589) validates the entire component state set, component receipt inventory and completion receipt contents before mutation. Forward reconciliation (line 670) and rollback (line 703) invoke it under the acquired native leases, while component compare-and-set checks remain in place. Completed replay also validates its retained receipts. Reverse work prevents an accidental forward reconciliation through the same audit.

**Verification reviewed:** `review-schema-red.txt` records three failing schema subcases before the fix; `review-launch-red.txt` records nine failing identity subcases; `review-preflight-red.txt` records three failing state/receipt cases. `review-fixes-green.txt` records all three regression tests passing in **52.162 seconds** after the correction. These are implementation-run logs reviewed here, not a second independent run of the full suite.

**Additional independent execution:** On the fixed commit, created a fresh synthetic native fixture, applied the transition, then injected an exception immediately after reversing `pc_launch` but before writing its rollback component receipt. A subsequent checked rollback completed, an identical rollback retry returned the same receipt, and all six components were back in their exact original states. Actual output:

```text
interrupted_reverse_commit_reconciliation: PASS
completed_reverse_receipt_replay: PASS
```

This check ran through the existing main virtual-environment Python, exited 0, and used only invented local fixture state. It checks that the new preflight does not break legitimate uncertain reverse-commit recovery.

Rechecked the full candidate's file scope and interactions with preservation, SQLite ownership, source/receipt conflict handling, rollback after new work, read-only inspection, and cross-host reference coordination. The sole runtime change remains literal-plus acceptance in `archive_snapshot.py`; no additional runtime behavior or provider boundary was introduced by the fixes. No source files were modified by this review.

The parent agent's full frozen Windows suite was still running separately during this re-review; its result is not asserted here. The earlier offline/transport/durability/provider limitations remain in force. Actual two-host quiescence, exact deployment/source maps, genuine native renewal when needed, preserved original opening and all-eight reports, healthy acknowledged supervision, completed native snapshot and verified E: copy remain separate live acceptance evidence after explicit approval.
