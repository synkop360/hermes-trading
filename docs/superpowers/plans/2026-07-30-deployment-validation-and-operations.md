# Deployment Validation And Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verifier le systeme sous panne, le deployer de facon reproductible sur Railway et conduire le rollout progressif sans activer automatiquement le trading reel.

**Architecture:** Un moteur de replay deterministe teste les decisions sur des donnees datees sans lookahead. Une campagne de chaos injecte les pannes aux frontieres externes. Le worker et le bot utilisent deux images et services Railway distincts, un stockage persistant sauvegarde et un bail de leader unique. Les runbooks et les artefacts humains permettent le diagnostic meme si l'application ne demarre plus.

**Tech Stack:** Python 3.11, SQLite WAL, Docker, Railway, GitHub Actions, Kraken Spot REST, Discord, `unittest`.

## Global Constraints

- Un seul worker peut executer ou reconcilier des ordres a un instant donne.
- Le bot et le worker sont des services, secrets et images distincts.
- Le worker tourne sans privilege root et avec dependances verrouillees.
- Les cles Kraken n'autorisent jamais les retraits.
- Les sauvegardes SQLite sont coherentes et leur restauration est testee.
- Aucun test de rollout ne peut envoyer un ordre reel avant approbation explicite.
- Une panne genere un etat humain lisible, meme quand Discord est indisponible.
- Les couts Railway et IA sont surveilles avec avertissement et plafond d'arret configurable.

---

## File Structure

- Create `hermes_trading/replay.py`: replay deterministe et garde anti-lookahead.
- Create `hermes_trading/chaos.py`: doubles de panne reutilisables.
- Create `hermes_trading/lease.py`: bail exclusif du worker.
- Create `hermes_trading/backup.py`: sauvegarde, verification et restauration.
- Create `scripts/run_replay.py`, `scripts/run_qualification.py`,
  `scripts/backup_state.py`, `scripts/restore_state.py`,
  `scripts/live_readiness.py`.
- Create `tests/test_replay.py`, `tests/test_chaos.py`,
  `tests/test_worker_lease.py`, `tests/test_backup.py`,
  `tests/test_live_readiness.py`.
- Modify `Dockerfile`; create `Dockerfile.bot`.
- Create `railway.worker.toml`, `railway.bot.toml`.
- Create `.github/workflows/ci.yml`.
- Modify `pyproject.toml`, `uv.lock`, `.gitignore`, `README.md`.
- Create or revise `docs/SAFETY.md`, `docs/KRAKEN.md`, `docs/HERMES.md`,
  `docs/NOTIFICATIONS.md`, `docs/OPERATIONS.md`, `docs/STATE_FILES.md`,
  `docs/TROUBLESHOOTING.md`, `docs/DEPLOYMENT.md`, `docs/COMMANDS.md`.

### Task 1: Add Deterministic Replay Without Lookahead

**Files:**
- Create: `hermes_trading/replay.py`
- Create: `scripts/run_replay.py`
- Create: `tests/test_replay.py`

**Interfaces:**
- Produces: `ReplayClock`, `ReplayMarketSource`, `ReplayResult`,
  `ReplayRunner.run()`.
- Consumes: versioned candle fixtures, champion strategy snapshot, fee/slippage model.

- [ ] **Step 1: Write failing determinism and lookahead tests**

```python
class ReplayTests(unittest.TestCase):
    def test_same_inputs_produce_same_decisions_and_pnl(self):
        first = fixture_runner(seed=7).run()
        second = fixture_runner(seed=7).run()
        self.assertEqual(first.decision_hash, second.decision_hash)
        self.assertEqual(first.net_pnl, second.net_pnl)

    def test_strategy_cannot_read_future_candle(self):
        source = ReplayMarketSource(CANDLES)
        source.advance_to(CANDLES[2].closed_at)
        with self.assertRaises(LookaheadViolation):
            source.candle_at(CANDLES[3].closed_at)

    def test_replay_charges_fees_and_slippage(self):
        result = fixture_runner().run()
        self.assertLess(result.net_pnl, result.gross_pnl)
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_replay -v`

