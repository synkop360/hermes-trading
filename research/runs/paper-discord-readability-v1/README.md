# Local Discord readability change — October 26, 2026

Formatting only, requested by the owner before the next notification deployment and refined against their pasted status alert. The existing paper reporting design remains the content authority. This is local source and synthetic evidence only: no deployment, Discord request, model call or live-state change.

The primary EUR200 wallet, all eight money rows and four same-size comparisons precede inventory/cost-quality/activity detail. Bold labeled bullets expose wallet net value, cumulative closed P&L and **known costs**, explicitly a recorded subtotal. Native accounting is unchanged; monetary display is rounded to cents using half-even, with signed nonzero values below one cent still visible. All six monetary fields, per-category actual/estimated/missing quality, unavailable rows, cadence, canonical event identities and confirmed-only delivery markers remain intact. Common entry-pause codes get bounded plain-language labels with their native code retained. Worker, entry permission and model access are separate; model access is not a review outcome.

## Final checks

- `focused-final-v5.result.json`: **67 tests pass**, zero failures/errors/skips; paper reporter plus legacy notifications. Source pins unchanged during the run.
- `native-capture-final-v3.result.json`: **12 tests pass**, zero failures/errors/skips; native capture/composition through the existing discovery entrypoint. This ran against the same final production source and capture tests. Later reporter-test-only edits await complete delivery and retain the control-wallet unavailable assertion.
- The unchanged four-part/1900-character checks include eight simultaneous closes combined with a five-batch summary, partial failure, restart and concurrent arrivals. No timeout or bound was relaxed.
- `rendered-preview-v2.json`: actual formatter output through an in-memory transport, guarded against real HTTP. Synthetic summary: three parts, 1567/1638/1678 characters. Close: one part, 906 characters. Status: one part, 617 characters. The additional unknown-cost case is three parts, 1564/1647/1696 characters. All eight wallet identities and four comparisons verified. Inputs exercise long decimal precision, a sub-cent loss, and unknown equity/total costs with a zero known subtotal.
- `final-file-verification-v2.json`: frozen `hermes-snapshot-20eb6af` remains exactly 667 files; all raw inventory hashes match, with no missing files, extras or mismatches. The installed PC notifier still matches that frozen release, and the unrelated dirty cutover document is unchanged.
- `graph-update-final.log`: repository AST graph updated locally without an LLM or paid API. No semantic labeling was requested.
- `git diff --check` passed for the changed production/test files. Git reported its existing LF-to-CRLF warning for the test file; no source rewrite was performed to conceal it.
- The full staged whitespace check flags doubled Windows carriage returns in four raw captured logs (`focused-final-v3/v4/v5.log` and `native-capture-final-v3.log`). The original logs are retained unchanged; this is not a source/test failure. Source, tests and the preview pass their scoped whitespace check.

## Retained earlier outcomes

- `readability-red-v1.log`: wrong interpreter lacked CCXT; three import errors. No dependency was installed. Later runs use the existing project venv and import the correction worktree.
- `readability-red-v2.log`: three expected formatting failures before implementation.
- `focused-v1.log`: eight failed assertion/subtest results and one timeout. Primary-first layout exposed tests treating `control_200` as the last delivered part; tests now await completed startup delivery. Wording/fake failure triggers were updated to the new presentation.
- `focused-v2.log`: all 64 then-current reporter/legacy cases passed.
- `overview-red-v1.log` and `timestamp-red-v1.log`: expected failures before moving all money rows ahead of details and removing duplicate first-part timestamps.
- `focused-v3.log`: 64 cases passed plus a native-capture import error from using its module directly. Its bare fixture imports require discovery mode; production code and test deadlines were unchanged.
- `focused-v4.log` and `native-capture-v1.log`: the then-current 64 + 12 cases passed before the final plain-language reason labels.
- `reasons-red-v1.log`: expected missing-label failures before the bounded reason mapping. Final runs supersede prior intermediate pins.
- `focused-final.result.json` and `native-capture-final.result.json`: earlier 65 + 12 passing checks predate human wallet labels and rounded monetary display; they are not final-source verification.
- Human-label, money-display, known-cost and standalone-close red logs retain the failing checks before those refinements.
- `focused-final-v2.result.json`: 66 checks with one failure; the longer combined summary omitted the final close reference. It was fixed by shortening repetitive presentation, not increasing delivery bounds. Its native capture companion passed 12 tests.
- `pasted-alert-red-v1.log`: five failed assertions across three cases before removing repeated status lines, replacing raw model data, and moving diagnostics after the wallets. `pasted-alert-green-v1.log` passes those cases plus the eight-close bounded envelope.
- `focused-final-v3.result.json`: 67 checks with one failure because the restart test inspected the first part before the diagnostics part arrived. It now waits for the confirmed cadence marker with its original timeout; production delivery behavior is unchanged. The next 67-check v4 run passed. V5 retains an additional existing control-wallet unavailable assertion after review.

Parent review confirmed that the production diff is confined to presentation helpers and the paper reporter's rendered lines. No input validation, event identity, queue/cadence, account, risk, strategy, transport or retry logic was changed. All preexisting native-capture assertions retain their limits and deadlines; names are updated to human-readable wallet labels. The live state-signature calculation is unchanged, including its existing sensitivity to telemetry fields: reducing notification frequency would be a separate behavior change.

This is focused local verification, not a full-suite result, live delivery proof or a deployment-ready release package. The original experiment, financial/safety history and running snapshot cutover remain outside this formatting change.
