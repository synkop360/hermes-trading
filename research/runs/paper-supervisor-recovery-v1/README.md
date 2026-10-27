# Current supervisor-halt investigation — October 27, 2026

Read-only live diagnosis followed by owner-approved disabled local implementation on synthetic copies. No deployment, schedule change, model invocation or actual safety-state mutation occurred. Discord readability remains separately committed as c362196 and not deployed.

## Findings

1. The original entry halt is still latched. At 06:56 UTC the latest remote supervisor event is successful (06:55:36.971913 UTC), but `consecutive_errors=3` and `entry_halt=true`. This is intentional in `paper_safety._record`: success after latching preserves the count and halt. No existing checked recovery API was found in the prior source review or current source.
2. Errors continue after the snapshot repair. Local scheduler metadata reports **59 failed / 352 completed** invocations after the 17:11 UTC October 26 cutover, with a maximum two-failure streak. Since the later redeployment, it reports **42 failed / 257 completed**, maximum one-failure streak. The latest sampled job is enabled and successful; PC status is `not_due`, mirror current, zero model attempts reserved. These are invocation counts, not independent return observations or proof that every failure has the same cause.
3. An unchanged native snapshot retry call fails with **numeric SQLITE_BUSY (5)** at **07:00:43.778912 UTC**, after **0.703434821 seconds**. The cause chain reaches `supervisor_sync.make_snapshot_with_retry` → `make_snapshot` → `paper_publication.assert_no_pending_paired_batch` → `archive_quote_state.operations`. Thirteen preceding calls in that bounded observation validated. This establishes a present failure beyond the old three-attempt retry, without publishing a health event or invoking a model.
4. Source explains the contention opportunity: `run_paired_cycle` acquires the exclusive publication connection before asynchronous public-market collection and retains it through settlement. The deployed retry waits only 0.2 + 0.5 seconds. The observed rejection proves the lock outlasted that attempt sequence; it does not establish the full lock duration or prove that a proposed longer wait always suffices.

## Current runtime and limits

The current deployment is `43361182-b030-464b-ad88-18a3e039699a`, created October 26 at 20:57:19 UTC as a redeploy with the same recorded image digest as the prior deployment. Its initiating actor is not established by this inspection. Do not reuse the old deployment/instance identity for a mutation.

All 129 loaded runtime files match the approved 20eb6af map `38fe4175e5ea53c72a26923e173374e0d9355e90477dd21d53c0c62607a9c0e2`. Paper/simulated heartbeat is healthy; all eight accounts have zero orders and no positions. The known entry blocker is `supervisor_error_halt`. Native volume usage is 212,423,423 / 500,000,000 bytes, below the unchanged 350,000,000-byte threshold.

The first read-only cohort observation overlaps a normal settlement: its journal query receives BUSY and several wallet times advance ahead of the previously read heartbeat. Therefore that observation does **not** verify a coherent eight-account valuation or a new inception hash. It is not evidence of corruption, and it cannot authorize recovery. Later native snapshot probes run their own complete validations, but do not replace a complete recovery preflight.

Generic native launcher output still discards child stdout/stderr. Historical scheduler errors cannot all be assigned a cause from these records. No current financial return, successful LLM steering or profitable strategy is claimed. The existing original clocks, historical gaps and long-term goal remain unchanged.

## Evidence

- `20261027T065610Z-pc.json`: sanitized read-only scheduler/status/lifecycle observation; 5000-row bound, no command bodies or raw errors.
- `20261027T065610Z-runtime.json`: pinned-source read-only native runtime inspection; includes its racing/unavailable cohort results rather than substituting a passing receipt.
- `20261027T065755Z-snapshot-probe.json`: twelve native retry calls validated in a quiet observation window; no inference that the intermittent failure was fixed.
- `20261027T070014Z-snapshot-full-interval.json`: stops at the first reproduced typed BUSY rejection; 14 calls, 27.897 seconds. All source hashes verified before calls. No raw exception messages, provider data, credentials or health writes retained.
- `observe_pc.py`, `observe_runtime.py`, `probe_snapshot.py`, `probe_snapshot_full_interval.py`: bounded read-only observation code. The long probe extends only the operator observation window, not product retry or process deadlines.

## Approved local correction and remaining approval boundary

[Approved correction design](../../../docs/superpowers/specs/2026-10-27-supervisor-contention-and-halt-recovery-design.md): a ten-second, eight-attempt typed-BUSY retry within unchanged process deadlines, plus explicit checked and audited acknowledgment of the supervisor latch. The owner approved this design and disabled implementation. Retry correction is committed as 9029e44; recovery implementation is local and opt-in. [Ledger](implementation-ledger.md), [affected verification](local-verification.json), [operator guide](../../../docs/PAPER_SUPERVISOR_RECOVERY.md).

The audit retains intent, exact state transitions and completion/cancellation receipts. Pending recovery blocks entries while normal health recording continues. A new event prevents stale application; checked cancellation preserves newer health and keeps the supervisor halted. Native preflight verifies the original opening, all eight flat accounts, matching PC/worker releases, current settlement, market/model readiness, original entry checks and the verified daily backup under the shared publication lease.

Independent review is unavailable because the delegated reviewer hit an account usage limit. Local correctness tests are not a full-suite, deployed-reliability or profitability claim. The exact two-host release transition and concrete cutover remain to be prepared before requesting deployment/live-recovery approval. The installed PC and frozen package are checked read-only in [untouched verification](untouched-verification.json).
