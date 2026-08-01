# Training Strategy Integrity Plan

## Goal

Make training results economically interpretable before any Kraken validation-only
work is promoted. Railway remains in `training` throughout this plan.

## Scope

1. Remove the obsolete one-candle `_paper_trade` path.
2. Extend simulated positions with take-profit, RSI-target and maximum-bars exits.
3. Replace falling-RSI entries with RSI recovery plus price confirmation.
4. Bound reflection changes per asset: RSI threshold 15..35, step <= 2, at least
   20 closed trades, 5 new closed trades, and a 12-hour cooldown.
5. Reject entries whose configured payoff cannot cover modeled round-trip costs.
6. Add EMA trend filtering after the market-data history contract is expanded.
7. Add ATR/ADX only after deterministic OHLC history is available and tested.

## Acceptance Criteria

- A simulated trade is only recorded when a durable position is closed.
- Every exit has a human-readable reason and includes fees/slippage in net P&L.
- No entry occurs while RSI is still falling below its threshold.
- Reflection cannot widen an RSI oversold threshold beyond 35 or change it by
  more than two points, and cannot react to no-trade decisions alone.
- Tests cover every entry/exit condition, bounds, cooldown and cost rejection.
- Full tests pass; no deployment changes execution mode or enables Kraken orders.
