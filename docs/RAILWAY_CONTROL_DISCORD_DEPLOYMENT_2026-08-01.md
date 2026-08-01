# Railway Control And Discord Deployment

Use one Railway service for the worker, private loopback Control API, and Discord bot. This keeps every control request inside the container while preserving the existing `/app/state` volume and worker uptime.

Worker-only command:

`uv run python -m hermes_trading.run`

Colocated worker, Control API, and Discord bot command:

`uv run python -m hermes_trading.colocated`

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
- `HERMES_CONTROL_BASE_URL=http://127.0.0.1:8765`

Local preflight, without printing secret values:

```powershell
uv run python scripts/check_control_discord_secrets.py control-api
uv run python scripts/check_control_discord_secrets.py discord-bot
uv run python scripts/check_control_discord_secrets.py all
```

## Railway Colocation Sequence

Do not change the existing worker safety variables when provisioning Control API or Discord bot secrets.

1. Keep the existing Railway service `hermes-trading`.
2. Keep its volume mounted at `/app/state`.
3. Use command `uv run python -m hermes_trading.colocated`.
4. Keep `HERMES_TRADING_MODE=training`.
5. Keep `HERMES_TRADING_ALLOW_REAL_ORDERS=false`.
6. Set `HERMES_CONTROL_HMAC_SECRET`, `HERMES_STATE_DIR=/app/state`, `HERMES_CONTROL_HOST=127.0.0.1`, and `HERMES_CONTROL_PORT=8765`.
7. Set the Discord bot token, allowed user IDs, TOTP secret, shared HMAC secret, and `HERMES_CONTROL_BASE_URL=http://127.0.0.1:8765`.
8. Deploy the existing `hermes-trading` service.
9. Verify the worker logs include the training boot line and colocated control startup line.
10. Verify `/status` and `/qualification` from the allowed Discord account with a fresh TOTP code.

The Control API must not have a public Railway domain. In the colocated setup it binds to loopback only.

Expected safe smoke-test results:

```text
status=ok mode=training real_orders_allowed=false
qualification phase=validation_ready promotable=false promoted=false run_id=qualification-...
```
