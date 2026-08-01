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
- `HERMES_CONTROL_BASE_URL=http://127.0.0.1:8765`

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
4. Set `HERMES_CONTROL_HMAC_SECRET` and `HERMES_STATE_DIR=/app/state`.
5. Deploy the Control API service and smoke test `uv run python scripts/control_command.py status`.

The current Control API and Discord bot code is intentionally loopback-only. A Discord bot running in a separate Railway service cannot call the Control API through Railway private networking until a later hardening change explicitly supports private-network host validation. Until then, keep the bot deployment pending or co-locate it with a loopback Control API process in a controlled service design.

Pending separate-service hardening:

1. Add explicit private-network allowlisting for `HERMES_CONTROL_BASE_URL`.
2. Keep HMAC nonce replay protection enabled.
3. Keep commands limited to status and non-promoting qualification.
4. Verify `promoted=false` and `real_orders_allowed=false` after deployment.

Expected safe smoke-test results:

```text
status=ok mode=training real_orders_allowed=false
qualification phase=validation_ready promotable=false promoted=false run_id=qualification-...
```
