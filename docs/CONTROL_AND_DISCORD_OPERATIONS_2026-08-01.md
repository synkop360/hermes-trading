# Hermes Control And Discord Operations

## Plan Being Followed

This work follows `docs/SESSION_HANDOFF_2026-08-01.md`, section `Immediate next work`.

Current status:

1. SQLite nonce replay persistence: implemented in schema v4.
2. Private local control API: implemented for read-only status and non-promoting qualification requests.
3. Typed qualification evidence collectors: implemented with missing evidence remaining visible as `missing`.
4. Qualification Discord summary notifications: implemented through the existing webhook path.
5. Separate Discord bot service: code and scripts are prepared; deployment still needs real private secrets.

## Safety Constraints

- Keep `HERMES_TRADING_MODE=training` on the current Railway worker unless an explicit later promotion plan says otherwise.
- Keep `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
- The control API only accepts HMAC signed requests and rejects replayed nonces.
- The control server and bot client reject non-loopback control URLs.
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

The host must be loopback: `127.0.0.1`, `localhost`, or `::1`.

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

Start the bot:

```powershell
uv run python scripts/run_discord_bot.py
```

Discord slash commands:

- `/status otp:<code>`
- `/qualification otp:<code>`

Responses are ephemeral. Unauthorized Discord users and invalid TOTP codes are rejected before the bot calls the control API.

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
