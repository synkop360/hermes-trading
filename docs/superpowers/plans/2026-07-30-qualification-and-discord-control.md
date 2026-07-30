# Qualification And Discord Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatiser la qualification avant live et fournir un vrai bot Discord de pilotage, sans exposer les cles Kraken ni permettre une augmentation de risque sans double validation.

**Architecture:** QualificationService evalue des preuves persistantes et ne fait que proposer un changement de phase. ControlService est l'unique facade de commandes du worker. Une API privee signee HMAC expose cette facade au bot Discord, deploye dans un service Railway separe. Le worker revalide l'identite, l'idempotence, la phase, la confirmation et le TOTP avant toute action sensible.

**Tech Stack:** Python 3.11, SQLite WAL, FastAPI, Uvicorn, HTTPX, discord.py, PyOTP, `Decimal`, `unittest`.

## Global Constraints

- Le bot Discord ne recoit jamais de cle Kraken.
- Une commande Discord ne contourne jamais RiskEngine, ProfitabilityService ou les gates de qualification.
- Les lectures utilisent une allowlist Discord; les mutations utilisent allowlist, confirmation et idempotence.
- Une action augmentant le risque exige en plus un TOTP valide, a usage unique dans la fenetre acceptee.
- Aucune promotion vers micro-live ou live n'est automatique.
- Un rapport lisible est toujours produit, y compris en cas d'erreur partielle.
- Les secrets, signatures, TOTP et corps d'erreur fournisseur ne sont jamais journalises.
- Le webhook Discord existant reste disponible pour les alertes critiques si le bot est indisponible.

---

## File Structure

- Create `hermes_trading/qualification.py`: phases, gates et rapports.
- Create `hermes_trading/qualification_runner.py`: campagnes et notifications.
- Create `hermes_trading/control.py`: commandes locales et distantes.
- Create `hermes_trading/control_auth.py`: signatures HMAC et anti-rejeu.
- Create `hermes_trading/control_api.py`: API privee du worker.
- Create `hermes_bot/__init__.py`, `hermes_bot/client.py`,
  `hermes_bot/security.py`, `hermes_bot/commands.py`, `hermes_bot/run.py`.
- Modify `hermes_trading/db.py`: migrations qualification, commandes et nonces.
- Modify `hermes_trading/notifications.py`: rapports et confirmations Discord.
- Modify `hermes_trading/loop.py`: points de controle pause et phase.
- Modify `pyproject.toml`: dependances et commandes.
- Create `tests/test_qualification.py`, `tests/test_qualification_runner.py`,
  `tests/test_control.py`, `tests/test_control_auth.py`,
  `tests/test_control_api.py`, `tests/test_discord_bot.py`.

### Task 1: Persist Qualification Phases And Evidence

**Files:**
- Create: `hermes_trading/qualification.py`
- Modify: `hermes_trading/db.py`
- Test: `tests/test_qualification.py`

**Interfaces:**
- Produces: `QualificationPhase`, `GateStatus`, `GateResult`,
  `QualificationSnapshot`, `QualificationService.evaluate()`.
- Persists: active phase, requested phase, strategy hash, evidence window,
  gate results and approval metadata.

- [ ] **Step 1: Write failing phase and evidence tests**

```python
class QualificationServiceTests(unittest.TestCase):
    def test_training_requires_fourteen_days_and_fifty_closed_positions(self):
        evidence = fixture_evidence(days=13, closed_positions=80)
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY, evidence, now=NOW
        )
        self.assertEqual(result.gate("training_duration").status, GateStatus.FAIL)
        self.assertFalse(result.promotable)

    def test_changed_strategy_invalidates_previous_evidence(self):
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY,
            fixture_evidence(strategy_hash="new"),
            now=NOW,
        )
        self.assertEqual(result.gate("strategy_unchanged").status, GateStatus.FAIL)

    def test_unknown_cost_blocks_live_but_not_training(self):
        evidence = fixture_evidence(costs_complete=False)
        self.assertFalse(
            self.service.evaluate(QualificationPhase.LIVE, evidence, now=NOW).promotable
        )
        self.assertTrue(
            self.service.evaluate(QualificationPhase.TRAINING, evidence, now=NOW).valid
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_qualification -v`

Expected: qualification types and persistence are missing.

- [ ] **Step 3: Add the phase model and immutable gate results**

