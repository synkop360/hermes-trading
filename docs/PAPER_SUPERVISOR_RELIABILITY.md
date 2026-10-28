# Supervisor snapshot reliability

The October 28 investigation established two timing failures in the installed `ea752c5` release:

- Three read-only attempts started at a normal publication lease onset all exhausted the existing retry after 9.709–10.000 seconds, with explicit numeric SQLite BUSY causes. The next healthy heartbeat followed about eleven seconds after the lease began. Evidence: `research/runs/paper-reliability-v1/20261028T180912Z-busy-onset.json`.
- A deterministic eight-wallet native worker fixture reproduced the completed report being visible before its matching heartbeat. Both publication gates were clear, the previous heartbeat was healthy, and snapshot validation rejected the report hash mismatch. Publishing the ordinary matching heartbeat restored validation.

The correction extends the shared retry admission budget to twenty seconds, at most thirteen complete collections, inside the existing thirty-second subprocess deadline. A specific report/heartbeat mismatch exception permits recollection within the same budget. Persistent mismatches and lock contention still fail closed. Other health, source, identity, account, risk, pending publication, malformed-record and timestamp checks remain mandatory. No snapshot is returned until all validation passes.

This changes one runtime module, `supervisor_sync.py`. The checked transition helper admits only the exact deployed predecessor and this module delta. Worker publication, strategies, risk limits, model cadence, paper mode, paid-call prohibition, original wallets, opening and clocks are preserved. The existing owner-authorized unhalt is preserved; this correction does not acknowledge or clear any halt.

## Verification and deployment plan

1. Reproduce with real SQLite and synthetic native worker fixtures, then test recovery, bounded failure and unchanged rejection checks.
2. Freeze the byte-preserving release over the deployed raw inventory; run affected exact-package checks on Windows and Linux and disclose broader-suite results separately.
3. Capture current state and schedules, pause only paper supervision and backup, stop the predecessor and deploy with readiness withheld.
4. Verify closed worker and PC references plus both checked transition rollback copies on E: before updating release bindings. Preserve all protected evidence and the current safety state.
5. Validate both actual hosts and original identities, release readiness and restore schedules. Observe normal supervision over multiple access-renewal windows, repeat the natural lock-onset read-only probe, verify all eight wallets and a natural Discord summary.

Standing owner authorization is “Always deploy before stopping,” followed by the request to continue until the worker runs without errors. No market backtest, training, paid inference, real order or new strategy is included. A clean observed window is operational evidence, not a guarantee of zero future errors or profitability.
