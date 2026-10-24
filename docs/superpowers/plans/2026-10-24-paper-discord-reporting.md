# Paper Discord Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Restore automatic paper Discord reporting without delaying protection or adding a strategy writer.

**Architecture:** One optional asynchronous reporter receives bounded compact envelopes after native settlement or operational heartbeat publication. It alone owns HTTP, scheduling and small delivery metadata; existing financial records and leases remain authoritative and unchanged.

**Tech Stack:** Existing Python/asyncio, Decimal, httpx and unittest; no dependency installation.

**Spec:** `docs/superpowers/specs/2026-10-24-paper-discord-reporting-design.md`, approved by the owner: “Approve design and disabled local implementation.”

## Global Constraints

- Local implementation and synthetic checks only. No deployment/restart, Discord request, credential validation, paid call, market backtest, training or trading change.
- Preserve all eight scenarios, EUR200 primary, original opening/clocks/history, controls, strategies, fees and safety limits. Reporter failures never change financial health/admission.
- Existing runtime filenames only: `notifications.py`, `paired_paper.py`, `paired_worker.py`, `paper_boot.py`.
- Synchronous offer never awaits queue/network/marker IO; background sender only.16 events,128KiB/envelope,64KiB metadata,1900characters/part,4parts,10seconds/attempt,3attempts, at most2seconds post-worker shutdown drain.
- HTTP confirmation uses wait=true; no redirects or mentions. Success markers only after confirmed delivery. Timeouts are uncertain; duplicates/gaps disclosed.
- Raw model reviews remain unavailable; healthy supervision can mean not_due. Unknown costs remain unknown. No profitability qualification is implied.

## Review Focus

- Partial/recovery/final risk sweeps: notification events must match canonical final account records, not an earlier cycle result.
- Corrupt identity/cost/cohort or oversized/untrusted input: unavailable/degraded reporting, never a trading exception or invented zero.
- HTTP timeout,429, marker failure or cancellation: worker progresses, and delivery uncertainty survives truthfully.
- Resume/repeated batch: durable finite markers avoid repeated successful messages and disclose any uncovered interval.
- Quote/financial ownership: no transaction or live mutable state reaches the sender; release-map keys remain unchanged.

## Shared interface

`PaperDiscordReporter(state_dir, identity, settings, *, post_json=None, clock=None, sleep=asyncio.sleep, attempt_timeout=10)` lives in `notifications.py`. It exposes synchronous `offer(envelope)` and asynchronous `run()`. `paper_boot` owns task creation/cancellation and catches notification-only failures. Fake `post_json(url, payload)` is the only transport in synthetic checks; the production default must validate Discord's confirmed message response.

`identity` has `experiment_id`, `opening_at`, `opening_hash`, `manifest_hash`, `primary_wallet_eur=200`, and `account_hashes` mapping exactly adaptive/control50/100/150/200 to their original hashes.

Envelope v1 has `schema_version=1`, `kind` (`settled` or `operational`), the four identity fields `experiment_id/opening_at/opening_hash/manifest_hash`, `at`, `observed_at`, `batch_id`, `input_hash`, `completed_batches`, `status`, `entry_admission`, `model_ready`, `strategy_version`, `error`, and `accounts`. Settled `accounts` maps the eight names to `{account_hash, finalized, observed_at, observation, decisions, closes, orders, halted, liquidation_pending, event_source_status}`. `observation` is a compact native final wallet observation or null. Decisions are compact canonical `{decision_id,asset,action,reason}`; close/order dictionaries use canonical identities plus actual monetary fields. Root supplies the exact field mapping after inspecting domain/repository types; no fabricated monetary quantity. An operational envelope may have empty accounts and an explicitly old/null settled timestamp. It cannot count as a new financial batch.

## Task1: Isolated reporter, scheduling and transport

**Files:** Modify `hermes_trading/notifications.py`; create `tests/test_paper_notifications.py`.

