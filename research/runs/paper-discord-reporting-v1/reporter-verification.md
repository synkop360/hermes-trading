# Isolated paper reporter verification

Approved scope: disabled local implementation and synthetic checks only. No deployment, restart, Discord request, credential validation, provider request, paid call, training or backtest was performed by this task. Legacy notification unit fixtures print boot messages for mocked training-mode runners; they do not run training.

## Interface and native mappings

`PaperDiscordReporter(state_dir, identity, settings, *, post_json=None, clock=None, sleep=asyncio.sleep, attempt_timeout=10)` exposes synchronous `offer(envelope)` and asynchronous `run()`, plus notification-only `enabled`, `status` and bounded counters.

Eight native names are `adaptive_50/control_50`, `adaptive_100/control_100`, `adaptive_150/control_150`, `adaptive_200/control_200`. Identity binds original experiment/opening/manifest/account hashes and EUR200 primary. Settled emission time may follow its aware observation time. Account row time/hash and native `observation.at/account_config_hash` must agree. Unavailable/mismatched observations stay unavailable. Final native EUR fields are used directly; neither percentage P&L nor another fee subtraction is used.

Closes use `(account, asset, position_id, closed_at)`, actual quantity and `realized_pnl_eur`. Orders use `(account, order_id)` (the standalone synthetic canonical-id alias is also accepted). Native event source is `observed`; unavailable or truncated sources cannot claim a complete event stream. Model readiness/access is explicitly separate from review audit evidence; healthy can mean `not_due`.

## Actual evidence

- `reporter-red.txt`: 10 initial tests, 12 missing-class assertion failures (mode subtests explain the additional failures); production was untouched until the root baseline completed.
- `reporter-first-green-attempt.txt`: 9 initial tests passed, one case-sensitive snapshot wording assertion failed and was corrected.
- A first combined failure-focused run was terminated after reproducing a cancellation race in `asyncio.wait_for`; its redirected output was not flushed (`reporter-failure-red.txt`). The direct subsequent terminal run showed the expected multipart-header failure. The sender now uses `asyncio.timeout` around its wake wait.
- `reporter-contract-red.txt`: native emission/order contract, secret redaction, conflicting replay and corrupt schedule regression failures before fixes.
- `reporter-bounds-red.txt`: native observation identity/time and oversized-detail failures before fixes.
- `reporter-startup-red.txt`: root review reproduced permanent startup suppression after three transient failures. The startup delivery identity is now bound to the current snapshot; retrying the same abandoned snapshot remains bounded, while a later distinct snapshot can resume.
- `reporter-green.txt`: 27 native reporter tests passed in 4.473 seconds, no failures/errors/ResourceWarnings.
- `reporter-regression.json` / `reporter-regression.txt`: 56 legacy plus native reporter tests passed in 7.000 seconds, zero failures/errors/skips. `reporter-verify.py` denies both raw HTTP transport methods across the entire run; injected callbacks and `MockTransport` are the only transports.

Tests exercise native monetary rows/comparisons, missing costs/null data, all three mode filters, canonical close deduplication, startup/five-batch/UTC 12h/daily/weekly/fallback liveness schedules, non-financial state changes, thread-preserving confirmed webhook transport, mention suppression, at most four parts of at most 1900 characters per delivery intent, partial confirmations, 429 backoff, permanent refusal, three timed attempts, cancellation uncertainty, corrupt/unwritable bounded metadata, overflow/coalescing, restart markers/counters and event-loop progress while disk IO stalls.

## Recorded implementation rulings and limits

- An input may populate both the event queue and current-summary slot. Event outcomes and due snapshots are separate delivery intents, each limited to four parts; root accepted this interpretation during integration. Excess event lines are counted and disclosed.
- All marker IO runs via `asyncio.to_thread` from the sender task with a single outstanding writer. `offer` performs bounded memory-only copying and never creates tasks or touches disk. Cancellation performs at most 0.4 seconds of best-effort metadata coordination and holds no financial lease. Python cannot cancel an OS filesystem call already in progress; a marker operation still pending disables further reporter writes. It does not acquire a financial resource.
- Stable finite markers and explicit gaps/uncertainty do not promise exactly-once delivery. An unconfirmed dispatch can duplicate after retry or crash. Corrupt metadata disables the reporter rather than replaying history.
- Arbitrarily long numbers/details are rendered unknown/truncated within bounds; all eight rows and four comparisons remain present. No missing cost is invented as zero.
- Full frozen repository verification and the AST-only graph update belong to root's composition/release task. This isolated task only stages its owned reporter, tests and `reporter-*` evidence; existing cutover documentation and other runtime files are left to root.
