# Offline paper candidate check

Run from a clean checkout of the current release:

```powershell
uv sync --python 3.11 --locked
uv run --no-sync --python 3.11 python -m scripts.check_paper_candidate
```

The installation can download dependencies. The checker itself reads only the
explicit candidate folder (default `config/paper-beta-v1`); it does not load
`.env`, open production state, contact any service, initialize accounts or
assign an inception time. An alternative disabled candidate can be selected
with `--root PATH`.

## Report and exit status

| Field | Meaning |
| --- | --- |
| `candidate_valid` | The disabled preparation checks in this command passed. |
| `status` | `valid_disabled_candidate` or `invalid_candidate`. |
| `checks`, `errors` | Individual checks and observed failures. Missing inputs fail; no zero values are invented. |
| `accounts` | Profiles whose preparation checks passed; a valid bundle has eight. |
| `activation_ready` | Always `false`: this command never grants launch authority. |
| `activation_assessed` | Always `false`: fresh/installed activation evidence is outside this command. |
| `remaining_activation_checks` | Release verification, fresh model/cost/venue evidence, explicit deployment/activation approval and actual runtime checks still needed. |

Exit `0` means a valid disabled candidate; exit `1` means invalid preparation.
Neither exit status proves trading activity, profitability or installed health.
The command validates selected preparation invariants, not every runtime
schema or a complete release/security audit. The existing runtime validators
remain authoritative at activation.

The checks cover the eight EUR50/100/150/200 adaptive/control profiles, the
EUR200 primary rationale binding, raw strategy/goal hashes, approved fee and
risk declarations, a common economic strategy with fixed controls, explicit
unassigned account/cost scopes, disabled execution/model/archive switches,
one supervised writer and the minimum safety/daily-backup declarations.
The rates are the candidate's approved estimates, not a newly verified current
Kraken fee schedule or private account tier. The public historical pilot with
zero fees is a different configuration and is not accepted as this beta bundle.

## Windows byte identity

The candidate's strategy and rationale pins use LF bytes. Its original goal
pins were calculated over CRLF even though the versioned goal files use LF.
The corrected manifest binds the actual LF goal bytes in all eight profiles;
no goal value, strategy or risk limit changes. Historical source hashes in
the provenance records are preserved as historical evidence.

`.gitattributes` now forces LF for `config/paper-beta-v1/**`, including when
Git has `core.autocrlf=true`. Use a fresh checkout for validation. Pulling a new
attribute rule need not rewrite unchanged files already present in an older
working tree; the checker will still reject their stale CRLF bytes. Do not
replace a manifest hash just to make an unexpected strategy pass. Preserve any
local changes and inspect them before choosing a new clean checkout.

## Verification scope

The unit tests copy the actual versioned bundle, exercise malformed and
repinned inputs and verify that the checker does not change any candidate
file. A real local Git conversion test reproduces Windows `core.autocrlf=true`
and verifies the pinned strategy bytes are preserved. These offline tests do
not replace the trading engine suite or a market observation.
