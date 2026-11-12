# Hermes Control And Discord Operations

## Plan Being Followed

This work follows `docs/SESSION_HANDOFF_2026-08-01.md`, section `Immediate next work`.

Current status:

1. SQLite nonce replay persistence: implemented in schema v4.
2. Private local control API: implemented for read-only status and non-promoting qualification requests.
3. Typed qualification evidence collectors: implemented with missing evidence remaining visible as `missing`.
4. Qualification Discord summary notifications: implemented through the existing webhook path.
5. Co-located Discord bot service: deployed inside the worker service with the loopback Control API.

## Safety Constraints

- Keep `HERMES_TRADING_MODE=training` on the current Railway worker unless an explicit later promotion plan says otherwise.
- Keep `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- The control API only accepts HMAC signed requests and rejects replayed nonces.
- The control server and bot client reject non-loopback control URLs unless a private Railway host is explicitly allowlisted.
- Discord commands currently expose only status and validation qualification requests.
- Qualification requests return `promoted=false`; they never promote a phase.

## Local Control API

Start the private local API:

```powershell
$env:HERMES_CONTROL_HMAC_SECRET = "<private-random-secret>"
$env:HERMES_STATE_DIR = "state"
uv run python scripts/run_control_api.py
```

Defaults:

- `HERMES_CONTROL_HOST=127.0.0.1`
- `HERMES_CONTROL_PORT=8765`
- `HERMES_STATE_DIR=state`

The host must be loopback: `127.0.0.1` or `localhost`.
For a separate private Railway Control API service, set `HERMES_CONTROL_HOST=0.0.0.0` only with `HERMES_CONTROL_BIND_PRIVATE_NETWORK=true`, and do not expose a public domain.

## Colocated Railway Service

The selected Railway deployment model is one `hermes-trading` service running:

```powershell
uv run python -m hermes_trading.colocated
```

This starts the worker, then starts the Control API on loopback when `HERMES_CONTROL_HMAC_SECRET` is present, and starts the Discord bot when all Discord secrets are present. The Control API remains private to the container through `HERMES_CONTROL_BASE_URL=http://127.0.0.1:8765`.

## Local Control Client

In another shell with the same HMAC secret:

```powershell
$env:HERMES_CONTROL_HMAC_SECRET = "<same-private-random-secret>"
uv run python scripts/control_command.py status
uv run python scripts/control_command.py qualification
```

The qualification command defaults to `validation_ready`.

## Discord Bot Service

Required environment variables:

- `HERMES_DISCORD_BOT_TOKEN`
- `HERMES_DISCORD_ALLOWED_USER_IDS`
- `HERMES_CONTROL_HMAC_SECRET`
- `HERMES_DISCORD_TOTP_SECRET`
- `HERMES_CONTROL_BASE_URL`, optional, defaults to `http://127.0.0.1:8765`
- `HERMES_CONTROL_ALLOWED_HOSTS`, required only when the Control API URL is a private Railway host instead of loopback

Start the bot:

```powershell
uv run python scripts/run_discord_bot.py
```

Discord slash commands:

- `/status otp:<code>`
- `/qualification otp:<code>`

Responses are ephemeral. Unauthorized Discord users and invalid TOTP codes are rejected before the bot calls the control API.

Smoke test Discord connectivity, command publication, and message sending to the `trading` channel without printing secrets:

```powershell
uv run python scripts/discord_bot_smoke.py --send-channel-message --channel-name trading
```

On Railway, this verifies the bot token, the global `status` and `qualification` slash commands, then posts a prompt in the `trading` channel asking the operator to respond with `/status` and `/qualification`:

```powershell
railway.cmd ssh "cd /app && uv run python scripts/discord_bot_smoke.py --send-channel-message --channel-name trading"
```

## Qualification Evidence

The state runner reads optional typed evidence from:

```text
state/qualification-evidence.json
```

Expected keys:

- `replay_without_lookahead`
- `fees_and_slippage_charged`
- `duplicate_candle_protected`
- `risk_limits_exercised`

Missing keys remain visible as `missing` gates in qualification reports.

Generate the replay evidence file from the current journals:

```powershell
uv run python scripts/generate_qualification_replay.py --state-dir state
```

Generate the remaining qualification evidence from canonical state:

```powershell
uv run python scripts/generate_final_qualification_evidence.py --state-dir state
```

Print the current evidence status without secrets or trading actions:

```powershell
uv run python scripts/qualification_evidence_status.py --state-dir state
```

In Railway, use the mounted state directory:

```powershell
railway.cmd ssh "cd /app && uv run python scripts/generate_qualification_replay.py --state-dir /app/state"
railway.cmd ssh "cd /app && uv run python scripts/generate_final_qualification_evidence.py --state-dir /app/state"
railway.cmd ssh "cd /app && uv run python scripts/qualification_evidence_status.py --state-dir /app/state"
```

## Safe Pre-Deploy Verification

On 2026-08-01, the local pre-deploy checks passed:

- `uv run python -m unittest discover -s tests`: 331 tests passed.
- `graphify update .`: completed and refreshed `graphify-out`, with graph metadata warnings only.
- `git diff --check`: passed.

Read-only Railway checks confirmed project `awake-abundance`, production service `hermes-trading` is online, `HERMES_TRADING_MODE=training`, and `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.

## Worker Deploy Verification

After explicit user confirmation on 2026-08-01, the worker service was deployed with:

```powershell
railway.cmd up --service hermes-trading --environment production --message "Deploy Hermes control and Discord hardening"
```

Deployment `68f878ea-a973-4be8-b471-b760e6a4beac` completed with status `SUCCESS`. Railway status reports the production `hermes-trading` service online, mounted at `/app/state`, and the deployment logs show:

```text
Booting hermes-trading worker for BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT in training mode
```

Post-deploy SSH verification returned:

```text
HERMES_TRADING_MODE=training
HERMES_TRADING_ALLOW_REAL_ORDERS=false
```

No Railway variables were changed. Real orders were not enabled. No phase was promoted.

Railway currently lists only the `hermes-trading` worker service. That service is now the intended home for the worker, loopback Control API, and Discord bot.

## Co-located Deploy Verification

On 2026-08-01, after the co-location decision, the worker service was deployed with:

```powershell
railway.cmd up --service hermes-trading --environment production --message "Fix colocated control API smoke tests"
```

Deployment `2efd6df4-e22f-4271-980c-d1f81ff198fc` completed with status `SUCCESS`.

Deployment logs show:

```text
Hermes colocated control API listening on http://127.0.0.1:8765
Hermes colocated Discord bot starting
Booting hermes-trading worker for BTC/USDT, ETH/USDT, XRP/USDT, SHIB/USDT in training mode
Hermes Discord bot logged in as Hermès Trader#0464
```

Post-deploy SSH verification returned:

```text
HERMES_TRADING_MODE=training
HERMES_TRADING_ALLOW_REAL_ORDERS=false
```

In-container smoke tests returned:

```text
status=ok mode=training real_orders_allowed=false
qualification phase=validation_ready promotable=false promoted=false run_id=qualification-6fdad236-07f6-4f20-9a68-08e9e1681128
```

The Discord bot token used for implementation was temporary and should be regenerated after validation. Do not record token values in docs or logs.
