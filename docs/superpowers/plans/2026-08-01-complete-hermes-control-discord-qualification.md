# Complete Hermes Control, Discord, And Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the implementation described by `docs/SESSION_HANDOFF_2026-08-01.md` while keeping Railway in training mode, exposing only safe control commands, and sending regular worker updates whenever an interesting feature becomes available.

**Architecture:** Keep the trading worker, private local control API, and Discord bot as separate services. The worker writes durable state and publishes safe progress notifications; the control API reads state and runs non-promoting qualification; the Discord bot authenticates Discord users with allowlist plus TOTP, then signs HMAC requests to the loopback-only control API. Qualification evidence remains explicit and typed: unavailable proofs are `missing`, not silently passing.

**Tech Stack:** Python 3.11, `uv`, SQLite/WAL, standard-library HTTP server for the private local API, `httpx`, `discord.py`, `unittest`, Railway, Discord webhook notifications.

## Global Constraints

- Repository path: `E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading`.
- Branch: `feat/live-hardening`.
- Railway project: `awake-abundance`, service `hermes-trading`, production environment.
- Railway volume: `/app/state`.
- Railway must remain `HERMES_TRADING_MODE=training`.
- Railway must remain `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- Never enable real Kraken orders or promote a phase automatically.
- After every Railway deploy, verify both variables over SSH.
- Current Railway configuration must preserve `HERMES_RAILWAY_MONTHLY_COST_USD=0.04`.
- Current Railway configuration must preserve `HERMES_RAILWAY_SUBSCRIPTION_ALLOCATION_PCT=100`.
- Current Railway configuration must preserve `HERMES_QUALIFICATION_EVERY_CYCLES=30`.
- Strategy is version `83` and assets are BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT.
- Remote scripts must use `uv run python`, not bare `python`.
- Do not delete or revert untracked `.test-tmp-*` folders or `graphify-out/`.
- Use `apply_patch` for edits and TDD for code changes.
- Run `graphify update .` after code changes.
- Worker updates must be sent when a useful capability becomes available: local control API ready, qualification evidence captured, Discord webhook summary enabled, Discord bot ready, and each post-deploy verification result.

---

## File Structure

- `hermes_trading/control_auth.py`: HMAC signing and verification; durable nonce replay via repository-backed store.
- `hermes_trading/control_api.py`: request handler for `GET /status` and `POST /qualification`.
- `hermes_trading/control_server.py`: loopback-only HTTP server construction and config validation.
- `hermes_trading/control_client.py`: local HMAC client for scripts and smoke tests.
- `hermes_trading/discord_bot.py`: Discord bot config, TOTP, HMAC client usage, and slash command adapter.
- `hermes_trading/qualification.py`: qualification model and typed evidence statuses.
- `hermes_trading/qualification_evidence.py`: typed collectors for replay, fees/slippage, duplicate candle protection, and risk limit exercises.
- `hermes_trading/qualification_runner.py`: gathers state evidence, records snapshots, writes latest reports.
- `hermes_trading/notifications.py`: Discord webhook formatting, including worker feature updates and qualification summaries.
- `hermes_trading/loop.py`: training loop scheduling for qualification and worker update notifications.
- `scripts/control_command.py`: signed local CLI for status and qualification smoke tests.
- `scripts/run_control_api.py`: starts the private local control API.
- `scripts/run_discord_bot.py`: starts the separate Discord bot service.
- `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`: operator runbook for the local API and bot.
- `Dockerfile`: copies `pyproject.toml`, `uv.lock`, `hermes_trading`, and `scripts`.
- `tests/test_control_auth.py`, `tests/test_control_api.py`, `tests/test_control_server.py`, `tests/test_control_client.py`, `tests/test_discord_bot.py`, `tests/test_qualification.py`, `tests/test_qualification_runner.py`, `tests/test_notifications.py`, `tests/test_operational_scripts.py`: regression coverage for this plan.

---

### Task 1: Stabilize The Current Control Foundation

**Files:**
- Modify: `hermes_trading/control_auth.py`
- Modify: `hermes_trading/persistence/migrations.py`
- Modify: `hermes_trading/persistence/repository.py`
- Modify: `tests/test_control_auth.py`
- Modify: `tests/test_persistence.py`
- Modify: `tests/test_artifacts.py`
- Commit includes current schema v4, nonce persistence, and schema expectation updates.

**Interfaces:**
- Consumes: `StateRepository.open(path)`, `StateRepository.migrate()`.
- Produces: `StateRepository.record_control_nonce(nonce: str, *, accepted_at: datetime, expires_at: datetime) -> bool`.
- Produces: `SQLiteNonceReplayStore(repository: StateRepository).add_once(nonce: str, *, accepted_at: datetime, expires_at: datetime) -> bool`.
- Produces: `verify_command(secret, nonce, timestamp, body, signature, used_nonces, *, now=None) -> bool`, where `used_nonces` accepts either `set[str]` or `NonceReplayStore`.

- [ ] **Step 1: Write the failing persistence test**

```python
def test_control_nonce_replay_survives_repository_reopen_and_expires(self) -> None:
    self.repo.migrate()
    accepted_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    expires_at = accepted_at + timedelta(minutes=5)

    self.assertTrue(self.repo.record_control_nonce("nonce-1", accepted_at=accepted_at, expires_at=expires_at))
    self.repo.close()
    self.repo = StateRepository.open(self.db_path)
    self.repo.migrate()

    self.assertFalse(self.repo.record_control_nonce("nonce-1", accepted_at=accepted_at, expires_at=expires_at))
    self.assertTrue(
        self.repo.record_control_nonce(
            "nonce-1",
            accepted_at=expires_at + timedelta(seconds=1),
            expires_at=expires_at + timedelta(minutes=5, seconds=1),
        )
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run python -m unittest tests.test_persistence.PersistenceTests.test_control_nonce_replay_survives_repository_reopen_and_expires
```

Expected: FAIL with `AttributeError: 'StateRepository' object has no attribute 'record_control_nonce'`.

- [ ] **Step 3: Implement schema v4 and repository method**

Add `control_nonces` to migration validation and migration list:

```python
V4_COLUMNS: dict[str, tuple[ColumnShape, ...]] = {
    "control_nonces": (
        ("nonce", "TEXT", 0, 1),
        ("accepted_at", "TEXT", 1, 0),
        ("expires_at", "TEXT", 1, 0),
    ),
}
```

Add repository method:

```python
def record_control_nonce(self, nonce: str, *, accepted_at: datetime, expires_at: datetime) -> bool:
    accepted_timestamp = accepted_at.astimezone(UTC).isoformat()
    expires_timestamp = expires_at.astimezone(UTC).isoformat()
    try:
        self._connection.execute("BEGIN IMMEDIATE")
        self._connection.execute("DELETE FROM control_nonces WHERE expires_at <= ?", (accepted_timestamp,))
        cursor = self._connection.execute(
            "INSERT OR IGNORE INTO control_nonces (nonce, accepted_at, expires_at) VALUES (?, ?, ?)",
            (nonce, accepted_timestamp, expires_timestamp),
        )
        self._connection.commit()
    except BaseException:
        self._connection.rollback()
        raise
    return cursor.rowcount == 1
```

- [ ] **Step 4: Write and verify HMAC replay test**

```python
def test_signed_command_rejects_replay_after_repository_reopen(self):
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "trading.db"
        repository = StateRepository.open(database_path)
        repository.migrate()
        signature = sign_command("secret", "nonce-1", NOW, '{"command":"status"}')

        self.assertTrue(
            verify_command(
                "secret",
                "nonce-1",
                NOW,
                '{"command":"status"}',
                signature,
                SQLiteNonceReplayStore(repository),
                now=NOW,
            )
        )
        repository.close()
        repository = StateRepository.open(database_path)
        repository.migrate()
        try:
            self.assertFalse(
                verify_command(
                    "secret",
                    "nonce-1",
                    NOW,
                    '{"command":"status"}',
                    signature,
                    SQLiteNonceReplayStore(repository),
                    now=NOW,
                )
            )
        finally:
            repository.close()
```

Run:

```powershell
uv run python -m unittest tests.test_control_auth tests.test_persistence
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add hermes_trading/control_auth.py hermes_trading/persistence/migrations.py hermes_trading/persistence/repository.py tests/test_control_auth.py tests/test_persistence.py tests/test_artifacts.py
git commit -m "feat: persist control nonce replay protection"
```

---

### Task 2: Finalize The Private Local Control API And CLI

**Files:**
- Create: `hermes_trading/control_api.py`
- Create: `hermes_trading/control_server.py`
- Create: `hermes_trading/control_client.py`
- Create: `scripts/run_control_api.py`
- Create: `scripts/control_command.py`
- Create: `tests/test_control_api.py`
- Create: `tests/test_control_server.py`
- Create: `tests/test_control_client.py`
- Modify: `tests/test_operational_scripts.py`
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: `verify_command(...) -> bool` and `SQLiteNonceReplayStore`.
- Consumes: `ControlService.request_qualification(phase: QualificationPhase) -> dict[str, object]`.
- Produces: `LocalControlApi.handle(method: str, path: str, headers: Mapping[str, str], body: str) -> ControlResponse`.
- Produces: `make_control_handler(api: LocalControlApi) -> type[BaseHTTPRequestHandler]`.
- Produces: `resolve_control_server_config(env: Mapping[str, str] | None = None) -> ControlServerConfig`.
- Produces: `ControlClient(base_url: str, hmac_secret: str).status() -> dict[str, object]`.
- Produces: `ControlClient(base_url: str, hmac_secret: str).qualification(phase: str = "validation_ready") -> dict[str, object]`.

- [ ] **Step 1: Write failing API tests**

```python
def test_status_requires_hmac_and_rejects_replay(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_dir = Path(directory)
        repository = StateRepository.open(state_dir / "trading.db")
        repository.migrate()
        api = LocalControlApi(repository, state_dir, secret="secret", now=lambda: NOW)
        headers = self.signed_headers("secret", "nonce-1", NOW, "")

        response = api.handle("GET", "/status", headers, "")
        replay = api.handle("GET", "/status", headers, "")
        repository.close()

    self.assertEqual(response.status, 200)
    self.assertEqual(json.loads(response.body)["status"], "ok")
    self.assertEqual(replay.status, 401)
```

Run:

```powershell
uv run python -m unittest tests.test_control_api
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_trading.control_api'`.

- [ ] **Step 2: Implement local API handler**

```python
@dataclass(frozen=True)
class ControlResponse:
    status: int
    body: str
    content_type: str = "application/json"

class LocalControlApi:
    def handle(self, method: str, path: str, headers: Mapping[str, str], body: str) -> ControlResponse:
        if not self._authorized(headers, body):
            return _json_response(401, {"error": "unauthorized"})
        if method == "GET" and path == "/status":
            return _json_response(200, self._status_payload())
        if method == "POST" and path == "/qualification":
            return self._qualification_response(body)
        return _json_response(404, {"error": "not_found"})
```

- [ ] **Step 3: Write failing server config tests**

```python
def test_config_rejects_non_loopback_host(self) -> None:
    with self.assertRaises(ControlServerConfigError):
        resolve_control_server_config(
            {
                "HERMES_CONTROL_HMAC_SECRET": "secret",
                "HERMES_CONTROL_HOST": "0.0.0.0",
            }
        )
```

Run:

```powershell
uv run python -m unittest tests.test_control_server
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_trading.control_server'`.

- [ ] **Step 4: Implement loopback-only server config and builder**

```python
def resolve_control_server_config(env: Mapping[str, str] | None = None) -> ControlServerConfig:
    values = env if env is not None else os.environ
    hmac_secret = values.get("HERMES_CONTROL_HMAC_SECRET", "").strip()
    if not hmac_secret:
        raise ControlServerConfigError("HERMES_CONTROL_HMAC_SECRET is required")
    host = values.get("HERMES_CONTROL_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ControlServerConfigError("HERMES_CONTROL_HOST must be loopback only")
    return ControlServerConfig(Path(values.get("HERMES_STATE_DIR", "state")), host, int(values.get("HERMES_CONTROL_PORT", "8765")), hmac_secret)
```

- [ ] **Step 5: Write failing client tests**

```python
def test_status_signs_get_request_to_local_api(self) -> None:
    captured = {}

    def request_json(method, url, headers, body):
        captured.update({"method": method, "url": url, "headers": headers, "body": body})
        return {"status": "ok", "trading_mode": "training", "real_orders_allowed": False}

    client = ControlClient("http://127.0.0.1:8765", "secret", request_json=request_json, now=lambda: NOW, nonce=lambda: "nonce-1")

    payload = client.status()

    self.assertEqual(payload["status"], "ok")
    self.assertEqual(captured["method"], "GET")
    self.assertEqual(captured["url"], "http://127.0.0.1:8765/status")
    self.assertEqual(captured["body"], "")
    self.assertIn("X-Hermes-Signature", captured["headers"])
```

Run:

```powershell
uv run python -m unittest tests.test_control_client
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_trading.control_client'`.

- [ ] **Step 6: Implement client and scripts**

```python
class ControlClient:
    def status(self) -> dict[str, object]:
        return self._signed_request("GET", "/status", "")

    def qualification(self, phase: str = "validation_ready") -> dict[str, object]:
        body = json.dumps({"phase": phase}, separators=(",", ":"), sort_keys=True)
        return self._signed_request("POST", "/qualification", body)
```

Add script entry points:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Call the private Hermes local control API")
    parser.add_argument("command", choices=("status", "qualification"))
    parser.add_argument("--phase", default="validation_ready")
    parser.add_argument("--base-url", default=os.getenv("HERMES_CONTROL_BASE_URL", "http://127.0.0.1:8765"))
    args = parser.parse_args()
    secret = os.getenv("HERMES_CONTROL_HMAC_SECRET", "").strip()
    if not secret:
        raise SystemExit("HERMES_CONTROL_HMAC_SECRET is required")
    client = ControlClient(args.base_url, secret)
    if args.command == "status":
        print(control_status_message(client.status()))
        return
    print(qualification_message(client.qualification(args.phase)))
```

- [ ] **Step 7: Verify and commit**

Run:

```powershell
uv run python -m unittest tests.test_control_api tests.test_control_server tests.test_control_client tests.test_operational_scripts
```

Expected: PASS.

Commit:

```powershell
git add hermes_trading/control_api.py hermes_trading/control_server.py hermes_trading/control_client.py scripts/run_control_api.py scripts/control_command.py tests/test_control_api.py tests/test_control_server.py tests/test_control_client.py tests/test_operational_scripts.py Dockerfile
git commit -m "feat: add private local control api"
```

---

### Task 3: Add Worker Feature Update And Qualification Summary Notifications

**Files:**
- Modify: `hermes_trading/notifications.py`
- Modify: `hermes_trading/loop.py`
- Create or modify: `tests/test_notifications.py`

**Interfaces:**
- Consumes: `NotificationSettings`, `send_notification(settings, content) -> bool`.
- Consumes: `QualificationReport`.
- Produces: `build_qualification_notification(report: QualificationReport) -> str`.
- Produces: `build_worker_feature_update(feature: str, detail: str, *, status: str = "available") -> str`.
- Produces: `WorkerFeatureUpdateLedger(state_dir: Path).record_once(feature: str, detail: str, *, now: datetime) -> str | None`.

- [ ] **Step 1: Write failing formatter and ledger tests**

Add to `tests/test_notifications.py`:

```python
def test_qualification_notification_summarizes_run_and_blockers(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        (state / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
        repository = StateRepository.open(state / "trading.db")
        repository.migrate()
        report = QualificationRunner(repository, state, QualificationService()).run_from_state(
            QualificationPhase.VALIDATION_READY,
            now=datetime.fromisoformat("2026-08-01T12:00:00+00:00"),
        )
        repository.close()

    message = build_qualification_notification(report)

    self.assertIn("Hermes Trading | Qualification validation_ready", message)
    self.assertIn(report.run_id, message)
    self.assertIn("MISSING | replay_without_lookahead", message)
    self.assertIn("Promotable: false", message)

def test_worker_feature_update_is_sent_once_per_feature(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        ledger = WorkerFeatureUpdateLedger(state)
        first = ledger.record_once(
            "control_api",
            "private local HMAC status and qualification endpoints are available",
            now=datetime.fromisoformat("2026-08-01T12:00:00+00:00"),
        )
        second = ledger.record_once(
            "control_api",
            "private local HMAC status and qualification endpoints are available",
            now=datetime.fromisoformat("2026-08-01T12:01:00+00:00"),
        )

    self.assertEqual(
        first,
        "Hermes Trading | Worker feature available control_api: private local HMAC status and qualification endpoints are available",
    )
    self.assertIsNone(second)
```

Run:

```powershell
uv run python -m unittest tests.test_notifications.CycleNotificationTests.test_worker_feature_update_is_sent_once_per_feature
```

Expected: FAIL with `NameError` or import error for `build_qualification_notification` and `WorkerFeatureUpdateLedger`.

- [ ] **Step 2: Implement qualification formatter, worker update formatter, and ledger**

```python
def build_qualification_notification(report: QualificationReport) -> str:
    promotable = "true" if report.snapshot.promotable else "false"
    return "\n".join(
        [
            report.markdown,
            f"Run: {report.run_id}",
            f"Promotable: {promotable}",
        ]
    )

def build_worker_feature_update(feature: str, detail: str, *, status: str = "available") -> str:
    return f"Hermes Trading | Worker feature {status} {feature}: {detail}"

class WorkerFeatureUpdateLedger:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "worker_feature_updates.json"

    def record_once(self, feature: str, detail: str, *, now: datetime) -> str | None:
        markers = _load_summary_markers(self._path)
        if markers.get(feature):
            return None
        markers[feature] = now.isoformat()
        self._path.write_text(json.dumps(markers, indent=2, sort_keys=True), encoding="utf-8")
        return build_worker_feature_update(feature, detail)
```

- [ ] **Step 3: Write failing loop integration test**

```python
async def test_run_loop_notifies_scheduled_qualification_report(self):
    messages = []

    async def fake_send(settings, content):
        messages.append(content)
        return True

    async def fake_run_cycle(*args, **kwargs):
        return CycleResult(results=(), mode="training")

    async def stop_after_cycle(_):
        raise asyncio.CancelledError()

    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        ensure_state_files(state)
        with patch.dict(os.environ, {
            "HERMES_TRADING_MODE": "training",
            "HERMES_NOTIFY_ENABLED": "true",
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token",
            "HERMES_QUALIFICATION_EVERY_CYCLES": "1",
        }, clear=False):
            with patch("hermes_trading.loop.run_cycle", fake_run_cycle):
                with patch("hermes_trading.loop._update_profitability_best_effort", return_value=None):
                    with patch("hermes_trading.loop.send_notification", fake_send):
                        with patch("hermes_trading.loop.asyncio.sleep", stop_after_cycle):
                            with self.assertRaises(asyncio.CancelledError):
                                await run_loop("BTC/USDT", state, interval_seconds=60)

    self.assertTrue(any("Qualification validation_ready" in message for message in messages))
    self.assertTrue(any("Promotable: false" in message for message in messages))

async def test_run_loop_notifies_control_api_feature_once_when_enabled(self):
    messages = []

    async def fake_send(settings, content):
        messages.append(content)
        return True

    async def fake_run_cycle(*args, **kwargs):
        return CycleResult(results=(), mode="training")

    async def stop_after_cycle(_):
        raise asyncio.CancelledError()

    with tempfile.TemporaryDirectory() as temp_dir:
        state = Path(temp_dir)
        ensure_state_files(state)
        with patch.dict(os.environ, {
            "HERMES_TRADING_MODE": "training",
            "HERMES_NOTIFY_ENABLED": "true",
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/token",
            "HERMES_WORKER_FEATURE_UPDATES": "control_api,qualification_evidence,discord_bot",
        }, clear=False):
            with patch("hermes_trading.loop.run_cycle", fake_run_cycle):
                with patch("hermes_trading.loop._update_profitability_best_effort", return_value=None):
                    with patch("hermes_trading.loop.send_notification", fake_send):
                        with patch("hermes_trading.loop.asyncio.sleep", stop_after_cycle):
                            with self.assertRaises(asyncio.CancelledError):
                                await run_loop("BTC/USDT", state, interval_seconds=60)

    self.assertTrue(any("Worker feature available control_api" in message for message in messages))
```

Run:

```powershell
uv run python -m unittest tests.test_notifications.LoopNotificationTests.test_run_loop_notifies_control_api_feature_once_when_enabled
```

Expected: FAIL because the loop does not yet send qualification report messages or call the feature update ledger.

- [ ] **Step 4: Integrate qualification reports and worker updates into training loop**

Add helper in `loop.py`:

```python
def _worker_feature_update_messages(state_dir: Path, now: datetime) -> list[str]:
    enabled = {
        item.strip()
        for item in os.getenv("HERMES_WORKER_FEATURE_UPDATES", "").split(",")
        if item.strip()
    }
    details = {
        "control_api": "private local HMAC status and qualification endpoints are available",
        "qualification_evidence": "typed qualification evidence reports missing proofs explicitly",
        "discord_webhook_qualification": "qualification summaries can be sent through the existing Discord webhook",
        "discord_bot": "separate Discord bot service is packaged and ready for private secrets",
    }
    ledger = WorkerFeatureUpdateLedger(state_dir)
    return [
        message
        for feature, detail in details.items()
        if feature in enabled
        for message in [ledger.record_once(feature, detail, now=now)]
        if message is not None
    ]
```

Capture the scheduled qualification report and append both notification types before `_notify_all(messages, notification_settings)` in `_run_training_loop`:

```python
qualification_report = None
if qualification_schedule.record_cycle():
    qualification_report = QualificationRunner(state, state_dir, QualificationService()).run_from_state(
        QualificationPhase.VALIDATION_READY, now=started_at
    )

messages = build_cycle_notifications(trades, orders, notification_settings)
messages.extend(_worker_feature_update_messages(state_dir, started_at))
if qualification_report is not None:
    messages.append(build_qualification_notification(qualification_report))
```

- [ ] **Step 5: Verify and commit**

Run:

```powershell
uv run python -m unittest tests.test_notifications
```

Expected: PASS.

Commit:

```powershell
git add hermes_trading/notifications.py hermes_trading/loop.py tests/test_notifications.py
git commit -m "feat: notify worker feature availability"
```

---

### Task 4: Connect Real Typed Qualification Evidence Collectors

**Files:**
- Modify: `hermes_trading/qualification_evidence.py`
- Modify: `hermes_trading/qualification_runner.py`
- Modify: `hermes_trading/persistence/repository.py`
- Modify: `tests/test_qualification.py`
- Modify: `tests/test_qualification_runner.py`
- Create or modify: `tests/test_qualification_evidence.py`

**Interfaces:**
- Consumes: existing JSON state files and canonical SQLite tables.
- Produces: `QualificationEvidenceCollector.collect() -> dict[str, EvidenceCheck]`.
- Produces: `StateRepository.risk_limit_evidence_count() -> int`.
- Produces: `StateRepository.duplicate_candle_evidence_count() -> int`.

- [ ] **Step 1: Write failing evidence collector tests**

Create `tests/test_qualification_evidence.py`:

```python
class QualificationEvidenceCollectorTests(unittest.TestCase):
    def test_missing_evidence_is_reported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checks = QualificationEvidenceCollector(Path(temp_dir)).collect()

        self.assertEqual(checks["replay_without_lookahead"].status.value, "missing")
        self.assertEqual(checks["fees_and_slippage_charged"].status.value, "missing")
        self.assertEqual(checks["duplicate_candle_protected"].status.value, "missing")
        self.assertEqual(checks["risk_limits_exercised"].status.value, "missing")

    def test_file_evidence_can_mark_a_gate_passed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "qualification-evidence.json").write_text(
                json.dumps({
                    "replay_without_lookahead": {
                        "status": "pass",
                        "observed": "replay-2026-08-01",
                        "required": "no lookahead detected",
                        "reason": "offline replay completed without future candles",
                    }
                }),
                encoding="utf-8",
            )
            checks = QualificationEvidenceCollector(state).collect()

        self.assertEqual(checks["replay_without_lookahead"].status.value, "pass")
        self.assertEqual(checks["replay_without_lookahead"].observed, "replay-2026-08-01")
```

Run:

```powershell
uv run python -m unittest tests.test_qualification_evidence
```

Expected: FAIL if `tests/test_qualification_evidence.py` or collector behavior is absent.

- [ ] **Step 2: Implement typed JSON evidence parsing**

```python
def _parse_check(value: Any, missing_reason: str) -> EvidenceCheck:
    if value is True:
        return EvidenceCheck.passed()
    if value is False:
        return EvidenceCheck.failed("false")
    if not isinstance(value, dict):
        return EvidenceCheck.missing(missing_reason)
    try:
        status = GateStatus(str(value["status"]))
    except (KeyError, ValueError):
        return EvidenceCheck.missing(missing_reason)
    observed = str(value.get("observed", status.value))
    required = str(value.get("required", "true"))
    reason = str(value.get("reason", "satisfied" if status == GateStatus.PASS else missing_reason))
    return EvidenceCheck(status, observed, required, reason)
```

- [ ] **Step 3: Add repository-backed duplicate candle and risk evidence tests**

Add to `tests/test_persistence.py`:

```python
def test_duplicate_candle_and_risk_evidence_counts_are_visible(self) -> None:
    self.repo.migrate()
    self.repo.open_incident("qualification:duplicate-candle", "duplicate_candle_protected", severity="info")
    self.repo.open_incident("qualification:risk-limit", "risk_limit_exercised", severity="info")

    self.assertEqual(self.repo.duplicate_candle_evidence_count(), 1)
    self.assertEqual(self.repo.risk_limit_evidence_count(), 1)
```

Run:

```powershell
uv run python -m unittest tests.test_persistence.PersistenceTests.test_duplicate_candle_and_risk_evidence_counts_are_visible
```

Expected: FAIL with missing repository methods.

- [ ] **Step 4: Implement repository evidence counts**

```python
def duplicate_candle_evidence_count(self) -> int:
    row = self._connection.execute(
        "SELECT COUNT(*) FROM incidents WHERE incident_id LIKE 'qualification:duplicate-candle%'"
    ).fetchone()
    return int(row[0])

def risk_limit_evidence_count(self) -> int:
    row = self._connection.execute(
        "SELECT COUNT(*) FROM incidents WHERE incident_id LIKE 'qualification:risk-limit%'"
    ).fetchone()
    return int(row[0])
```

- [ ] **Step 5: Let runner merge JSON and repository evidence**

In `qualification_runner.py`, construct the collector with repository:

```python
typed_evidence = QualificationEvidenceCollector(self.projector.state_dir, repository=self.repository).collect()
```

In `qualification_evidence.py`, repository observations override missing JSON checks only when durable evidence exists:

```python
if self._repository and checks["duplicate_candle_protected"].status == GateStatus.MISSING:
    count = self._repository.duplicate_candle_evidence_count()
    if count > 0:
        checks["duplicate_candle_protected"] = EvidenceCheck.passed(str(count), ">= 1", "duplicate candle protection exercised")
```

Repeat the same shape for `risk_limits_exercised`.

- [ ] **Step 6: Verify and commit**

Run:

```powershell
uv run python -m unittest tests.test_qualification tests.test_qualification_runner tests.test_qualification_evidence tests.test_persistence
```

Expected: PASS.

Commit:

```powershell
git add hermes_trading/qualification.py hermes_trading/qualification_evidence.py hermes_trading/qualification_runner.py hermes_trading/persistence/repository.py tests/test_qualification.py tests/test_qualification_runner.py tests/test_qualification_evidence.py tests/test_persistence.py
git commit -m "feat: collect typed qualification evidence"
```

---

### Task 5: Finalize Discord Bot Service And Secrets Readiness

**Files:**
- Modify: `hermes_trading/discord_bot.py`
- Modify: `scripts/run_discord_bot.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`
- Modify: `tests/test_discord_bot.py`
- Modify: `tests/test_operational_scripts.py`

**Interfaces:**
- Consumes: `ControlClient` and `HERMES_CONTROL_HMAC_SECRET`.
- Produces: `resolve_discord_bot_config(env: Mapping[str, str] | None = None) -> DiscordBotConfig`.
- Produces: `DiscordControlBot.status(user_id: int, otp: str) -> str`.
- Produces: `DiscordControlBot.qualification(user_id: int, otp: str) -> str`.
- Produces: `run_discord_bot(config: DiscordBotConfig) -> None`.

- [ ] **Step 1: Write failing Discord config tests**

```python
def test_config_requires_token_allowed_users_hmac_secret_and_totp_secret(self) -> None:
    with self.assertRaises(DiscordBotConfigError):
        resolve_discord_bot_config({})

def test_config_accepts_only_loopback_control_url(self) -> None:
    config = resolve_discord_bot_config({
        "HERMES_DISCORD_BOT_TOKEN": "bot-token",
        "HERMES_DISCORD_ALLOWED_USER_IDS": "123, 456",
        "HERMES_CONTROL_HMAC_SECRET": "control-secret",
        "HERMES_DISCORD_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "HERMES_CONTROL_BASE_URL": "http://127.0.0.1:8765",
    })
    self.assertEqual(config.allowed_user_ids, frozenset({123, 456}))
```

Run:

```powershell
uv run python -m unittest tests.test_discord_bot
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_trading.discord_bot'`.

- [ ] **Step 2: Implement TOTP and bot command core**

```python
def totp_code(secret: str, at: datetime | None = None, *, step_seconds: int = 30, digits: int = 6) -> str:
    current = (at or datetime.now(UTC)).astimezone(UTC)
    counter = int(current.timestamp()) // step_seconds
    key = base64.b32decode(_base32_padded(secret), casefold=True)
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)
```

```python
class DiscordControlBot:
    def status(self, *, user_id: int, otp: str) -> str:
        denial = self._deny(user_id, otp)
        if denial is not None:
            return denial
        payload = self._signed_request("GET", "/status", "")
        return f"Hermes status={payload.get('status')} mode={payload.get('trading_mode')} real_orders_allowed={_bool_text(payload.get('real_orders_allowed'))}"
```

- [ ] **Step 3: Add `discord.py` dependency and adapter**

Run:

```powershell
uv add "discord.py>=2.5.2"
```

Add adapter:

```python
async def run_discord_bot(config: DiscordBotConfig) -> None:
    import discord
    from discord import app_commands

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    bot = DiscordControlBot(config)

    @tree.command(name="status", description="Read Hermes status")
    async def status(interaction: discord.Interaction, otp: str) -> None:
        await interaction.response.send_message(
            bot.status(user_id=interaction.user.id, otp=otp),
            ephemeral=True,
        )

    @tree.command(name="qualification", description="Request a validation qualification report")
    async def qualification(interaction: discord.Interaction, otp: str) -> None:
        await interaction.response.send_message(
            bot.qualification(user_id=interaction.user.id, otp=otp),
            ephemeral=True,
        )

    await client.start(config.token)
```

- [ ] **Step 4: Document required secrets without inventing values**

In `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`, include:

```markdown
Required environment variables:

- `HERMES_DISCORD_BOT_TOKEN`
- `HERMES_DISCORD_ALLOWED_USER_IDS`
- `HERMES_CONTROL_HMAC_SECRET`
- `HERMES_DISCORD_TOTP_SECRET`
- `HERMES_CONTROL_BASE_URL`, optional, defaults to `http://127.0.0.1:8765`
```

- [ ] **Step 5: Verify and commit**

Run:

```powershell
uv run python -m unittest tests.test_discord_bot tests.test_operational_scripts
```

Expected: PASS.

Commit:

```powershell
git add hermes_trading/discord_bot.py scripts/run_discord_bot.py pyproject.toml uv.lock docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md tests/test_discord_bot.py tests/test_operational_scripts.py
git commit -m "feat: prepare private discord control bot"
```

---

### Task 6: Package Separate Railway Services Without Changing Worker Safety

**Files:**
- Modify: `Dockerfile`
- Create: `docs/RAILWAY_CONTROL_DISCORD_DEPLOYMENT_2026-08-01.md`
- Create or modify: `tests/test_operational_scripts.py`

**Interfaces:**
- Consumes: `scripts/run_control_api.py`.
- Consumes: `scripts/run_discord_bot.py`.
- Produces: documented Railway service commands:
  - Worker: `uv run python -m hermes_trading.run`
  - Control API: `uv run python scripts/run_control_api.py`
  - Discord bot: `uv run python scripts/run_discord_bot.py`

- [ ] **Step 1: Write failing documentation smoke test**

Add to `tests/test_operational_scripts.py`:

```python
def test_deployment_doc_names_separate_service_commands(self) -> None:
    content = (ROOT / "docs" / "RAILWAY_CONTROL_DISCORD_DEPLOYMENT_2026-08-01.md").read_text(encoding="utf-8")

    self.assertIn("uv run python -m hermes_trading.run", content)
    self.assertIn("uv run python scripts/run_control_api.py", content)
    self.assertIn("uv run python scripts/run_discord_bot.py", content)
    self.assertIn("HERMES_TRADING_ALLOW_REAL_ORDERS=false", content)
```

Run:

```powershell
uv run python -m unittest tests.test_operational_scripts.OperationalScriptTests.test_deployment_doc_names_separate_service_commands
```

Expected: FAIL with `FileNotFoundError` for the deployment doc.

- [ ] **Step 2: Ensure Docker copies lockfile and scripts**

Dockerfile must contain:

```dockerfile
COPY pyproject.toml uv.lock ./
COPY hermes_trading ./hermes_trading
COPY scripts ./scripts
RUN uv sync
ENV HERMES_TRADING_MODE=training
CMD ["uv", "run", "python", "-m", "hermes_trading.run"]
```

- [ ] **Step 3: Add deployment doc**

Create `docs/RAILWAY_CONTROL_DISCORD_DEPLOYMENT_2026-08-01.md` with:

```markdown
# Railway Control And Discord Deployment

Worker service command:

`uv run python -m hermes_trading.run`

Control API service command:

`uv run python scripts/run_control_api.py`

Discord bot service command:

`uv run python scripts/run_discord_bot.py`

Safety variables for the worker:

- `HERMES_TRADING_MODE=training`
- `HERMES_TRADING_ALLOW_REAL_ORDERS=false`
```

- [ ] **Step 4: Verify and commit**

Run:

```powershell
uv run python -m unittest tests.test_operational_scripts
```

Expected: PASS.

Commit:

```powershell
git add Dockerfile docs/RAILWAY_CONTROL_DISCORD_DEPLOYMENT_2026-08-01.md tests/test_operational_scripts.py
git commit -m "docs: document separate control and discord services"
```

---

### Task 7: Deploy Safely And Verify Railway

**Files:**
- Modify: `docs/SESSION_HANDOFF_2026-08-01.md`
- Modify: `docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md`

**Interfaces:**
- Consumes: Railway CLI.
- Produces: verified deployment notes with exact command output summarized.
- Produces: worker update messages after deploy verification.

- [ ] **Step 1: Run local full verification before deploy**

Run:

```powershell
uv run python -m unittest discover -s tests
graphify update .
git diff --check
```

Expected:

- Tests: PASS with no failures.
- Graphify: code graph updated.
- Diff check: no whitespace errors; LF/CRLF warnings on Windows are acceptable.

- [ ] **Step 2: Check Railway status before deploy**

Run:

```powershell
railway.cmd status
```

Expected: project `awake-abundance`, service `hermes-trading`, production environment.

- [ ] **Step 3: Deploy without changing real-order safety variables**

Use the repository’s existing Railway deployment path. If using Railway CLI deployment, run the project’s normal deploy command. Do not set `HERMES_TRADING_MODE=live`. Do not set `HERMES_TRADING_ALLOW_REAL_ORDERS=true`.

- [ ] **Step 4: Verify safety variables over SSH**

Run:

```powershell
railway.cmd ssh "printenv HERMES_TRADING_MODE"
railway.cmd ssh "printenv HERMES_TRADING_ALLOW_REAL_ORDERS"
```

Expected:

```text
training
false
```

If `railway.cmd ssh` reports a temporary key-verification outage, retry once or twice. Treat any actual value other than `training` and `false` as a blocker.

- [ ] **Step 5: Smoke test control API service before Discord bot service**

On the control API service shell:

```powershell
uv run python scripts/control_command.py status
uv run python scripts/control_command.py qualification
```

Expected:

```text
status=ok mode=training real_orders_allowed=false
qualification phase=validation_ready promotable=false promoted=false run_id=qualification-...
```

- [ ] **Step 6: Send worker update after each verified capability**

With notifications enabled, confirm the worker sends feature updates for:

```text
control_api
qualification_evidence
discord_webhook_qualification
discord_bot
railway_safety_verified
```

Expected message shape:

```text
Hermes Trading | Worker feature available control_api: private local HMAC status and qualification endpoints are available
```

- [ ] **Step 7: Update handoff docs**

Add a dated section to `docs/SESSION_HANDOFF_2026-08-01.md`:

```markdown
## Continuation 2026-08-01

- Durable nonce replay is stored in SQLite schema v4.
- Private local control API is available for status and non-promoting qualification.
- Typed qualification evidence remains visible as missing unless evidence is present.
- Discord webhook qualification summaries are available.
- Separate Discord bot service is prepared and requires private secrets.
- Railway safety verification after deploy: `HERMES_TRADING_MODE=training`, `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
```

- [ ] **Step 8: Commit deployment documentation**

```powershell
git add docs/SESSION_HANDOFF_2026-08-01.md docs/CONTROL_AND_DISCORD_OPERATIONS_2026-08-01.md
git commit -m "docs: record safe control deployment verification"
```

---

## Final Verification Checklist

- [ ] `uv run python -m unittest discover -s tests` passes.
- [ ] `graphify update .` completes.
- [ ] `git diff --check` reports no whitespace errors.
- [ ] `railway.cmd status` points to project `awake-abundance`, service `hermes-trading`, production environment.
- [ ] `railway.cmd ssh "printenv HERMES_TRADING_MODE"` returns `training`.
- [ ] `railway.cmd ssh "printenv HERMES_TRADING_ALLOW_REAL_ORDERS"` returns `false`.
- [ ] `scripts/control_command.py status` returns `real_orders_allowed=false`.
- [ ] `scripts/control_command.py qualification` returns `promoted=false`.
- [ ] Discord webhook sends qualification summaries.
- [ ] Discord bot commands are ephemeral and reject unauthorized users or invalid TOTP before calling the control API.
- [ ] Worker feature updates are sent once per feature as capabilities become available.

## Self-Review

Spec coverage:

- Nonce SQLite persistence is covered by Task 1.
- Private local HMAC API is covered by Task 2.
- Typed evidence collectors with missing visibility are covered by Task 4.
- Qualification Discord webhook summaries are covered by Task 3 and existing notification wiring.
- Separate Discord bot service with bot token, allowed user IDs, private HMAC secret, and TOTP secret is covered by Task 5.
- Deployment safety and SSH verification are covered by Task 7.
- Regular worker updates when useful features become available are covered by Task 3 and Task 7.

Placeholder scan:

- The plan avoids incomplete markers and unspecified implementation steps.
- Every task has concrete files, interfaces, test commands, implementation snippets, verification commands, and commit commands.

Type consistency:

- `ControlClient`, `LocalControlApi`, `ControlServerConfig`, `DiscordControlBot`, `QualificationEvidenceCollector`, `EvidenceCheck`, and `WorkerFeatureUpdateLedger` are named consistently across producer and consumer tasks.
