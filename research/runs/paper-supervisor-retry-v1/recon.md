# Read-only transition recon — October 25, 2026

No product code, test, state, process, provider or remote action was changed/run by this recon. The design and implementation plan were read alongside the existing transition helper/fixtures/tests.

## Smallest helper change

Only new predecessor/purpose constants, `_purpose`, and `validate_release_plan` need change. Keep the existing plan shape, six components, inspection/apply/rollback APIs, guards and old purpose branches. For the new ed439e8 branch require both `_hash(old)` equal the approved full release and `_hash(old['runtime'])` equal the approved runtime-map pin. Then the existing inventory/watcher/native equality checks and exact changed-set check must allow only `{'supervisor_sync.py'}`. A valid one-file diff by itself is insufficient predecessor authority.

## Authoritative offline predecessor

Both following files are already tracked:

- `research/runs/paper-discord-reporting-v1/live-cutover/frozen-live-transition-plan.json`: read `['release_plan']['new']` only.
- `research/runs/paper-discord-reporting-v1/packaged-verification-ed439e8/release-map-verification.json`: independently names its source, release and runtime-map hashes.

Canonical hashing of the first map was checked in this recon:

- source `ed439e897a4dd388fdcf6425e7e8b6171d5a88a9`
- full release `fb67c1a204c1448af5b9e6e0ca7ec41aeb95eadaba62075bcce80af6dd8dd844`
- runtime map `0a76f3a95733fdc7f0ce951b7f55f5a6040be0ae49387be4cf70563c6489165c`, 129 modules
- watcher `118d3170daa2bd816c205a66ec12cbaa829aceae01d456e1aa9f8d842f4e36e2`
- native launcher `d1426320536bd8c369163d8fd6221f151fcb4f01e76ceb3033d945b8f96bd073`

No installed state is needed to obtain this map. Do not derive the predecessor from `_supervisor_release()` after editing runtime code; that would change the old map and local CRLF differences can also change unrelated digests.

## Synthetic fixture adaptation

Follow `ReportingTransitionFixture` in `tests/test_paper_discord_transition.py`: subclass `NativeTransitionFixture` and override only `_releases`. Return the immutable old map above plus a deep copy with just the successor `supervisor_sync.py` digest replaced. A synthetic digest is sufficient for the admission/closed-state tests; it must not be called proof of a packaged successor's actual source. Update `f.release_plan`'s old/new commits after `create`; no shared fixture edit is needed.

`NativeTransitionFixture.create` already propagates the supplied release through native importer/lifecycle/opening/worker evidence. `release_context` patches six actual runtime import sites. Existing test patterns provide all-six-component inspect/apply/idempotency, fault/reconcile, root-bound intent and protected-history comparisons.

Useful new-purpose cases:

1. Admit the exact new purpose and preserve the input plan; reject old-purpose substitution.
2. Reject a foreign old runtime field changed identically on both sides (the diff remains one file), a foreign watcher/native changed identically on both sides, wrong base commit, missing/no-op/extra runtime changes and inventory changes.
3. Prepare/inspect/apply/retry the new purpose on a native synthetic closed fixture, compare `_protected` before/after and original opening/eight accounts/clocks/history. Seed a supervisor latch using native `record_supervisor_result` at the synthetic worker root if explicit latch coverage is needed, and compare its bytes unchanged.
4. Reuse the existing injected `component_commit` interruption pattern for partial/mixed/reconcile behavior and root-bound audit rejection. Keep actual-state and deployment operations excluded.

Existing general transition tests remain valuable regression coverage, but the new purpose needs its own exact-predecessor and composed synthetic proof. Do not expand archive engineering or claim full-suite success from these selected checks.