**Interfaces:** Consumes the identity/envelope contract above; produces `PaperDiscordReporter` and its nonblocking `offer`/async `run`. Keep legacy helpers unchanged; reuse settings, period calculations and transport injection but not legacy percentage P&L or marker-before-send code.

- [ ] Write meaningful failing tests for disabled/invalid/cohort/missing-cost inputs, EUR accounting, mode filters, startup/5-batch/12h/day/week/heartbeat schedules and duplicate batches.
- [ ] Run `python -B -X utf8 -W error::ResourceWarning -m unittest tests.test_paper_notifications -v`; watch intended missing-feature failures.
- [ ] Implement bounded queue/latest slot, compact rendering, confirmed transport, per-part delivery markers, backoff/uncertainty, corruption isolation and cancellation.
- [ ] Add and run failure-focused tests: stalled delivery,429/permanent failure/timeout, marker failure, overflow/restart, split/mention bounds and healthy-but-not-due semantics. Expect zero failures/errors/ResourceWarnings; all transports fake.
- [ ] Commit only reporter/test files and evidence summaries; report exact constructor/envelope mapping to root.

## Task2: Native settlement and worker lifecycle integration

**Files:** Modify `paired_paper.py`, `paired_worker.py`, `paper_boot.py`; create `tests/test_paper_notification_capture.py`.

**Interfaces:** Native `run_worker(..., observer=None)` and `run_paired_cycle(..., notification_capture=False)` default to old behavior. Capture returns an ephemeral `notification` member when requested, containing final measurements and canonical exact-batch events. No notification-only member is persisted in completed packets or financial journals. Heartbeat generation returns its existing dict for a pure copied envelope. Reporter code owns no database handle.

- [ ] Write failing synthetic capture tests using existing native paired fixtures, including unknown cost, partial close and subsequent halt sweep; assert old persisted packets/opening/controls/history unchanged.
- [ ] Run the new module; observe intended absent-hook failures before production edits.
- [ ] Implement bounded event capture under existing account ownership and ephemeral final-observation handoff after full commit/connection close; isolate all capture/observer errors from worker health and admission.
- [ ] Write failing boot/task tests for disabled and malformed config, failed/stalled sender, continued worker/protective processing, and bounded cancellation; integrate optional reporter without legacy services.
- [ ] Run new capture module and affected paired/worker/bootstrap tests; expect actual terminals and preserved ResourceWarning discipline. Commit only owned source/tests.

## Task3: Composition, review and release candidate

**Files:** Add integration cases to the new modules and a dated verification/cutover document; do not change live state.

- [ ] Compose real native fixture, worker and fake reporter; verify eight settled rows, native cost/close data, deliberate sender stalls/errors, replay/identity checks and no extra writer or clock reset.
- [ ] Run both new modules and existing notifications/paired-worker/paper-boot tests, then appropriate frozen full discovery. Preserve the known disabled-archive baseline deadline limitation; restore exact historical fixtures where necessary rather than relaxing guards or assertions.
- [ ] Obtain independent source/patch review; reproduce and fix material findings with failing-then-passing checks.
- [ ] Update graph AST-only, verify unchanged runtime-map key set and raw bytes outside the four allowed files, commit a concrete candidate and prepare checked both-host transition plus bounded message-activation acceptance.
- [ ] Only after the candidate is concrete and verified ask separately for exact deployment/notification activation. No actual send during local work.

## Execution ruling

The owner has explicitly authorized independent work and this local implementation. No execution-method question is needed: delegate Task1 while root implements Task2 against the fixed interface, then perform Task3 and independent review. The existing linked worktree is already isolated from the actual PC runtime; reuse it on a new branch, preserving preexisting uncommitted cutover documentation. Shared interpreter is the main project's venv; no install or broad baseline rerun before the relevant existing baseline checks. Preserve all evidence and do not delete the plan workspace.