```python
class QualificationPhase(str, Enum):
    TRAINING = "training"
    VALIDATION_READY = "validation_ready"
    KRAKEN_VALIDATE_ONLY = "kraken_validate_only"
    MICRO_LIVE_BTC = "micro_live_btc"
    LIVE_BTC = "live_btc"
    LIVE_ETH = "live_eth"
    LIVE_XRP = "live_xrp"
    LIVE_SHIB = "live_shib"
    PAUSED = "paused"

@dataclass(frozen=True)
class GateResult:
    name: str
    status: GateStatus
    observed: str
    required: str
    reason: str
```

Store every evaluation as a new snapshot. Its identity includes code version,
strategy version, SQLite schema version, data window, sanitized configuration
hash and result hash. Never overwrite the evidence used for an earlier
approval; changing any identity component makes it stale.

- [ ] **Step 4: Encode the approved gates**

Implement pure gate functions for:

- training to validation-ready: all automated tests pass, 14 elapsed days,
  50 independent closed positions, replay without lookahead, fees/slippage
  charged, no duplicate candle and exercised risk limits;
- Kraken validation-only: 48 hours without precision error, successful Kraken
  validation responses, no duplicated intent, restart/ambiguous-response tests
  passed, hosted stop validated and no real order sent;
- BTC micro-live: explicit Discord approval, risk caps and hosted stop ready;
- post micro-live: 7 days and 10 closed BTC positions;
- each symbol expansion: prior phase healthy and explicit approval;
- regular live: projected 30-day net positive, cost coverage at least `1.5`,
  stressed result positive with PnL `-20%` and costs `+25%`;
- all promotions: current strategy/config hashes match the evidence;
- micro-live: record profitability but do not make absolute profitability a gate.

- [ ] **Step 5: Add migrations and repository methods**

Create `qualification_snapshots`, `qualification_gates`,
`qualification_approvals` and `system_phase` tables. Use foreign keys,
UTC timestamps and transactional phase updates.

- [ ] **Step 6: Run focused and full tests**

Run: `uv run python -m unittest tests.test_qualification -v`

Expected: all qualification tests pass.

Run: `uv run python -m unittest discover -s tests -v`

Expected: existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/qualification.py hermes_trading/db.py tests/test_qualification.py
git commit -m "feat: persist qualification evidence and gates"
```

### Task 2: Build Qualification Campaigns And Human Reports

**Files:**
- Create: `hermes_trading/qualification_runner.py`
- Modify: `hermes_trading/notifications.py`
- Test: `tests/test_qualification_runner.py`

**Interfaces:**
- Consumes: repository metrics, reconciliation health, cost report, strategy hashes.
- Produces: `QualificationReport`, atomic Markdown/JSON artifacts, Discord summary.

- [ ] **Step 1: Write failing campaign tests**

```python
class QualificationRunnerTests(unittest.TestCase):
    def test_report_lists_pass_fail_and_missing_gates(self):
        report = self.runner.run(now=NOW)
        self.assertIn("PASS | training_duration", report.markdown)
        self.assertIn("MISSING | railway_cost", report.markdown)
        self.assertFalse(report.snapshot.promotable)

    def test_promotable_campaign_requests_approval_without_changing_phase(self):
        report = qualified_runner().run(now=NOW)
        self.assertTrue(report.snapshot.promotable)
        self.assertEqual(report.active_phase, QualificationPhase.TRAINING)
        self.notifier.send_qualification.assert_called_once()
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_qualification_runner -v`

Expected: runner and qualification notification do not exist.

- [ ] **Step 3: Implement evidence collection**

Collect all inputs through typed repository queries. A failed source creates a
`MISSING` gate with a human reason; it must not abort the report.

```python
report = QualificationRunner(
    repository=repo,
    qualification=QualificationService(repo),
    profitability=ProfitabilityService(repo),
    projector=ArtifactProjector(paths),
    notifier=notifier,
).run(now=clock.now())
```

- [ ] **Step 4: Publish readable artifacts and Discord messages**

Write atomically:

- `state/qualification-latest.json` for machines;
- `state/qualification-latest.md` for humans;
- append the evaluation event to JSONL.

The Discord message starts with the requested phase and overall status, then
one line per failing or missing gate, followed by the approval request only
when promotable. A promotion request includes current/requested phase, all
mandatory checks, residual risks, current/projected cost, normal/stressed
profitability, code/strategy versions and expiry. It offers explicit approve
and refuse actions.

- [ ] **Step 5: Add cycle and scheduled execution**

Allow the runner to execute:

- every configurable number of worker cycles;
- at standard intervals parsed by the existing schedule parser;
- on demand through ControlService.

The trading cycle only enqueues a due campaign; evidence collection, Markdown
projection and Discord delivery run outside the critical trading path.
Deduplicate identical reports within the configured quiet period.

- [ ] **Step 6: Run tests**

Run: `uv run python -m unittest tests.test_qualification_runner tests.test_notifications -v`

Expected: reports and existing notifications pass.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/qualification_runner.py hermes_trading/notifications.py tests/test_qualification_runner.py
git commit -m "feat: automate qualification reports"
```

