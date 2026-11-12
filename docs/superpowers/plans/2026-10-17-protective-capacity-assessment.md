# Protective protocol capacity assessment

Read-only source assessment, 2026-10-17. No capacity probe, market backtest or external service was run. These are static bounds and required measurements, not a capacity qualification.

## Observed limits

Version2 `archive_protective_witness.read` and `apply` replay the full retained event chain under a maximum ten-second deadline. The loader also retains operation IDs in a growing set. Signed transport calls these stateless paths directly. Version1's historical 518,401-event fixture already exceeded the stateless ten-second read limit; its prepared receiver, 73.641-second preparation and export measurements qualify version1 only.

The prepared receiver in `archive_receiver.py` and exporter/restorer in `archive_receiver_export.py` require version1 bindings, tables, heads and certificates. They cannot be reused as version2 serviceability or recovery evidence. Source validation also scans all retained original journal records and all-eight account completions before returning a suffix of at most two; a small returned suffix is not a bound on source scan work.

For 259,200 common cohort slots, the protocol permits 4,096 bytes per request, 8,192 per event and 1,036,801 events. The universal raw event allowance is 8,493,473,792 bytes, before SQLite overhead. Thus individual caps do not prove that the default four-GiB receiver budget can fit every allowed history.

For a declared run with no cancellations or independent checkpoint-only changes, event count is one genesis plus ordinary reservations plus completion acknowledgements plus distinct pending freezes. Reserving the last original slot for protection gives at most 518,400 events without freezes, or 777,600 with one freeze per packet. Protective credit renewal piggybacks on a later ordinary reservation; it creates neither another event type nor another original slot. Combining normal and protective completions in one acknowledgement reduces event count. Cancellations and checkpoint-only changes require separate accounting.

The current conservative receiver write ceiling reserves three main-file extents plus 64 KiB. With four-GiB budget, 4,096-byte pages and no sidecars, the maximum writable main file is 1,431,633,920 bytes, roughly 1.333 GiB. At 518,400 events this allows about 2,761.6 database bytes per event, including row, index and page overhead. Measure actual payload distributions rather than substituting the per-event maximum as an expected size.

## Missing combined budget

Every accepted authenticated read, check, reserve and fresh-envelope retry writes a durable nonce before receiver execution. A failed CAS or later timeout can therefore consume nonce work without an event. The nonce repository uses WAL separately from the receiver. Its five-minute expiry cleanup bounds currently retained rows only with an explicit request-rate assumption; retries are not bounded by the original cohort cycle count. Main-file high-water pages, WAL, SHM, checkpoints and contention remain outside the receiver-only budget.

The worker session's bounded cache payload lives in the existing worker runtime database, but its SQLite pages and rollback journal still count toward the total worker footprint. Its payload cap does not bound the whole database or snapshot peak.

Source journals, all eight financial databases, both cost books, supervisor/review/attempt state, nonce state, retained archive objects, manifests, source and destination staging, receiver backup/export, restore staging and independently retained copies must be included in the total peak calculation. Existing source-only, receiver-only and per-export limits do not compose automatically.

## Next bounded diagnostic

Use a disposable synthetic version2 receiver/nonce/source diagnostic, limited to 2,048 common completions, 128 MiB total fixture footprint and a 120-second outer lifetime. Stop on any limit. Construct history linearly using the actual version2 transitions and event encoding, then validate complete prefixes with the real loader at 64, 256, 1,024 and 2,048 slots. Repeated stateless appends would create quadratic fixture setup and obscure the measurement.

Include ordinary reserve/freeze/completion, protective freeze/completion, credit renewal, a combined normal-plus-protective suffix, and exact lost-reply retry with a fresh nonce. Measure signed message bytes, event payloads, database/index/page/freelist bytes, receiver/source/nonce files and sidecars, process peak memory, full-read latency and representative durable append latency. Advance synthetic receiver time by one minute to exercise nonce expiry; include a bounded retry burst and restart replay refusal. Validate source shape on a small original eight-account fixture; label larger receiver histories as invented receiver evidence.

Slopes and projections are diagnostics. Prepared version2 receiver, version2 export/restore, nonce-inclusive recovery, blocking-I/O deadlines and complete cohort/staging/backup peak capacity remain separate launch gates. Preserve the original cycle, deadline and eight-wallet scope.

Source references: `hermes_trading/archive_protective_witness.py`, `archive_protective_transport.py`, `archive_protective_source.py`, `archive_protective_session.py`, `archive_witness.py`, `archive_receiver.py`, `archive_receiver_export.py`, `control_auth.py`, `persistence/repository.py`. Historical version1 evidence: `research/runs/production-archive-integration-v1/receiver-cache-full-horizon-probe.json` and `receiver-export-full-horizon-probe.json`.

## Bounded diagnostic result

The original 2,048-slot run stopped at its 120-second outer deadline while regression/graph work also ran; it produced no capacity report. A new 512-slot run completed its workload but failed report publication because unintended third-party virtualenv source fingerprints exceeded the 256 KiB evidence limit. Both failed sources/logs are preserved. Neither is a passing capacity measurement.

The corrected one-shot version3 diagnostic pins imported repository code, its own source and dependency config/lock files; installed third-party package source contents remain unqualified. Seven helper checks passed, including dependency filtering, evidence publication and Windows launcher/runtime/descendant termination. The main diagnostic passed in 78.875 seconds over 512 total synthetic slots: two native eight-account source slots and 510 invented receiver slots, with 1,276 retained receiver events. Temporary disk peak was 8,638,520 bytes; process peak RSS including imports was 169,549,824 bytes. These are separate resources. Owned temporary roots were removed.

The receiver main file ended at 4,263,936 bytes; nonce WAL reached 4,124,152 bytes before restart. Durable nonce expiry and restart replay refusal passed. Signed full-read time grew from 0.109 seconds at 156 events to 0.609 seconds at 1,276 events. The observed growth supports prioritizing a prepared version2 receiver and a combined nonce/receiver/peak budget. It does not qualify six-month latency, export/restore, independence, a hard filesystem quota, or profitability. Bulk receiver setup was batched separately from nonce authentication, so setup time is not end-to-end production throughput.

Evidence: [version3 report](../../../research/runs/production-archive-integration-v1/protective-capacity-probe-v3.json) and [diagnostic status](../../../research/runs/production-archive-integration-v1/protective-capacity-diagnostic-status.json). Repository paths in the source-reference paragraph are relative to the project root.