Expected: replay types are missing.

- [ ] **Step 3: Implement replay around production domain services**

Use the production StrategySignal, RiskEngine and PositionEngine with injected
clock, market source and fake ExchangeGateway. Do not duplicate trading logic.
Persist the strategy hash, fixture hash, seed and code version in ReplayResult.

- [ ] **Step 4: Add invariants and report artifacts**

Fail the run on:

- future candle access;
- duplicate decision for a closed candle;
- negative quantity or cash;
- position exceeding approved exposure;
- entry without a modeled stop;
- mismatch between fills and position ledger.

Write `state/replay-latest.json` and `state/replay-latest.md`.

- [ ] **Step 5: Add a command-line runner**

```bash
uv run python scripts/run_replay.py --strategy state/strategy-champion.yaml --candles tests/fixtures/replay --output state
```

Exit `0` when all invariants pass, `1` for failed qualification and `2` for an
invalid setup. Always print the Markdown report path.

- [ ] **Step 6: Run tests and commit**

Run: `uv run python -m unittest tests.test_replay -v`

Expected: all replay tests pass.

```bash
git add hermes_trading/replay.py scripts/run_replay.py tests/test_replay.py
git commit -m "test: add deterministic trading replay"
```

### Task 2: Exercise External Failure Boundaries

**Files:**
- Create: `hermes_trading/chaos.py`
- Create: `tests/test_chaos.py`
- Modify: `hermes_trading/notifications.py`

**Interfaces:**
- Produces controllable failures for Kraken, Hermes, OpenAI, Discord, Railway
  cost API, filesystem and process restart.

- [ ] **Step 1: Write the failure matrix as failing tests**

```python
class ChaosTests(unittest.TestCase):
    def test_kraken_timeout_after_submit_reconciles_before_retry(self):
        gateway = ChaosGateway(timeout_after_accept=True)
        result = scenario(gateway=gateway).run_one_cycle()
        self.assertEqual(gateway.create_calls, 1)
        self.assertEqual(result.order_state, "reconciled")

    def test_all_models_unavailable_keeps_trading_champion(self):
        result = scenario(models=AllUnavailable()).run_reflection()
        self.assertEqual(result.strategy_role, "champion")
        self.assertEqual(result.status, "reflection_degraded")

    def test_discord_failure_still_writes_critical_alert(self):
        result = scenario(discord=Unavailable()).trigger_low_funds()
        self.assertTrue(result.human_alert_path.exists())

    def test_unknown_railway_cost_blocks_regular_live(self):
        result = scenario(railway_cost=Unavailable()).evaluate_live()
        self.assertFalse(result.promotable)
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_chaos -v`

Expected: chaos doubles and some degraded paths are missing.

- [ ] **Step 3: Cover the complete boundary matrix**

Add scenarios for:

- Kraken timeout before/after acceptance, rate limit, invalid nonce,
  partial fill, rejected stop and stale balance;
- SQLite busy, disk full during artifact projection and restart after commit;
- Hermes malformed YAML, timeout and all free models limited;
- OpenAI unavailable after Hermes exhaustion;
- Discord bot offline, webhook offline and duplicate delivery;
- Railway cost API missing or delayed;
- clock drift breaking HMAC/TOTP;
- two workers attempting leadership;
- malformed configuration and missing secret.

- [ ] **Step 4: Assert defensive behavior**

Each scenario must prove one of: no new entry, reconcile before retry,
champion preserved, phase paused, critical local alert written, or emergency
exit attempted. No scenario may silently continue as healthy.

- [ ] **Step 5: Run boundary and full tests**

Run: `uv run python -m unittest tests.test_chaos -v`

Expected: all injected failures have deterministic outcomes.

Run: `uv run python -m unittest discover -s tests -v`

Expected: complete suite passes.

- [ ] **Step 6: Commit**