### Task 3: Create The Worker Control Service

**Files:**
- Create: `hermes_trading/control.py`
- Modify: `hermes_trading/loop.py`
- Modify: `hermes_trading/db.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Produces: `ControlCommand`, `ControlResult`, `ControlService.execute()`.
- Supported reads: `status`, `positions`, `orders`, `pnl`, `costs`,
  `qualification`, `strategy`.
- Supported mutations: `pause`, `resume`, `run_qualification`,
  `request_phase`, `confirm_phase`, `rollback_strategy`, `emergency_close`.

- [ ] **Step 1: Write failing command and idempotency tests**

```python
class ControlServiceTests(unittest.TestCase):
    def test_duplicate_request_returns_original_result(self):
        command = command_fixture("pause", request_id="same")
        first = self.service.execute(command, now=NOW)
        second = self.service.execute(command, now=NOW + timedelta(seconds=1))
        self.assertEqual(first, second)
        self.assertEqual(self.repo.count_command_executions("same"), 1)

    def test_request_phase_does_not_promote(self):
        result = self.service.execute(command_fixture("request_phase"), now=NOW)
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(self.repo.active_phase(), QualificationPhase.TRAINING)

    def test_emergency_close_still_uses_execution_and_risk_services(self):
        self.service.execute(command_fixture("emergency_close"), now=NOW)
        self.execution.emergency_close_all.assert_called_once()
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_control -v`

Expected: control facade is missing.

- [ ] **Step 3: Implement a registry with explicit command policy**

```python
COMMANDS = {
    "status": CommandPolicy(read_only=True),
    "pause": CommandPolicy(mutating=True, confirmation=True),
    "request_phase": CommandPolicy(mutating=True, confirmation=True),
    "confirm_phase": CommandPolicy(
        mutating=True, confirmation=True, totp=True, increases_risk=True
    ),
    "emergency_close": CommandPolicy(
        mutating=True, confirmation=True, increases_risk=False
    ),
}
```

Reject unknown parameters. Store the sanitized request, actor, outcome and
correlation id. A duplicate `request_id` returns the recorded outcome.

- [ ] **Step 4: Integrate pause and phase checks into the loop**

At the start of each cycle, load system control state. `PAUSED` may collect
market data and reconcile open positions, but it may not create new entries.
Emergency exits and hosted stop monitoring remain active.

- [ ] **Step 5: Revalidate every sensitive command**

`confirm_phase` reloads the latest qualification snapshot, hashes, account
state, risk limits and profitability immediately before the transaction that
changes phase. A stale or failed gate rejects the command.

- [ ] **Step 6: Run tests**

Run: `uv run python -m unittest tests.test_control tests.test_loop -v`

Expected: control and loop tests pass.

- [ ] **Step 7: Commit**

```bash
git add hermes_trading/control.py hermes_trading/loop.py hermes_trading/db.py tests/test_control.py
git commit -m "feat: add idempotent worker control service"
```

### Task 4: Expose A Private Signed Control API

**Files:**
- Create: `hermes_trading/control_auth.py`
- Create: `hermes_trading/control_api.py`
- Modify: `pyproject.toml`
- Test: `tests/test_control_auth.py`
- Test: `tests/test_control_api.py`

**Interfaces:**
- `GET /health`: liveness without secrets.
- `GET /v1/status`: signed read.
- `POST /v1/commands`: signed command envelope.
- Headers: `X-Hermes-Timestamp`, `X-Hermes-Nonce`,
  `X-Hermes-Signature`, `X-Hermes-Key-Id`.

- [ ] **Step 1: Write failing HMAC and replay tests**

```python
class ControlAuthTests(unittest.TestCase):
    def test_signature_binds_method_path_timestamp_nonce_and_body(self):
        signed = sign_request(self.key, "POST", "/v1/commands", NOW, "n1", BODY)
        verify_request(self.repo, self.keyring, signed, body=BODY, now=NOW)

    def test_nonce_replay_is_rejected(self):
        request = signed_fixture(nonce="used")
        verify_request(self.repo, self.keyring, request, body=BODY, now=NOW)
        with self.assertRaises(ReplayDetected):
            verify_request(self.repo, self.keyring, request, body=BODY, now=NOW)

    def test_stale_timestamp_is_rejected(self):
        with self.assertRaises(StaleSignature):
            verify_request(..., now=NOW + timedelta(minutes=6))
