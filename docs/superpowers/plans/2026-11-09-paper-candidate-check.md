# Disabled paper candidate check

**Goal:** Turn the existing paper-beta preparation checklist into a local read-only command, without treating the public pilot as a beta release.

**Contract:** Check only the explicit disabled configuration folder: eight adaptive/control EUR50/100/150/200 profiles, EUR200 primary/rationale binding, strategy and goal hashes, declared fee consistency, preserved risk caps, fixed controls, unassigned account/cost scopes, disabled model/supervisor/archive switches, safety and daily-backup settings. Return a structured JSON report and exit nonzero for an invalid candidate. Never activate accounts, populate missing proofs or claim installed readiness. An intact candidate still requires fresh release, model, cost, venue and deployment checks.

1. Add unittest cases using copies of the checked-in candidate. Cover intact/read-only operation, changed pins, missing/duplicate profiles, path traversal, repinned economic drift, activated drafts, incomplete costs and malformed input.
2. Implement `scripts/check_paper_candidate.py` using the standard library and the project's existing PyYAML dependency. Bound file reads and reject symlinked candidate inputs. Do not import the trading engine or external clients.
3. Document the command and report meaning in `docs/PAPER_BETA_READINESS.md`.
4. Run the new test module, the existing PowerShell launcher tests, and the command on the actual candidate. Obtain a focused independent review before publishing a PR based on the merged #14 release.

## Implementation finding

The first candidate check reproduced inconsistent raw-byte pins: strategy and
rationale pins use LF, while all eight goal pins were generated over CRLF.
Git stores the goals as LF. Enforce LF for the configuration directory and
refresh only the manifest's eight goal pins to the existing LF bytes. Preserve
all goal values and historical source-hash records. A real Git smudge test
with `core.autocrlf=true` proves the checkout policy prevents recurrence.
