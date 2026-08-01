# Railway Control And Discord Deployment

Use separate Railway services for the worker, private local control API, and Discord bot. Do not deploy from this note until the required secrets and Railway service settings are reviewed.

Worker service command:

`uv run python -m hermes_trading.run`

Control API service command:

`uv run python scripts/run_control_api.py`

Discord bot service command:

`uv run python scripts/run_discord_bot.py`

Safety variables for the worker:

- `HERMES_TRADING_MODE=training`
- `HERMES_TRADING_ALLOW_REAL_ORDERS=false`

Keep `HERMES_TRADING_ALLOW_REAL_ORDERS=false` for all worker packaging and deployment verification. The control API and Discord bot services must not be used to enable real Kraken orders or automatically promote a phase.

## Secrets Readiness

The Control API service needs:

- `HERMES_CONTROL_HMAC_SECRET`
- `HERMES_STATE_DIR=/app/state`

The Discord bot service needs:

- `HERMES_DISCORD_BOT_TOKEN`
- `HERMES_DISCORD_ALLOWED_USER_IDS`
- `HERMES_CONTROL_HMAC_SECRET`, same value as the Control API service
- `HERMES_DISCORD_TOTP_SECRET`
- `HERMES_CONTROL_BASE_URL=http://127.0.0.1:8765` for a co-located local setup, or the private Railway Control API URL for separate services
- `HERMES_CONTROL_ALLOWED_HOSTS=<control-api-private-host>` when `HERMES_CONTROL_BASE_URL` is not loopback

Local preflight, without printing secret values:

```powershell
uv run python scripts/check_control_discord_secrets.py control-api
uv run python scripts/check_control_discord_secrets.py discord-bot
uv run python scripts/check_control_discord_secrets.py all
```

## Railway Provisioning Sequence

Do not change the existing worker variables when provisioning the Control API or Discord bot services.

1. Create or select the Control API Railway service.
2. Set its command to `uv run python scripts/run_control_api.py`.
3. Attach the same `/app/state` volume or confirm the intended shared state path before starting it.
4. Set `HERMES_CONTROL_HMAC_SECRET`, `HERMES_STATE_DIR=/app/state`, `HERMES_CONTROL_HOST=0.0.0.0`, and `HERMES_CONTROL_BIND_PRIVATE_NETWORK=true`.
5. Deploy the Control API service and smoke test `uv run python scripts/control_command.py status`.

The Control API and Discord bot allow Railway private-network URLs only through an explicit host allowlist. Do not use a public Railway domain for the Control API.

Discord bot service:

1. Create or select the Discord bot Railway service.
2. Set its command to `uv run python scripts/run_discord_bot.py`.
3. Set the Discord bot token, allowed user IDs, TOTP secret, shared HMAC secret, `HERMES_CONTROL_BASE_URL`, and `HERMES_CONTROL_ALLOWED_HOSTS`.
4. Deploy the Discord bot service.
5. Verify `/status` and `/qualification` from an allowed Discord account with a fresh TOTP code.

Expected safe smoke-test results:

```text
status=ok mode=training real_orders_allowed=false
qualification phase=validation_ready promotable=false promoted=false run_id=qualification-...
```
