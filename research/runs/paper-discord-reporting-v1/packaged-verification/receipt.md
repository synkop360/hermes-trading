Packaged synthetic verification of `release-upload-8f315b3-v2` completed with exit code 0: 161 tests passed, 0 failures, 0 errors, 0 skips, and 0 captured ResourceWarnings. Test duration was 416.844 seconds; total verification duration was 419.141 seconds. Candidate: `8f315b35cf6a40267cc07be1bac2c9be126006c7`.

| Exact test module | Tests |
| --- | ---: |
| test_notifications | 29 |
| test_paper_notifications | 29 |
| test_paper_notification_capture | 12 |
| test_paper_discord_transition | 3 |
| test_paired_worker | 25 |
| test_paper_boot | 4 |
| test_paired_paper | 27 |
| test_paired_risk | 3 |
| test_archive_protective_worker | 10 |
| test_paper_release_transition | 19 |

The unchanged native process cancellation test launched both `_load` and `_persist` children. It asserted native `asyncio.run` shutdown under 2 seconds during a 3-second synthetic metadata stall. The guarded child processes exited in 1.047 and 1.422 seconds including interpreter startup/imports. The release-transition CLI child also used the packaged script. All three child receipts show active raw HTTP denial, no attempted raw HTTP, and no imported runtime/script provenance violations.

Financial/capture checks included all eight final native EUR account observations, all four adaptive-minus-control comparisons, individual actual/estimated/missing cost provenance, unavailable measurements, duplicate close suppression, native partial-close quantity/P&L, final risk-sweep closes, archive-outage closes, and a stalled sender while the next native batch protected all eight accounts and preserved the opening. The exact both-host reporting transition preserved original state and retry behavior. Legacy transition cases covered rollback without rewinding history and all six component boundaries at both commit and receipt (12 independent synthetic fixtures).

All 586 raw source pins matched before and after, with no missing, extra, or mismatched export files. The raw pin manifest SHA-256 remained `ac2383d3a76f6dcfd1642f87a1640400a660a3f95ab4661873473d6f1a2a4dba`. The runtime key set stayed at 129 modules: exactly `notifications.py`, `paired_paper.py`, `paired_worker.py`, and `paper_boot.py` differed from the predecessor; 125 runtime module digests, the watcher, and the native launcher remained unchanged. Release-plan validation and every map/release digest matched the supplied `export-verification.json` and `proposed-release-plan.json`.

The main interpreter ran from the v2 export with `sys.path` beginning `[export/tests, export]`. All imported `hermes_trading` modules and `scripts` resolved inside that export. The shared virtualenv supplied the interpreter/dependencies only. `sitecustomize.py` installed raw `httpx.HTTPTransport.handle_request` and `httpx.AsyncHTTPTransport.handle_async_request` denial before application/test imports, inherited by every native subprocess; injected fakes and MockTransport remained available. The test modules were not edited. Python bytecode writes were disabled. Every financial state/transition fixture was synthetic and temporary. No deployment, restart, real webhook request, live-state mutation, training, backtest, or profitability qualification was performed.

Receipts in this directory:

- [Machine-readable results and per-test durations](packaged-verification-results.json)
- [Complete terminal log](packaged-terminal.txt)
- [Raw pins before](raw-pins-before.json) and [after](raw-pins-after.json)
- [Release map verification](release-map-verification.json)
- [Main interpreter receipt](process-56356.json)
- [Native metadata load child](process-54836.json) and [persist child](process-57568.json)
- [Release-transition CLI child](process-50784.json)
- [Source-frozen runner](run_packaged_verification.py) and [inherited transport guard](sitecustomize.py)
- [Captured invocation](captured-command.txt)

This is correctness evidence for the exact packaged candidate. It does not establish installed behavior or investment performance.
