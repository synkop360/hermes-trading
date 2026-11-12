# Native reporting acceptance inspector

Prepared for owner execution after readiness. The script has only been parsed/reviewed statically; it has not been run against state or a fixture. It reads no environment or credentials, makes no network/provider/sender call, and writes no file or database. Output is one sanitized JSON observation.

Run natively on the worker with:

```sh
/app/.venv/bin/python /path/to/inspect_reporting_acceptance.py --root /app/state --baseline-complete 1741 --summary-every 5
```

The ledger is `/app/state/paper_discord_reporter.json`, schema1, max64KiB. It exposes `startup_confirmed`, confirmed local part IDs, pending/abandoned counts, counters, last completed ordinal and sanitized cadence markers. A persisted cycle marker of at least1, with at least five observed new settled batches, records successful completion of the configured five-batch report intent; the cadence argument is an explicitly supplied expectation, not a configuration/environment read. The inspector separately records completion delta from the original closed cut1741, current heartbeat identity, all eight wallet hashes/native EUR observations/four cost-quality categories, four same-size comparisons, latest finalized settlement agreement and the retained supervisor entry halt.

A single query_only SQLite mode=ro connection has a0.2s deadline and no retry. A locked journal or racing heartbeat yields unavailable/inconsistent evidence, never fabricated success. It never uses immutable=1 on live state. The ledger does not retain Discord remote message IDs, message bodies or historical per-report financial snapshots, so a current eight-row match cannot prove a historical message body. Startup can precede the first new financial envelope. Preserve this limit when pairing the ledger with actual observed Discord messages. No test trade, manual message or replay is needed.

Both-host sequence remains one frozen plan/transition_id but separate host intents. Remote roots stay `/app/state`, `/tmp/hermes-discord-ed439e8/peer-originals/pc-reference-bundle/pc-original` and its `launch-original.json`. PC roots stay `d439/w`, the actual supervisor folder and actual native launch file recorded in `pc-original-reference.json`; PC audit is `C:/Users/thoma/AppData/Local/hermes/cutovers/paper-discord-ed439e8/pc-audit`. Root bindings hash the three absolute input paths and must remain unchanged within each host's prepare/reconcile/apply sequence. Do not copy an intent between hosts. The native Linux plan is exported unchanged; foreign path validation is not relaxed.
