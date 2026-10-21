# Paper proposal numeric compatibility

Continue the approved release-readiness work without model calls, activation or a new strategy experiment. Reproduce the accepted fractional RSI proposal through the real review, publication, YAML reload and strategy evaluator. The strict engine rejects binary floats while the legacy proposal helper retains fractional floats. Use a shared paper-only proposal normalizer for review and publication: exact decimal bounds/steps, integer values where integral, decimal text where fractional, whole bar counts, and numeric equality for ineffective changes. Preserve the raw model response, one-variable scope, original strategy bytes, version/archive contracts and all risk limits. Leave the legacy reflection path unchanged.

Tests first: fractional RSI survives publication and affects real signals; fractional stop/ATR settings survive engine validation; integral bar counts remain integers and fractional bar counts are rejected; numeric-equivalent text does not consume a change; exact boundary steps are accepted and excess/nonfinite/bool inputs rejected. Run focused and full unittest suites, refresh the AST graph and save evidence. This is compatibility verification, not profitability evidence.

## Verified checkpoint

Implemented shared paper-only decimal normalization.22publication/23global/5numeric-policy checks and872full-suite tests pass in86.367s. Scoped review clear. Evidence research/runs/paper-proposal-numbers-v1; AST graph refreshed. No provider call, release deployment, experiment activation or financial result occurred.
