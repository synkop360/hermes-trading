# Versioned primary wallet implementation

> **For agentic workers:** Use superpowers:executing-plans inline with TDD. Existing dirty feat/live-hardening checkout is the authorized candidate; preserve its uncommitted work and all historical evidence. No commit/push was requested.

**Goal:** Implement the approved explicit EUR200 primary revision coherently across the full eight-wallet paper system, preserving original EUR100 interpretation.

**Architecture:** One pure strict resolver binds wallet and canonical selection hash. New reporting/risk versions carry that binding; consumers select the same account pair through the resolver. A separate disabled candidate retains the original strategies, caps and accounting. No in-place active migration.

**Tech stack:** Existing Python, Decimal, SQLite and unittest. No services or paid calls.

**Spec:** `docs/superpowers/specs/2026-10-18-primary-wallet-protocol-design.md`; explicit authorization `research/runs/primary-wallet-protocol-v1/authorization.json`.

## Constraints / review focus

- Keep all8adaptive/control EUR50/100/150/200 accounts, complete standalone costs,5unique-close cadence/latest25context/allunresolvedIDs and one-variable/no-change policy.
- Preserve legacy REPORTING_PROTOCOL and risk schema1 as EUR100-only. New selection requires schema2 reporting/risk identity with identical method parameters.
- Strict integers/versions/digests; reject conflicting manifest/report/launch/risk/account selection before collection or paid work. Selection changes after inception/history fail; original pending packets are preserved.
- Global3percent halt follows either chosen primary arm; other pairs halt locally. Initial peaks use each original capital; no threshold changes.
- Preserve original7-field archive identity: cohort digest already binds manifest selection. Historical evidence is verified only by its original release.
- Review below/above100 selections, custom profile-directory mapping, changed rationale with same capital, copied legacy risk and in-flight/history state, all supervisor/report/archive paths and original statistical seeds/methods.
- No deployment, activation, economic backtest/training, paid service/call or real order.

## Tasks

1. **Strict resolver and versioned risk foundation**
   - [x] Add failing tests for all four capitals, legacy100 interpretation, ambiguous labels/types/versions, canonical rationale hash and explicit matching risk state.
   - [x] Add `hermes_trading/paired_primary.py`: immutable `PrimarySelection`, `resolve_primary(manifest)`, selected canonical account names/directories and `reporting_protocol_for(manifest, legacy_protocol)` with every method parameter preserved.
   - [x] Extend `paired_risk.initial_risk_state(primary=None)` and `update_risk_state(...,primary=None)` with explicit schema2 binding. Defaults remain legacy100. Validate state before use via `validate_risk_state(state,primary=None)`.
   - [x] Run new tests and existing risk/accounting tests. This foundation alone does not complete selection support or authorize a new manifest/candidate.
2. **Financial report, source and inception bindings**
   - [x] Make statistics accept explicit starting capital while preserving default100 and original numerical method. Bind report/opening/stress/settlement keys to the resolver; retain immutable REPORTING_PROTOCOL.
   - [x] Make source reconciliation and inception select the same primary pair; reject mismatched/new protocol or changed inception identity. Test selected capital50/200, original opening data and wrong risk state. Financial composition86tests passes; selected native fills strengthened with one additional overlapping test. Evidence `research/runs/primary-wallet-protocol-v1/finance-verification.json`.
3. **Execution, supervisor and archive composition**
   - [x] Resolve selection before profile I/O/collection. Wire common journal, strategy propagation, worker stats/heartbeat, supervisor launch/mirror/account and publication checks to the same selected pair.
   - [x] Wire certificate/coordinator/witness/protective source/worker boundary/full-cut reads. Preserve7-field identity, all canonical accounts and original claim/history semantics.
   - [x] Exercise selected-primary publication, either-primary global halt, nonprimary pair halt, pending packet/restart and complete eight-account/separate-controller archive coherence. Legacy fixtures remain unchanged.
4. **New disabled candidate revision**
   - [x] Build a fresh `research/runs/primary-wallet-protocol-v1/candidate` from the original disabled venue candidate. Retain all original files unchanged; bind200selection rationale/report/supervisor/release consistently.
   - [x] Leave experiment/account/owner identity unassigned and activation null. Check all original strategies/caps/fees/cost-reserve settings and eight wallet scope match.
