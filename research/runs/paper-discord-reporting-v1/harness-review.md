# Scoped Linux verification harness review

October 25, 2026. Candidate `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9`; exact `release-upload-ed439e8` export. No runtime/test source edit, model/provider change, installed-state query, deployment, restart, Discord request, credential use, or paid call is part of this review.

The proposed narrow harness adjustment is meaningful **qualified verification**, provided it is not reported as passing the original outer test deadline. The native metadata subprocess test measures `asyncio.run` after imports and asserts elapsed time below two seconds. Its separate `subprocess.run(timeout=8)` includes interpreter and heavy CCXT/test-helper imports. The preserved diagnostic shows complete child wall times of 11.027 and 12.642 seconds but measured shutdown bodies of 0.0094 and 0.4049 seconds. The faulthandler/import-time trace locates the delay in imports before the measured timer. The original 52-case failure and isolated eight-second deadline failures remain valid preserved evidence; they are not relabeled as passes.

The external [qualified harness](run_linux_harness_review.py) retains all pinned source/test bytes and changes only the outer observation timeout from 8 to 30 seconds when the argv exactly matches the AST-extracted pinned metadata snippet, native interpreter flags, `_load`/`_persist` operation, and original keyword arguments. Other subprocess calls pass through unchanged. Both matching child timings and snippet hashes are recorded. The native two-second assertion, three-second synthetic stall, return-code checking, platform/storage/archive guards, and expected 52-case suite remain unchanged.

The run uses the same cached image digest, CPU/user/memory/tmpfs configuration, read-only source/pin mounts, cleared environment, and disabled container network as the original 52-case attempt. It neither pulls an image nor reads actual paper state. Native source imports must resolve under `/workspace`, all source pins must match before/after, and both recognized metadata children must exit successfully with measured shutdown below two seconds. The output explicitly states `qualified_harness=true` and `original_test_deadlines_unmodified=false`.

Independent pre-run check verified all **603/603** export files and raw pins, pin-manifest SHA256 `f4f5ce336e1be6f955dff901f9ea345e98584c80cebfd7dc50df871ca70c7d99`, candidate HEAD, and no source/test/script working-tree diff.

The qualified run completed with **52/52 passing, exit 0**, no failures, errors, skips, captured ResourceWarnings, or foreign imports. Test-runner duration was 101.786 seconds; total measured verification duration was 106.926 seconds. All 603 source pins remained unchanged, and the owned disposable container is absent. [Machine receipt](linux-verification-ed439e8-harness-review/verification.json), [terminal log](linux-verification-ed439e8-harness-review/stdout.log), [exact command](linux-verification-ed439e8-harness-review/command.json), [qualified container harness](linux-verification-ed439e8-harness-review/qualified-container-code.py).

| Exact recognized child | Whole process observation | Native measured shutdown | Result |
| --- | ---: | ---: | --- |
| `_load` | 10.139 seconds | 0.0040 seconds | exit 0; unchanged `<2` assertion passed |
| `_persist` | 10.378 seconds | 0.4078 seconds | exit 0; unchanged `<2` assertion passed |

Exactly two overrides occurred, both with pinned snippet SHA256 `270335c6cf93cee8c943b913069362051aaf897f6f7948c7e8e59258022df526`. No other subprocess timeout changed. Post-run source/test/script diff is empty; reporter raw SHA256 remains `5020950cac1c807ab5864384172f43ba91d6d4ed77a42ad64a038b9abdefb0a4` and the working test raw SHA256 remains `a686a5dbb97efd090ed37919a940cc9dd20d2e81b1fddd4356566e364291b5fb` (the export's LF-normalized test bytes are pinned separately).

This is sufficient scoped Linux correctness/shutdown evidence under the explicitly adjusted observation harness. It is **not** an unmodified-deadline test pass, full Linux qualification, installed-operation acceptance, or resolution of the separately investigated live entry halt. Keep the original eight-second deadline failures alongside this qualified result. No pinned test-only or runtime correction is required merely to obtain this clearly qualified evidence while the source-frozen full run continues.
