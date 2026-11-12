# Prospective paper execution reconciliation

Scope: the approved adaptive/control recovery experiment, before activation. Existing historical training behavior is not evidence of achievable prospective fills. This is a staged design and validation plan; no new experiment, backtest, paid model, deployment or real order is launched.

## Evidence motivating the next change

PositionEngine.process_candle sizes entries using the signal reference, while _entry fills at candle.close. Full exits use candle open/stop/target or close; _partial_exit fills at close. Decisions and fills currently carry the candle close timestamp. A collector acting after that timestamp cannot establish that any of those prices were still available. Risk liquidation already uses the captured bid, but that does not correct ordinary executions. Maker declarations also receive immediate fills without order-book/queue evidence. Consequently the current runner must not be activated or qualified as executable profit evidence.

## Intended bounded change

Add an explicit prospective execution mode used by the paired runner, with logical decision time frozen in the durable common packet. Closed candles remain signal inputs and deterministic opportunity identifiers. Actual simulated entry prices use the captured ask plus original-contract slippage; full and partial exits use captured bid minus original-contract slippage. Decision/order/fill times use the frozen decision time, and existing-position candle sequencing remains tied to the original signal candle rather than conflating opened_at with a candle timestamp.

Use the same prospective price for quantity sizing, venue minimum checks, initial protection, fees, cash reservations and final fills. Validate positive finite bid/ask, bid <= ask, receipt time at or after the signal candle (and outstanding intent trigger), no later than decision time and age <= five minutes. Missing or invalid quotes must never produce a candle-price fallback. Block new entries, preserve an explicit pending exit when no usable quote exists, and allow subsequent captured quotes to execute that exit without requiring a newly closed candle. Full inventory falling below venue minimums remains unfilled and explicitly pending. Keep intent identity and restart replay deterministic; do not rewrite history or use a later quote on the original batch.

There are no resting stop/target orders in this paper mode. Evaluate protective price conditions against the captured bid instead of using earlier candle extrema as execution opportunities. Missing quotes may establish a pending signal, but cannot establish a fill. Persist upgraded full protection when a pending partial encounters a stop, including when the full close is temporarily below minimums. Missing-contract recovery has priority over discretionary pending intents. Holding-period bar counts use the canonical entry signal candle while actual decision/fill timestamps remain separate.

Prospective baseline uses taker orders. Reject unsupported maker execution contracts before publication/execution; do not grant a cheaper fee or an instant limit fill merely because the LLM chose maker. A future maker simulator requires separate fill evidence and validation. Existing historical training callers retain their explicitly labeled candle simulation behavior.

## Verification before activation

- Divergent candle close versus quote: buy at ask and sell at bid; report spread, slippage and original entry-version fees once.
- Exact quantity, venue minimum and cash checks use the eventual execution price, including partial-leg and remainder minima.
- Decision timestamp after candle close does not prevent the next legitimate candle from managing a position; old candles cannot drive a newer entry retrospectively.
- Missing, stale, future, crossed, negative and nonfinite quotes produce no fabricated fills. Protective intent can complete on a later valid quote even if the strategy candle is unchanged.
- Crash/restart retains the original packet price/time; each order and close occurs once. Both arms receive the same quote event.
- Unsupported maker proposals are rejected before publication, without altering the reviewed five-trade watermark or producing a changed-version receipt.
- Full existing suite, focused prospective cases and independent scoped review pass; source bindings and evidence are refreshed.

Still required afterward: accrued-cost freshness and verified model access, bounded collector/heartbeat and startup release binding, controlled paper release, prospective checkpoint and independently validated profitability assessment. Realistic paper execution is necessary evidence infrastructure, not proof of a profitable strategy.

## Verified local checkpoint

Implemented in prospective_execution.py and PositionEngine, selected explicitly by the paired runner using its durable logical batch time. Pending intent incidents, financial outcomes and resolution commit together in the canonical account database. Canonical buy-intent/decision history supplies the entry signal time for bar-age evaluation. Unsupported maker baselines fail before common collection; the numeric one-variable publication menu cannot change maker/taker fees from the approved taker baseline.

14engine-focused tests and769full-suite tests pass (108.562s). Regression cases first reproduced quote-before-signal execution, delayed max_bars, pending partial overriding stop, protective escalation lost at venue minimum, and pending partial masking missing-contract recovery; all now pass. Independent final read-only follow-up found no further important scoped blocker. Evidence research/runs/prospective-paper-execution-v1. An earlier full run was invalidated by source edits during execution and correctly rejected a release hash mismatch; the final passing run used unchanged source. AST graph updated without paid semantic calls.

No deployment, experiment activation, strategy model request or real order occurred. Received quote age is not verified exchange book age; top-of-book modeled fills, poll intervals and absent resting orders remain explicit execution limitations. These tests establish local behavior, not achievable profits.