```bash
git add hermes_trading/chaos.py hermes_trading/notifications.py tests/test_chaos.py
git commit -m "test: cover trading boundary failures"
```

### Task 3: Enforce One Worker And Recoverable State

**Files:**
- Create: `hermes_trading/lease.py`
- Create: `hermes_trading/backup.py`
- Create: `scripts/backup_state.py`
- Create: `scripts/restore_state.py`
- Modify: `hermes_trading/db.py`
- Modify: `hermes_trading/loop.py`
- Test: `tests/test_worker_lease.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Produces: `WorkerLease.acquire()`, `renew()`, `release()`,
  `StateBackup.create()`, `verify()`, `restore()`.

- [ ] **Step 1: Write failing lease and takeover tests**

```python
class WorkerLeaseTests(unittest.TestCase):
    def test_second_live_worker_cannot_start(self):
        first = WorkerLease(self.repo, "worker-a", ttl=timedelta(seconds=30))
        second = WorkerLease(self.repo, "worker-b", ttl=timedelta(seconds=30))
        first.acquire(now=NOW)
        with self.assertRaises(LeaseHeld):
            second.acquire(now=NOW)

    def test_takeover_requires_expired_lease_and_reconciliation(self):
        fixture_lease("worker-a", expires_at=NOW - timedelta(seconds=1))
        acquired = self.service.start("worker-b", now=NOW)
        self.assertTrue(acquired)
        self.reconciler.reconcile_all.assert_called_once()
```

- [ ] **Step 2: Write failing backup/restore tests**

```python
class BackupTests(unittest.TestCase):
    def test_online_backup_is_verified_and_has_manifest(self):
        backup = StateBackup(self.repo).create(self.destination, now=NOW)
        self.assertTrue(backup.manifest_path.exists())
        self.assertTrue(StateBackup.verify(backup))

    def test_restore_refuses_hash_mismatch(self):
        backup = fixture_backup()
        corrupt(backup.database_path)
        with self.assertRaises(BackupIntegrityError):
            StateBackup.restore(backup, self.target)
```

- [ ] **Step 3: Implement transactional lease semantics**

Renew the lease before every cycle and during long reconciliation. A worker
that loses the lease stops new work immediately. On takeover, reconcile Kraken
and local state before processing signals.

- [ ] **Step 4: Implement consistent backup and explicit restore**

Use Python's SQLite online backup API, then run `PRAGMA integrity_check`.
Manifest fields include schema version, UTC time, database SHA-256, artifact
cursor and strategy/config hashes. Restore only into a stopped worker and keep
the replaced database as a timestamped rollback file.

- [ ] **Step 5: Add retention and JSONL rotation**

Rotate human JSONL by UTC month, retain the configured number of months and
never delete the SQLite canonical state during rotation. Backups use daily,
weekly and monthly retention buckets configurable in strategy YAML.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run python -m unittest tests.test_worker_lease tests.test_backup tests.test_loop -v
```

Expected: exclusivity, takeover and restore tests pass.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/lease.py hermes_trading/backup.py hermes_trading/db.py hermes_trading/loop.py scripts/backup_state.py scripts/restore_state.py tests/test_worker_lease.py tests/test_backup.py
git commit -m "feat: add worker lease and verified backups"
```

### Task 4: Make Railway Builds Reproducible And Isolated

**Files:**
- Modify: `Dockerfile`
- Create: `Dockerfile.bot`
- Create: `railway.worker.toml`
- Create: `railway.bot.toml`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.gitignore`
- Test: `tests/test_live_readiness.py`

**Interfaces:**
- Worker command: `uv run hermes-worker` or existing equivalent entry point.
- Bot command: `uv run hermes-discord-bot`.
- Worker private control port and health endpoint; bot health endpoint.

- [ ] **Step 1: Write failing deployment contract tests**

