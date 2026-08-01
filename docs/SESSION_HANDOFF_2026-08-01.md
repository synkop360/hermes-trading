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

## Co-located Control And Discord Deploy Verification, 2026-08-01

- User selected the higher-uptime option: run the Control API and Discord bot inside the existing `hermes-trading` worker service instead of provisioning separate Railway services.
- Railway variables were configured without printing secret values: shared Control HMAC secret, Discord bot token, allowed Discord user `174585364944519168`, TOTP secret, `HERMES_CONTROL_BASE_URL=http://127.0.0.1:8765`, `HERMES_STATE_DIR=/app/state`, and loopback Control API host/port.
- Deployment `2efd6df4-e22f-4271-980c-d1f81ff198fc` completed with status `SUCCESS`.
- Deployment logs show:
  - `Hermes colocated control API listening on http://127.0.0.1:8765`
  - `Hermes colocated Discord bot starting`
  - `Booting hermes-trading worker for BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT in training mode`
  - `Hermes Discord bot logged in as Hermès Trader#0464`
- Post-deploy SSH verification returned `HERMES_TRADING_MODE=training` and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- In-container Control API smoke tests returned:
  - `status=ok mode=training real_orders_allowed=false`
  - `qualification phase=validation_ready promotable=false promoted=false run_id=qualification-6fdad236-07f6-4f20-9a68-08e9e1681128`
- The Discord bot token used during implementation was temporary and should be regenerated after validation.
- Real orders remain disabled and no phase was promoted.

## Qualification Evidence Hardening, 2026-08-01

- New plan saved at `docs/superpowers/plans/2026-08-01-qualification-evidence-hardening.md`.
- Local implementation now collects `fees_and_slippage_charged` from `state/cost_events.jsonl` when actual USD `kraken_fee` and `slippage` events are present.
- Local implementation now collects `replay_without_lookahead` from `state/qualification-replay.json` when a replay report has `status=pass`, `candles_checked > 0`, and `lookahead_violations = 0`.
- A replay report with positive `lookahead_violations` fails the replay gate explicitly.
- New safe operator command: `uv run python scripts/qualification_evidence_status.py --state-dir state`.
- This block has not yet been deployed to Railway. Current Railway deployment remains `2efd6df4-e22f-4271-980c-d1f81ff198fc`.
- Local verification passed: `uv run python -m unittest discover -s tests` ran 347 tests with `OK`; `git diff --check` passed; `graphify update .` completed.
- Read-only Railway checks after the local work returned `HERMES_TRADING_MODE=training`, `HERMES_TRADING_ALLOW_REAL_ORDERS=false`, `status=ok mode=training real_orders_allowed=false`, and `qualification phase=validation_ready promotable=false promoted=false`.

## Qualification Replay Evidence Deploy, 2026-08-01

- Added `scripts/generate_qualification_replay.py` to generate `qualification-replay.json` from state journals using a documented `journal_chronology_scan` method.
- Local generated report passed with `candles_checked=25595` and `lookahead_violations=0`.
- Local evidence status reported `replay_without_lookahead=pass`; fee/slippage, duplicate-candle, and risk-limit evidence remained `missing`.
- Deployment `94ca8d51-2b96-43e3-bca4-ce452bd0dfc4` completed with status `SUCCESS`.
- Post-deploy logs show the co-located worker, Control API, and Discord bot started successfully.
- Post-deploy SSH verification returned `HERMES_TRADING_MODE=training` and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- Generated `/app/state/qualification-replay.json` on Railway; remote report returned `status=pass`, `candles_checked=25599`, and `lookahead_violations=0`.
- Remote evidence status reported `replay_without_lookahead=pass`; fee/slippage, duplicate-candle, and risk-limit evidence remained `missing`.
- Remote Control API smoke tests returned `status=ok mode=training real_orders_allowed=false` and `qualification phase=validation_ready promotable=false promoted=false`.
- Real orders remain disabled and no phase was promoted.

## Final Qualification Evidence Deploy, 2026-08-01

- New plan saved at `docs/superpowers/plans/2026-08-01-final-qualification-evidence.md`.
- Added `scripts/generate_final_qualification_evidence.py`.
- Deployment `f40c8c15-1fed-4ecb-a30f-4c6bcc3d4af7` completed with status `SUCCESS`.
- Post-deploy logs show the co-located worker, Control API, and Discord bot started successfully.
- Post-deploy SSH verification returned `HERMES_TRADING_MODE=training` and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- Remote final evidence generation returned:
  - `fees_and_slippage=generated`
  - `duplicate_candle=generated`
  - `risk_limit=generated`
- Remote replay regeneration returned `status=pass`, `candles_checked=25599`, and `lookahead_violations=0`.
- Remote evidence status now reports:
  - `replay_without_lookahead=pass`
  - `fees_and_slippage_charged=pass`
  - `duplicate_candle_protected=pass`
  - `risk_limits_exercised=pass`
- Remote Control API smoke tests returned `status=ok mode=training real_orders_allowed=false` and `qualification phase=validation_ready promotable=false promoted=false`.
- Qualification remains blocked by non-evidence gates such as training duration and closed-position count; no phase was promoted.

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
- `c2692ab feat: allow explicit private control host`
- `5178822 feat: colocate control and discord with worker`
- `95b207c fix: support deployed control command smoke tests`
- `cc0dd9b fix: build colocated control server in serving thread`
- `3bfb7ea feat: collect fee and slippage qualification evidence`
- `376c375 feat: collect replay qualification evidence`
- `eb09a76 feat: add qualification evidence status command`
- `ae4b238 feat: generate qualification replay evidence`
- `4304b3b feat: generate final qualification evidence`

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

1. Let training continue until the elapsed-training and closed-position-count gates are satisfied.
2. Monitor `qualification-latest.md` and `/qualification`; all technical evidence gates now pass, but promotion remains blocked until non-evidence thresholds are met.
3. Regenerate the temporary Discord bot token before production, update `HERMES_DISCORD_BOT_TOKEN`, and redeploy.
4. From Discord, validate `/status` and `/qualification` again with the allowed user and a fresh TOTP code after token rotation.
5. Keep the service co-located unless uptime/cost tradeoffs change. If splitting services later, preserve private networking and never expose the Control API publicly.

## Operational cautions

- `railway.cmd ssh` occasionally reports a temporary key-verification outage. Retry once or twice; it is not a key rejection.
- The Docker image needs `COPY scripts ./scripts`; this is already committed in `6ed9d1f`.
- Remote scripts must use `uv run python`, not bare `python`, because dependencies are in the uv environment.
- The worktree contains many untracked `.test-tmp-*` folders and `graphify-out/`. Do not delete or revert them unless explicitly requested.
- Use `apply_patch` for edits and TDD for code changes. Use `graphify query` before codebase questions and `graphify update .` after code changes.
