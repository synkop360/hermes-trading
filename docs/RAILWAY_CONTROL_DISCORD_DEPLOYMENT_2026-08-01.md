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
