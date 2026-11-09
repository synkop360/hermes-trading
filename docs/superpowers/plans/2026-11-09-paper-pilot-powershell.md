# PowerShell paper pilot implementation plan

**Goal:** Complete the existing Windows launch requirement for the merged eight-account pilot.

**Scope:** A PowerShell wrapper around the existing `pilot_paper` CLI, plus its documented commands. Python runtime, strategy, fee assumptions, reference replay and risk limits stay as implemented.

1. Add unittest coverage invoking Windows PowerShell with a substituted `uv` process boundary. Check bootstrap-before-run, optional installation, bounded arguments, replay forwarding, exit failures and existing-root refusal.
2. Implement `scripts/run_paper_pilot.ps1`. Default to a unique `.test-tmp*` session, Python 3.11, one cycle and 120 seconds. Forward the same root, experiment and optional replay archive to bootstrap and run. Stop after every nonzero process exit.
3. Add PowerShell examples for current-market observation and the existing historical archive to `docs/PAPER_PILOT.md`.
4. Run the launcher tests and PowerShell parser checks. Report that these verify orchestration; they do not replace the Python engine suite or a real Kraken observation.
5. Publish a focused PR from the merged release, with actual validation results.