```python
class DeploymentContractTests(unittest.TestCase):
    def test_images_install_from_lockfile(self):
        self.assertDockerContains("Dockerfile", "uv sync --frozen --no-dev")
        self.assertDockerContains("Dockerfile.bot", "uv sync --frozen --no-dev")

    def test_worker_runs_as_non_root(self):
        self.assertDockerHasNonRootUser("Dockerfile")

    def test_bot_definition_does_not_declare_kraken_secrets(self):
        text = Path("railway.bot.toml").read_text()
        self.assertNotIn("KRAKEN_API", text)
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_live_readiness.DeploymentContractTests -v`

Expected: bot image and Railway definitions are absent.

- [ ] **Step 3: Build minimal locked images**

Use multi-stage images, copy `uv.lock` and `pyproject.toml` before source,
install with `--frozen --no-dev`, then run as a numeric non-root user. The bot
image copies only bot code plus the shared signing/schema modules it needs.

- [ ] **Step 4: Define two Railway services**

Worker:

- one replica only;
- persistent volume mounted at `/data`;
- private control API, no public domain;
- restart policy and health check;
- conservative CPU/RAM limits;
- no preview deployment connected to production secrets.

Bot:

- no persistent trading state;
- private connection to worker;
- Discord/HMAC/TOTP secrets only;
- separate cost attribution label.

- [ ] **Step 5: Pin dependencies and ignore runtime state**

Lock FastAPI, Uvicorn, discord.py and PyOTP. Ignore local SQLite, WAL/SHM,
backups, usage files and generated state while keeping fixtures tracked.

- [ ] **Step 6: Build and inspect both images**

Run:

```bash
docker build -f Dockerfile -t hermes-worker:local .
docker build -f Dockerfile.bot -t hermes-bot:local .
docker inspect hermes-worker:local --format '{{.Config.User}}'
docker inspect hermes-bot:local --format '{{.Config.User}}'
```

Expected: both builds pass and both users are non-root.

- [ ] **Step 7: Run tests and commit**

Run: `uv run python -m unittest tests.test_live_readiness.DeploymentContractTests -v`

Expected: deployment contracts pass.

```bash
git add Dockerfile Dockerfile.bot railway.worker.toml railway.bot.toml pyproject.toml uv.lock .gitignore tests/test_live_readiness.py
git commit -m "build: isolate Railway worker and Discord bot"
```

### Task 5: Automate CI, Readiness And Cost Guardrails

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `scripts/run_qualification.py`
- Create: `scripts/live_readiness.py`
- Modify: `hermes_trading/profitability.py`
- Modify: `hermes_trading/notifications.py`
- Test: `tests/test_live_readiness.py`

**Interfaces:**
- Produces a stable readiness exit code and Markdown/JSON report.
- Adds configurable monthly warning and hard cost ceilings.

- [ ] **Step 1: Write failing readiness tests**

```python
class LiveReadinessTests(unittest.TestCase):
    def test_missing_kraken_permission_check_blocks_validation(self):
        report = fixture_readiness(kraken_permissions=None).run()
        self.assertFalse(report.ready)
        self.assertIn("permissions inconnues", report.markdown)

    def test_hard_cost_ceiling_pauses_new_entries(self):
        report = fixture_readiness(monthly_cost="31", hard_ceiling="30").run()
        self.assertTrue(report.pause_new_entries)
        self.assertEqual(report.severity, "critical")

    def test_micro_live_report_shows_profitability_without_blocking_on_it(self):
        report = fixture_readiness(phase="micro_live_btc", projected_net="-1").run()
        self.assertNotIn("profitability", report.blocking_gates)
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_live_readiness -v`

Expected: readiness runner and cost ceiling behavior are incomplete.

- [ ] **Step 3: Implement Kraken credential readiness**

With a dedicated Spot key:

- authenticate and query balance/open orders;
- validate that trading permission is present;
- document and manually verify that withdrawal permission is absent;
- verify system time and nonce behavior;
- execute Kraken validate-only for the allowed symbols and order types;
- never send a real order from this script.

Redact key ids and account details from artifacts.

