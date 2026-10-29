# First-entry history correction — October 29

Deployment pending. The first-trade audit found a separate entry blocker after supervisor recovery: `market_data._normalized_price` retained OHLC values but discarded candle timestamps. The doubled-cost entry reserve requires those timestamps to validate closed, ordered history. Every ATR-based entry through the public collector could therefore return zero reserved quantity, reported as `cost_reserve_entry_rejected`.

A read-only native observation at 17:47 UTC found healthy admission, no orders, and one such recorded rejection in each of the eight accounts. Current data contained 255 OHLC rows per symbol with no retained history timestamps. Current RSI recovery conditions were also false. The five-close AI-review trigger is not a prerequisite for first entries.

The correction preserves each supplied candle timestamp as an aware UTC string, including exchange millisecond timestamps. It does not invent missing times or accept malformed history. Existing closed-bar selection, ATR values, signals, fees, minimum sizes, risk sizing, cost reserves and all eight strategies are unchanged. Historical packets and consumed candles remain intact.

The synthetic collector-to-position regression failed with `cost_reserve_entry_rejected` before this change and opens a minimum-compliant position afterward. Separate checks cover open-bar exclusion, exact source times, missing/reordered history rejection and millisecond normalization. No market backtest, training, model invocation or real order is involved.

Deployment uses the existing checked two-host transition from exact predecessor `c7f816d`, permitting only `market_data.py` to change in the runtime map. Original opening, clocks, history, halt state and risk limits are preserved. The operator helper update adds this pinned predecessor and delta; it grants no general release bypass.

At the audited prices, exchange minimum notionals were about EUR4.34 BTC, EUR4.18 ETH and EUR4.63 XRP, below the EUR5 absolute cap. Smaller wallets may still be rejected by their risk budget and doubled-cost reserve. Signal readiness and size feasibility must be rechecked after deployment; this correction does not establish profitability or guarantee when a trade will occur.