5. **Verification and recording**
   - [x] Freeze current sources, run relevant compositions and full Windows unittest discovery; Linux if existing Docker is available. One independent final scoped review plus TDD fixes.
   - [x] Refresh AST graph, retain previous244source full-discovery proof and original15prototype/26draft hashes, update readiness/progress with honest limits. Primary selection is not profitability evidence or launch approval.

The preceding capture milestone is independently complete: full Windows discovery1451tests/1448pass/3platform skips, noResourceWarnings,244pinsunchanged; original prototypes/drafts preserved. This does not qualify independent recovery lifecycle, installed broker/allwriters, physical/aggregate beta capacity or financial outcomes.

## Task3 implementation decisions from consumer review

`supervisor_sync.py` and `paper_publication.py` are also primary-sensitive; the design's behavior requirements cover them even though its explicit module list omitted them. This is required coherence within the approved implementation, not a new authorization request.

- Retain the existing six serialized snapshot files and unchanged transport commands. Existing wallet reports already contain `observation.account_config_hash`, starting balance and currency.
- Versioned launch schema2 adds strict `primary_wallet_eur` and `primary_selection_sha256`, keeping the existing canonical account/policy/release/lifetime pins. Legacy launch1 remains historical100. New launch paths are explicitly canonical `adaptive_<selected capital>` within the isolated experiment directory; the core resolver can still map custom physical directories for accounting.
- `capture_startup_release(state_dir,*,now=None,primary=None)` preserves legacy defaults. The selected worker passes its already resolved selection and pins capital, selection hash and selected account hash once at startup, before lifecycle reconciliation. Heartbeats forward those pins unchanged; do not reread a changed manifest into a live startup identity.
- `validate_snapshot(snapshot,expected_policy,*,now=None,expected_primary=None,expected_account_hash=None)` branches strictly: legacy expectation accepts snapshot1, new expectation requires snapshot2 even at100. Keep all current process/release/health/source/strategy/time checks. New snapshots require report capital/currency/experiment/account hash to match startup and launch pins; changed rationale at unchanged capital also fails.
- `make_snapshot` keeps its public signature and six-file body. For versioned workers, bind to the current selected account and read its account bytes before/after capture without exporting/overwriting the supervisor's pinned local account. Reject selection/account replacement during snapshot.
- Both watcher validations (initial snapshot and post-publication refresh) receive selected launch/account expectations. `_assert_launched_owner` also rechecks the local selected account binding before access renewal, usage transport or model review.
- Check all four remote transports at50/200; malformed/missing local account after entering launch must block before access/model/usage. Verify actual selected worker snapshots and selected publication propagation across all8accounts. Keep v1 legacy fixtures intact.
- Venue candidate has its own profile loader, so validate selection/report there directly without recursing through `_profiles -> validate_activation_venue`.
- Schema1 supervisor limits additionally carry the selected capital, selection hash and account hash for new launches. The existing lifecycle hash already binds the full limits object; this prevents selection replacement while retaining counters without changing the lifecycle container version.
- A selected archive certificate validates both persisted risk and the packet's earlier risk against the selection (they describe different stages and need not be equal), the authoritative inception and all8opening copies, and the selected controller account/limits/retained lifecycle without admitting a new cycle.
- Sealed recovery reads use the existing deadline-aware immutable SQLite opener. Live opening reads retain committed-WAL semantics. A versioned inception read through the original ordinary read-only SQLite URI created16WAL/SHM sidecars in an exported cohort; no sealed recovery check may add those files or use an unbounded opening connection.
- New-version publication admission must establish the supplied state directory is the selected primary and validate its risk identity; absent shared DB cannot silently grant publication to a secondary. Standalone legacy publication semantics stay unchanged.

The exact-cut verifier stays non-authorizing. The separate recovery lifecycle needs external current-cut evidence, verifiable former-host/process stop and complete final original claims; receiver epochs cannot revoke an isolated worker's cached offline credit. Initial beta recovery may require operator reconciliation and remain blocked on ambiguity. Universal automatic failover is not added here.

Verified October19:1,482Windows tests/1,479pass/3platform skips,zeroResourceWarnings,253pinsunchanged; independent review and one correction pass complete. Current inactive artifact is `research/runs/primary-wallet-protocol-v1/candidate-reviewed`; `candidate` is preserved pre-review evidence. No current Linux qualification, deployment, activation or profitability claim. All implementation-plan tasks complete within the approved disabled local scope; the full recovery goal remains active.