- [ ] **Step 4: Implement cost warning and hard ceiling**

Use actual Railway project/service usage when available and conservative
configured estimates otherwise. Include worker, bot, Kraken fees/slippage and
model/token costs. Unknown cost blocks regular live. The warning sends an
amber alert; crossing the hard ceiling pauses new entries and sends a red
alert, while exits and reconciliation stay active.

- [ ] **Step 5: Create the qualification and readiness commands**

```bash
uv run python scripts/run_qualification.py --notify-discord
uv run python scripts/live_readiness.py --phase kraken_validate_only
```

Both write atomic JSON and Markdown reports. Qualification can request an
approval but cannot grant it.

- [ ] **Step 6: Add CI**

CI runs:

- formatting/static checks already used by the repository;
- complete unittest suite;
- replay invariants;
- deployment contract tests;
- secret-pattern scan of tracked files;
- Docker builds without production secrets.

External Kraken, Discord, Hermes and Railway tests use fakes in CI. Live
readiness runs only as an explicitly approved Railway job.

- [ ] **Step 7: Run local CI-equivalent checks**

Run:

```bash
uv run python -m unittest discover -s tests -v
uv run python scripts/run_replay.py --strategy state/strategy-champion.yaml --candles tests/fixtures/replay --output state
docker build -f Dockerfile -t hermes-worker:local .
docker build -f Dockerfile.bot -t hermes-bot:local .
```