```

- [ ] **Step 2: Write failing API contract tests**

```python
class ControlApiTests(unittest.TestCase):
    def test_unsigned_status_is_unauthorized(self):
        self.assertEqual(self.client.get("/v1/status").status_code, 401)

    def test_valid_command_returns_sanitized_result(self):
        response = self.post_signed(
            "/v1/commands",
            {"command": "status", "request_id": "r1", "parameters": {}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secret", response.text.lower())
```

- [ ] **Step 3: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_control_auth tests.test_control_api -v`

Expected: auth and API modules are missing.

- [ ] **Step 4: Implement canonical signing and key rotation**

Sign:

```text
METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(BODY)
```

Use `hmac.compare_digest`, a five-minute maximum age, persisted nonce
consumption and a key id allowing overlap during rotation. The database
transaction consumes the nonce before command execution.

- [ ] **Step 5: Implement the FastAPI app**

Use dependency injection so tests provide fake ControlService and clock.
Return stable error codes, a correlation id and a human message. Do not return
tracebacks, provider payloads, configuration or environment values.

- [ ] **Step 6: Bind only to Railway private networking**

Add the entry point:

```toml
[project.scripts]
hermes-control-api = "hermes_trading.control_api:run"
```

The deployment plan must set the bot URL to the worker private hostname. The
API is not exposed through a public Railway domain.

- [ ] **Step 7: Run tests**

Run: `uv run python -m unittest tests.test_control_auth tests.test_control_api -v`

Expected: all auth/API tests pass.

- [ ] **Step 8: Commit**

```bash
git add hermes_trading/control_auth.py hermes_trading/control_api.py pyproject.toml tests/test_control_auth.py tests/test_control_api.py
git commit -m "feat: expose signed private control API"
```

### Task 5: Build The Read-Only Discord Bot

**Files:**
- Create: `hermes_bot/__init__.py`
- Create: `hermes_bot/client.py`
- Create: `hermes_bot/security.py`
- Create: `hermes_bot/commands.py`
- Create: `hermes_bot/run.py`
- Modify: `pyproject.toml`
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- Slash commands: `/hermes status`, `/hermes positions`, `/hermes orders`,
  `/hermes risk`, `/hermes pnl`, `/hermes costs`, `/hermes qualification`,
  `/hermes strategy`, `/hermes models`, `/hermes incidents`.
- `WorkerClient`: signs and sends private API requests.

- [ ] **Step 1: Write failing allowlist and formatting tests**

```python
class DiscordBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_allowlisted_user_gets_no_private_information(self):
        response = await self.commands.status(interaction_fixture(user_id=999))
        self.assertTrue(response.ephemeral)
        self.assertEqual(response.message, "Acces refuse.")
        self.worker.assert_not_awaited()

    async def test_status_is_one_screen_and_human_readable(self):
        response = await allowed_commands().status(interaction_fixture())
        self.assertIn("Phase", response.message)
        self.assertIn("Dernier cycle", response.message)
        self.assertLessEqual(len(response.message), 1900)

    async def test_worker_unavailable_returns_visible_degraded_status(self):
        self.worker.status.side_effect = WorkerUnavailable()
        response = await self.commands.status(interaction_fixture())
        self.assertIn("INDISPONIBLE", response.message)
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_discord_bot -v`

Expected: bot package is missing.

- [ ] **Step 3: Implement the signed worker client**

The bot reads only these secrets:

- Discord bot token and guild id;
- allowed Discord user/role ids;
- private worker URL;
- HMAC key id and key;
- no TOTP seed: the worker alone validates TOTP in Task 6.

It never reads Kraken, Hermes or OpenAI credentials.

- [ ] **Step 4: Implement slash commands and severity formatting**

Use Discord embeds with stable colors:

- green: healthy/information;
- amber: degraded, approval pending or low warning;
- red: blocked, critical funds, worker unavailable or emergency.

Put the severity and status in the title so visibility does not depend on
color. Use one line per symbol for positions/PnL and explicit zero values.

- [ ] **Step 5: Add bot health and reconnect behavior**

Expose a local health endpoint or heartbeat file for Railway. Reconnect with
bounded exponential backoff. After recovery, send one recovery message and
avoid flooding the channel.

- [ ] **Step 6: Add the bot entry point**

```toml
[project.scripts]
hermes-discord-bot = "hermes_bot.run:main"
```

- [ ] **Step 7: Run tests**

Run: `uv run python -m unittest tests.test_discord_bot -v`

Expected: read commands, allowlist and degraded responses pass.

- [ ] **Step 8: Commit**

```bash
git add hermes_bot pyproject.toml tests/test_discord_bot.py
git commit -m "feat: add read-only Discord control bot"
```

### Task 6: Add Confirmed Mutations And TOTP

**Files:**
- Modify: `hermes_bot/security.py`
- Modify: `hermes_bot/commands.py`
- Modify: `hermes_trading/control.py`
- Modify: `hermes_trading/notifications.py`
- Test: `tests/test_discord_bot.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Adds: `/hermes pause`, `/hermes resume`, `/hermes qualify`,
  `/hermes promote`, `/hermes rollback`, `/hermes emergency-close`.
- Produces: short-lived `PendingConfirmation`, single-use approval challenge.

- [ ] **Step 1: Write failing confirmation and TOTP tests**

```python
class DiscordMutationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_requires_button_confirmation(self):
        pending = await self.commands.pause(allowed_interaction())
        self.assertEqual(pending.status, "confirmation_required")
        self.worker.command.assert_not_awaited()

    async def test_promotion_requires_valid_totp(self):
        pending = await self.commands.promote(allowed_interaction(), "live_btc")
        rejected = await pending.confirm(totp="000000")
        self.assertEqual(rejected.status, "totp_invalid")
        self.worker.command.assert_not_awaited()

    async def test_challenge_cannot_be_reused(self):
        pending = await valid_promotion_pending()
        await pending.confirm(totp=current_totp())
        reused = await pending.confirm(totp=current_totp())
        self.assertEqual(reused.status, "already_consumed")
```

- [ ] **Step 2: Verify tests fail**

Run: `uv run python -m unittest tests.test_discord_bot tests.test_control -v`

Expected: mutation policies and confirmation flow are missing.

- [ ] **Step 3: Implement short-lived Discord confirmations**

Bind each challenge to guild, channel, user, command, normalized parameters
and request id. Expire after five minutes. Only the initiating allowed user can
confirm. Disable the buttons after success, rejection or expiry.

- [ ] **Step 4: Implement TOTP without storing the seed in the bot database**

Provision the TOTP secret only as a worker Railway secret. The bot transports
the user-entered code without validating or persisting it. The worker validates
the code and records a hash of the accepted time-step to prevent reuse. Permit
one time step of clock drift and reject older steps.

- [ ] **Step 5: Map command sensitivity**

- `pause`, `run_qualification`: confirmation, no TOTP;
- `resume`: confirmation and fresh health gates;
- `rollback_strategy`: confirmation, no TOTP because it reduces risk;
- `emergency_close`: confirmation, no TOTP, never blocked by profitability;
- `confirm_phase` and any limit increase: confirmation plus TOTP.

Risk limits remain immutable through Discord in the first version.

- [ ] **Step 6: Confirm through Discord and webhook**

Every mutation returns an ephemeral immediate result and publishes one audit
message to the operations channel. Critical failures also use the existing
webhook fallback, with deduplication.

- [ ] **Step 7: Run security and integration tests**

Run:

```bash
uv run python -m unittest tests.test_discord_bot tests.test_control tests.test_control_auth tests.test_control_api -v
```

Expected: allowlist, confirmation, TOTP, replay and worker revalidation pass.

- [ ] **Step 8: Run the complete suite**

Run: `uv run python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 9: Update Graphify and commit**

Run: `graphify update .`

Expected: bot, control API and qualification relationships are indexed.

```bash
git add hermes_bot hermes_trading/control.py hermes_trading/notifications.py tests/test_discord_bot.py tests/test_control.py graphify-out
git commit -m "feat: secure Discord trading controls"
```
