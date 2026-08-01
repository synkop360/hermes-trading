# Hermes Trading Session Handoff

## Repository

- Path: `E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading`
- Branch: `feat/live-hardening`
- Railway project: `awake-abundance`, service `hermes-trading`, production environment.
- Railway volume: `/app/state`.

## Non-negotiable safety state

- Railway must remain `HERMES_TRADING_MODE=training`.
- Railway must remain `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- Never enable real Kraken orders or promote a phase automatically.
- After every Railway deploy, verify both variables over SSH.

## Current Railway configuration

- `HERMES_RAILWAY_MONTHLY_COST_USD=0.04`
- `HERMES_RAILWAY_SUBSCRIPTION_ALLOCATION_PCT=100`
- `HERMES_QUALIFICATION_EVERY_CYCLES=30`
- Strategy is version `83` and assets are BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT.

## Implemented recently

1. Profitability snapshots can be sent to Discord on an independent cadence using `HERMES_NOTIFY_PROFITABILITY_FREQUENCIES`.
2. Profitability includes the configured Railway monthly cost. Missing or unconvertible costs block the economic gate rather than being treated as zero.
3. `heartbeat.json` now contains `economic_gate` with status, reasons, net PnL, projected 30d PnL, and stress PnL.
4. Qualification model exists in `hermes_trading/qualification.py`:
   - training/validation/live phases;
   - gates for elapsed training, closed positions, strategy consistency, replay, fees/slippage, candle protection, risk limits, and costs for live phases.
5. Qualification snapshots and individual gates are append-only in SQLite through `StateRepository.record_qualification_snapshot()`.
6. Qualification runner writes `qualification-latest.json` and `qualification-latest.md`.
7. Runner can execute against real state with `scripts/run_qualification.py`.
8. Training start is persisted in `state/qualification-training-started-at.txt` unless `HERMES_TRAINING_STARTED_AT` is explicitly configured.
9. Training loop launches a qualification campaign every configured number of cycles. It does not promote a phase.
10. `ControlService` can request a qualification report but always returns `promoted=false`.
11. `control_auth.py` provides HMAC signatures, a five-minute window, and in-memory nonce replay rejection.
12. Private local control and Discord command plumbing is implemented for read-only status and non-promoting validation qualification requests.
13. Qualification evidence collectors now read typed evidence from state, with missing evidence remaining visible as `missing`.
14. Qualification Discord summary notifications are implemented through the existing webhook path.

## Safe pre-deploy verification, 2026-08-01

- `uv run python -m unittest discover -s tests` passed: 331 tests.
- `graphify update .` completed and refreshed `graphify-out`; it reported only graph metadata warnings for empty state JSON files and changed community labels.
- `git diff --check` passed.
- `railway.cmd status` reported project `awake-abundance`, production service `hermes-trading` online, volume `/app/state`, deployment `8f31e2f6-b3ad-4c9e-ace5-898716c63a6c`.
- Read-only Railway SSH checks returned `HERMES_TRADING_MODE=training` and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- No Railway deploy was performed, no Railway variables were changed, and real orders were not enabled.
- Deployment remains pending explicit confirmation and private secrets review.

## Post-deploy verification, 2026-08-01

- User gave explicit `Go deploy`.
- Added `.dockerignore` so Railway Docker builds include only `Dockerfile`, `pyproject.toml`, `uv.lock`, `hermes_trading/`, and `scripts/`.
- Ran `railway.cmd up --service hermes-trading --environment production --message "Deploy Hermes control and Discord hardening"`.
- Railway deployment `68f878ea-a973-4be8-b471-b760e6a4beac` completed with status `SUCCESS`.
- `railway.cmd status` reported project `awake-abundance`, production service `hermes-trading` online, volume `/app/state`, deployment `68f878ea-a973-4be8-b471-b760e6a4beac`.
- Deployment logs show `Booting hermes-trading worker for BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT in training mode`.
- Post-deploy SSH checks returned `HERMES_TRADING_MODE=training` and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- A temporary SSH key-verification outage occurred immediately after deploy; retry succeeded.
- Railway currently lists only the `hermes-trading` worker service. Separate Control API and Discord bot services are packaged and documented but still need Railway service provisioning and private secrets before service-level smoke tests.
- No Railway variables were changed, real orders were not enabled, and no phase was promoted.

## Current real qualification result

Manual Railway command used:

```powershell
railway.cmd ssh "cd /app && uv run python scripts/run_qualification.py --state-dir /app/state --phase validation_ready"
```

Current report is correctly blocked:

- training duration observed 0 days (before persistent start is deployed and exercised);
- 4 closed positions, required 50;
- strategy hash passes;
- replay, charged fees/slippage, duplicate candle protection, and risk-limit evidence are not yet connected, so they fail explicitly.

This is correct behavior. Nothing has been promoted.

## Recent commits

- `ec3feea feat: expose economic gate in heartbeat`
- `c6f2046 feat: add qualification gate model`
- `a01de8d feat: persist qualification snapshots`
- `c9cf426 feat: publish qualification reports`
- `d2ba948 feat: run qualification from worker state`
- `29ad077 fix: support direct qualification script execution`
- `6ed9d1f fix: include operational scripts in image`
- `f185b8e feat: persist qualification training start`
- `90167a1 feat: schedule qualification campaigns`
- `3d53cc6 feat: add non-promoting qualification control`
- `3027691 feat: authenticate control commands with hmac`

## Verification commands

```powershell
uv run python -m unittest discover -s tests
graphify update .
railway.cmd status
railway.cmd ssh "printenv HERMES_TRADING_MODE"
railway.cmd ssh "printenv HERMES_TRADING_ALLOW_REAL_ORDERS"
```

The last full test run before the latest small control-auth addition passed 292 tests. Run the full suite again before the next deploy.

## Immediate next work

1. Finish `control_auth.py` persistence: move nonce replay storage from in-memory to SQLite, with expiry and pruning.
2. Implement a private local control API using HMAC auth. Start with read-only status and qualification requests only.
3. Add typed evidence collectors for replay, fee/slippage, duplicate-candle protection, and risk-limit exercises. Missing evidence must remain visible as `MISSING`/`FAIL`.
4. Add qualification Discord summary notifications, still using the existing webhook first.
5. Only then build the separate Discord bot service. It will require a Discord bot token, allowed Discord user IDs, a private HMAC secret, and a TOTP secret from the user. Do not ask for these until the local/private API is ready.

## Operational cautions

- `railway.cmd ssh` occasionally reports a temporary key-verification outage. Retry once or twice; it is not a key rejection.
- The Docker image needs `COPY scripts ./scripts`; this is already committed in `6ed9d1f`.
- Remote scripts must use `uv run python`, not bare `python`, because dependencies are in the uv environment.
- The worktree contains many untracked `.test-tmp-*` folders and `graphify-out/`. Do not delete or revert them unless explicitly requested.
- Use `apply_patch` for edits and TDD for code changes. Use `graphify query` before codebase questions and `graphify update .` after code changes.