Expected: tests, replay and images pass.

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/ci.yml scripts/run_qualification.py scripts/live_readiness.py hermes_trading/profitability.py hermes_trading/notifications.py tests/test_live_readiness.py
git commit -m "ci: automate qualification and cost readiness"
```

### Task 6: Write Human-First Operations Documentation

**Files:**
- Modify: `README.md`
- Create or modify: `docs/SAFETY.md`
- Create or modify: `docs/KRAKEN.md`
- Create or modify: `docs/HERMES.md`
- Create or modify: `docs/NOTIFICATIONS.md`
- Create or modify: `docs/OPERATIONS.md`
- Create or modify: `docs/STATE_FILES.md`
- Create or modify: `docs/TROUBLESHOOTING.md`
- Create or modify: `docs/DEPLOYMENT.md`
- Create or modify: `docs/COMMANDS.md`

- [ ] **Step 1: Inventory current documentation**

Run:

```bash
rg -n "training|validation|live|Kraken|Discord|Railway|OpenAI|Hermes|backup|restore" README.md docs
```

Expected: identify stale commands and missing operational sections.

- [ ] **Step 2: Document safety and phase boundaries**

`SAFETY.md` defines every phase, invariant, stop condition, risk limit and who
may approve promotion. State plainly that training results do not prove future
profit and that micro-live uses real funds.

- [ ] **Step 3: Document Kraken and secrets**

`KRAKEN.md` covers creating a dedicated Spot key, required read/trade
permissions, forbidden withdrawal permission, validation-only, hosted stops,
key rotation and revocation. Never include a real key or account identifier.

- [ ] **Step 4: Document Hermes routing and costs**

`HERMES.md` explains free model auto-discovery, cooldowns, Hermes-first
behavior, OpenAI last-resort fallback, usage files and how to recognize a
degraded reflection without mistaking it for a trading outage.

- [ ] **Step 5: Document notifications and Discord commands**

`NOTIFICATIONS.md` maps severities to message titles/colors, summary
frequencies, low-funds alerts and fallback behavior. `COMMANDS.md` lists every
read/mutation command, confirmation/TOTP requirements and examples with fake
values.

- [ ] **Step 6: Document recovery without the application**

`STATE_FILES.md` explains SQLite as canonical and the purpose of every
JSONL/JSON/Markdown artifact. `TROUBLESHOOTING.md` starts with commands that
work when Python fails: locate the latest Markdown alert, inspect the health
file, verify disk space and identify the active backup.

- [ ] **Step 7: Document deployment and disaster recovery**

`DEPLOYMENT.md` and `OPERATIONS.md` cover two Railway services, private
networking, one worker replica, health checks, cost limits, backup schedule,
restore drill, lease takeover, rollback and emergency stop.

- [ ] **Step 8: Check documentation links and forbidden placeholders**

Run:

```bash
rg -n "TODO|TBD|FIXME|<your-|changeme|real-api-key" README.md docs
```

Expected: no unfinished instructions or secret-like examples.

- [ ] **Step 9: Commit**

```bash
git add README.md docs/SAFETY.md docs/KRAKEN.md docs/HERMES.md docs/NOTIFICATIONS.md docs/OPERATIONS.md docs/STATE_FILES.md docs/TROUBLESHOOTING.md docs/DEPLOYMENT.md docs/COMMANDS.md
git commit -m "docs: add live trading operations runbooks"
```

### Task 7: Deploy Training Services And Start The Gated Rollout

**Files:**
- Modify: `strategy.yaml`
- Modify: `README.md`
- Runtime: Railway worker and bot services, Discord application and secrets.

- [ ] **Step 1: Capture a pre-deployment baseline**

Record current Railway deployment id, strategy/config hashes, database backup,
open positions/orders, monthly cost estimate and the last healthy cycle.
Confirm the current worker is in training and no live order path is enabled.

- [ ] **Step 2: Configure secrets with least privilege**

Worker receives Kraken Spot read/trade key, Hermes credentials, optional
OpenAI fallback, Discord webhook fallback, Railway cost access, control HMAC
key and TOTP secret. Bot receives only Discord, private worker and HMAC values;
it forwards TOTP input without keeping the seed. Rotate any secret previously
exposed in logs or local documentation.

- [ ] **Step 3: Deploy worker first in training**

Deploy one worker replica with the persistent volume. Verify:

- lease acquired;
- database migrated and backup valid;
- reconciliation complete;
- health and human status current;
- Hermes attempted before OpenAI;
- no real order submitted.

Rollback immediately on migration, reconciliation or invariant failure.

- [ ] **Step 4: Deploy the bot and verify controls**

Verify allowlisted read commands, denied unauthorized access, pause
confirmation, invalid TOTP rejection, worker-unavailable display and webhook
critical fallback. Do not test promotion with real phase change yet.

- [ ] **Step 5: Run the automated training campaign**

Keep the approved strategy unchanged for at least 14 days and 50 closed
positions. Discord publishes progress and the final gate report. Any strategy
change resets the relevant evidence window.

- [ ] **Step 6: Approve and run validation-only**

After Discord approval, run 48 hours of authenticated Kraken validation-only.
Verify every allowed symbol, sizing, minimum order, hosted stop payload,
idempotency and reconciliation without placing an order.

- [ ] **Step 7: Request BTC micro-live approval**

Generate the final readiness report and require explicit Discord confirmation
plus TOTP. The report must show maximum possible loss, current funds,
projected/stressed economics, monthly costs and rollback command.

This plan stops before sending the first real order. Execution requires a new,
explicit user approval after reviewing that report.

- [ ] **Step 8: Continue the approved symbol rollout**

After separate approvals:

1. BTC micro-live for at least 7 days and 10 closed positions;
2. BTC regular live if economic gates pass;
3. add ETH, then XRP, then SHIB, one qualification window at a time.

Any critical invariant breach pauses new entries. Defensive strategy rollback
may be automatic; phase promotion and risk increase remain manual.

- [ ] **Step 9: Run final verification and update Graphify**

Run:

```bash
uv run python -m unittest discover -s tests -v
uv run python scripts/live_readiness.py --phase kraken_validate_only
graphify update .
git diff --check
```

Expected: tests pass, readiness report is explicit, Graphify is current and
the diff has no whitespace errors.

- [ ] **Step 10: Commit rollout configuration only after validation**

```bash
git add strategy.yaml README.md graphify-out
git commit -m "ops: enable gated validation-only rollout"
```
