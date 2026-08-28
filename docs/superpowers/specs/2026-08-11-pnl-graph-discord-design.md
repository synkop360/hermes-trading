# P&L Graph Discord Design

## Goal

Add a Discord slash command that returns a PNG graph showing Hermes wallet amount over time, cumulative realized P&L over time, and realized P&L over time per symbol.

## Scope

The first version uses the qualification training window only. It starts at `qualification-training-started-at.txt` when present, otherwise the current control API time. It ends at the current control API time. Closed positions before the start or after the end are excluded.

## Data

The graph is derived from canonical closed positions in `StateRepository.closed_positions()`. Positions are ordered by `closed_at`, and each close event updates:

- `wallet`: starting wallet plus cumulative realized P&L.
- `cumulative_pnl`: total realized P&L across all symbols.
- `symbol_pnl`: cumulative realized P&L for the position's symbol.

The starting wallet comes from `HERMES_QUALIFICATION_STARTING_WALLET_BALANCE`, falling back to the existing default of `50`.

## Architecture

Create a focused `hermes_trading.pnl_graph` module for series calculation and PNG rendering. Expose it through the signed local control API as `GET /pnl-graph`, add a bytes-capable `ControlClient.pnl_graph()` call, and wire a `/pnl_graph` Discord slash command that sends an ephemeral image attachment after TOTP authorization.

## Error Handling

If there are no closed positions in the selected window, return a valid PNG with the starting wallet baseline and an empty P&L baseline. Unauthorized control API requests still return `401`.

## Testing

Add tests for scoped series generation, future-position exclusion, PNG generation, control API bytes responses, client bytes requests, and Discord file delivery.
