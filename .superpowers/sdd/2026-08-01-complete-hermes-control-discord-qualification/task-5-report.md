# Task 5 Report

Implemented the private Discord control bot service with loopback-only control API configuration, Discord user allow-listing, TOTP validation, HMAC-signed status and validation-only qualification requests, and an async launcher.

Added the `discord-py` dependency, operational documentation for the required environment variable names, and coverage for configuration, authorization, request signing, non-promoting qualification, and launcher importability.

Verification: `uv run python -m unittest tests.test_discord_bot tests.test_operational_scripts` passed (7 tests).

Fix review update: `DiscordControlBot` now delegates status and qualification calls to an injectable `ControlClient`-style dependency after Discord allow-list and TOTP authorization, removing duplicate signing and raw request logic from the Discord layer. Discord slash command handlers now offload blocking bot calls with `asyncio.to_thread`.

Fix verification: `uv run python -m unittest tests.test_discord_bot tests.test_operational_scripts` passed (8 tests).

Fix round 2 update: Discord slash command handlers now defer the interaction response ephemerally before starting the network-backed control call in `asyncio.to_thread`, then send the result through `interaction.followup.send`. The regression coverage now exercises fake registered slash command handlers and verifies `defer -> to_thread -> followup` ordering for both `/status` and `/qualification` without requiring `discord.py`.

Fix round 2 verification: `uv run python -m unittest tests.test_discord_bot tests.test_operational_scripts` passed (8 tests).

Fix round 2 commit: see latest Task 5 fix commit.

Fix round 2 concerns: no known concerns; no real Discord gateway integration test is run in this environment.
