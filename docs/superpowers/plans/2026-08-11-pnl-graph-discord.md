# P&L Graph Discord Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Discord `/pnl_graph` command that returns a PNG chart of wallet amount, cumulative realized P&L, and per-symbol realized P&L.

**Architecture:** Add a focused P&L graph module, expose it through the existing signed local control API, add a bytes-capable control client call, then wire the Discord slash command to send an ephemeral PNG attachment. Keep Discord as a thin control API client.

**Tech Stack:** Python 3.11+, `StateRepository`, `Decimal`, `matplotlib` with the non-interactive `Agg` backend, `discord.py`, `httpx`, `unittest`.

## Global Constraints

- Use the qualification window by default: `qualification-training-started-at.txt` through current control API time.
- Exclude closed positions before the window start and after current time.
- Starting wallet comes from `HERMES_QUALIFICATION_STARTING_WALLET_BALANCE`, defaulting to `50`.
- Discord command output is an ephemeral PNG image attachment.
- The control API endpoint must remain HMAC-signed and reject unauthorized requests.

---

### Task 1: P&L Series And PNG Renderer

**Files:**
- Create: `hermes_trading/pnl_graph.py`
- Test: `tests/test_pnl_graph.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `StateRepository.closed_positions()`, `resolve_starting_wallet_balance(env)`
- Produces: `PnlGraphPoint`, `PnlGraphSeries`, `build_pnl_graph_series(repository, state_dir, now, env=None)`, `render_pnl_graph_png(series) -> bytes`

- [ ] **Step 1: Write failing tests**

Add tests that create closed BTC/ETH positions before, inside, and after the qualification window. Assert that the series excludes out-of-window positions, calculates wallet and cumulative P&L, and returns PNG bytes beginning with the PNG signature.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_pnl_graph -v`
Expected: failure because `hermes_trading.pnl_graph` does not exist.

- [ ] **Step 3: Implement minimal module**

Create dataclasses for graph points and series. Implement window loading, closed-position filtering, cumulative calculations, and PNG rendering with `matplotlib.use("Agg")`.

- [ ] **Step 4: Run tests and verify pass**

Run: `uv run python -m unittest tests.test_pnl_graph -v`
Expected: pass.

### Task 2: Control API Image Endpoint

**Files:**
- Modify: `hermes_trading/control_api.py`
- Test: `tests/test_control_api.py`

**Interfaces:**
- Consumes: `render_pnl_graph_png(build_pnl_graph_series(...))`
- Produces: `GET /pnl-graph` returning `image/png` bytes when authorized

- [ ] **Step 1: Write failing API tests**

Add tests that a signed `GET /pnl-graph` returns status `200`, `content_type == "image/png"`, and PNG bytes, while an unsigned request returns `401`.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_control_api -v`
Expected: failure because `/pnl-graph` is not routed.

- [ ] **Step 3: Implement endpoint**

Allow `ControlResponse.body` to be `str | bytes`, update `make_control_handler()` encoding logic, and add the authorized `GET /pnl-graph` route.

- [ ] **Step 4: Run tests and verify pass**

Run: `uv run python -m unittest tests.test_control_api -v`
Expected: pass.

### Task 3: Control Client Bytes Call

**Files:**
- Modify: `hermes_trading/control_client.py`
- Test: `tests/test_control_client.py`

**Interfaces:**
- Produces: `ControlClient.pnl_graph() -> tuple[bytes, str]`

- [ ] **Step 1: Write failing client test**

Add a test with an injected byte request function. Assert that `ControlClient.pnl_graph()` signs `GET /pnl-graph` and returns PNG bytes plus `image/png`.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_control_client -v`
Expected: failure because `pnl_graph()` does not exist.

- [ ] **Step 3: Implement bytes request path**

Add `request_bytes` injection, `_signed_request_bytes()`, `_httpx_request_bytes()`, and `pnl_graph()`.

- [ ] **Step 4: Run tests and verify pass**

Run: `uv run python -m unittest tests.test_control_client -v`
Expected: pass.

### Task 4: Discord Slash Command

**Files:**
- Modify: `hermes_trading/discord_bot.py`
- Test: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `ControlClientLike.pnl_graph() -> tuple[bytes, str]`
- Produces: `DiscordControlBot.pnl_graph(user_id, otp) -> tuple[bytes, str] | str`
- Produces Discord command: `/pnl_graph otp:<code>`

- [ ] **Step 1: Write failing Discord tests**

Extend the fake control client, fake Discord module, and handler tests. Assert authorization gates still work and the slash command sends an ephemeral file named `hermes-pnl-graph.png`.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_discord_bot -v`
Expected: failure because `pnl_graph` is not implemented.

- [ ] **Step 3: Implement bot method and command**

Add the protocol method, bot method, and slash command. Use `io.BytesIO` and `discord.File(..., filename="hermes-pnl-graph.png")`.

- [ ] **Step 4: Run tests and verify pass**

Run: `uv run python -m unittest tests.test_discord_bot -v`
Expected: pass.

### Task 5: Qualification Window Safety Regression

**Files:**
- Modify: `hermes_trading/qualification_runner.py`
- Test: `tests/test_qualification_runner.py`

**Interfaces:**
- Tightens existing scoped closed-position behavior to `started_at <= closed_at <= current`.

- [ ] **Step 1: Write failing regression test**

Add a test with one current closed position and one future-dated closed position. Assert only the current position contributes to closed count and P&L.

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run python -m unittest tests.test_qualification_runner -v`
Expected: failure because future-dated positions currently count.

- [ ] **Step 3: Implement upper-bound filter**

Parse `closed_at` once and require it to be less than or equal to `current`.

- [ ] **Step 4: Run tests and verify pass**

Run: `uv run python -m unittest tests.test_qualification_runner -v`
Expected: pass.

### Task 6: Verification And Graphify Update

**Files:**
- Modify: `docs/COMMANDS.md` if useful command documentation is missing.
- Generated: `graphify-out/*` via `graphify update .`

**Interfaces:**
- Confirms all changed behavior works together.

- [ ] **Step 1: Run focused tests**

Run: `uv run python -m unittest tests.test_pnl_graph tests.test_control_api tests.test_control_client tests.test_discord_bot tests.test_qualification_runner -v`
Expected: pass.

- [ ] **Step 2: Run broader relevant tests**

Run: `uv run python -m unittest tests.test_qualification tests.test_operational_scripts tests.test_notifications -v`
Expected: pass.

- [ ] **Step 3: Update code graph**

Run: `graphify update .`
Expected: graphify finishes without errors.

- [ ] **Step 4: Review diff**

Run: `git diff --stat`
Expected: changes are limited to P&L graph, control API/client, Discord bot, tests, docs, dependency metadata, and graphify output.

## Self-Review

- Spec coverage: the plan covers image generation, wallet series, cumulative and per-symbol P&L, signed API delivery, Discord attachment delivery, and qualification-window consistency.
- Placeholder scan: no placeholders remain.
- Type consistency: endpoint, client, and Discord method all use `tuple[bytes, str]` for image bytes and content type.
