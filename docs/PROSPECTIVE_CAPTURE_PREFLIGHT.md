# Prospective capture preflight

October8,2026. One bounded public WebSocket smoke test completed successfully. No orders, credentials, production changes, simulated positions or180-day observation were started. This is a feasibility result, not trading performance.

Artifacts: `research/runs/cross-sectional-feasibility-v1/capture-smoke-v1/`. Capture ran19:31:22–19:32:27UTC (approximately65seconds), subscribing to the12 size-fitting symbols from the prior snapshot. It retained every received message with a local receipt timestamp, compressed raw payloads and SHA256. This diagnostic stored data at completion; it is not a crash-safe production collector.

There were1,939 total messages:12 subscription confirmations, one information message and1,926 ticker messages. All12 symbols were observed. Across captured ticker messages, maximum local-receipt minus exchange-server timestamp was1,111.27ms, and largest per-symbol inter-receipt gap was2,001.60ms. No positive-price/uncrossed-quote failures or missing current/relative/next-time funding fields were found. These measurements describe this sample only. They do not establish actual executable fills, message completeness, book sequencing or future availability.

An accompanying REST clock probe had145.69ms round-trip time and server time53.93ms behind the local request midpoint. Network asymmetry prevents treating this as an exact clock offset. It reveals no obvious multi-second clock mismatch during the sample; ongoing clock monitoring remains necessary.

All12 WebSocket current absolute funding rates matched the contemporaneous REST ticker values within1e-14. A separate SOL historical funding response reports19:00UTC rate0.000964617133333297 and relative rate0.000008928333333333, both matching the stream around19:31. Its next funding timestamp is20:00UTC. This corroborates earlier same-provider timing evidence. Absolute and relative rates must remain distinct; do not multiply an absolute per-unit funding rate by price again, or treat the prediction as the paid rate. No hourly transition was observed in this65-second sample.

[Kraken's public ticker specification](https://docs.kraken.com/exchange/api-reference/futures-websocket/ticker) identifies current versus predicted funding, per-message server timestamps, suspension/post-only flags and base versus quote volume fields. It says zero funding fields can be omitted; the eventual parser must distinguish that documented representation from truncated, malformed or missing messages. The next-time field's example and observed epoch value identify an absolute timestamp despite its ambiguous prose. Require explicit normalization tests.

Raw messages occupy1,479,875bytes, gzip48,623bytes. This sample is not a storage budget for the whole market universe or six months; volume, active subscriptions and retention policy differ. The prior REST snapshot extrapolation likewise does not measure a continuous WebSocket collector.

## Remaining launch prerequisites

- Verify candle-volume units and lot increments against raw executions/instrument specifications.
- Implement deterministic parsing and an append-only collector with crash-safe writes, manifests, restarts, reconnections, subscription failures and explicit gap reporting. The smoke-test script was an inline diagnostic and is not production-ready collection infrastructure.
- Test at least an hourly funding boundary and a deliberately interrupted/reconnected public feed. Verify freshness and simultaneous-pair quote availability on the recorded minute grid, not merely overall message maxima.
- Implement and test the frozen signal/accounting model, especially stress sizing, closing-cost equity, delayed stops and terminal settlement. Complete independent implementation review.
- Establish data/specification refresh operation and measured storage/cadence over a longer bounded soak before fixing the180-day start date. No process remains running after this test.

The prospective protocol and profitability gates remain unchanged. Goal completion remains unproven.

## October9 quantity and persistence checkpoint

EOS's final trading hour was cross-checked against the public historical execution endpoint:10unique execution IDs, all in the requested hour, sum to293base units, exactly matching candle volume. Sum(price*quantity)=USD205.6184, and reported execution USD values differ by at mostUSD0.004from those products (rounding). The candle high/low/close match executions, but candle open0.7089does not match first observed execution0.6935. This supports base-unit volume for this sample and demonstrates why candle opens cannot be assumed executable. Raw executions and `EOS_volume_unit_validation.json` are saved in the parent feasibility directory; wider instrument/lot-step verification remains open.

Implemented `hermes_trading/market_capture.py`: finite Decimal ticker parsing with status/freshness checks and documented omitted-zero provenance; a single-writer SQLite journal with explicit resume/configuration verification, committed hash-chained records and bounded atomic batches. It has no exchange client, trade submission, network reconnect loop or background scheduler. The journal's integrity limits explicitly include undetectable tail removal or adversarial rehashing without external checkpoints.

Independent review identified inconsistent funding signs and binary-float parsing; both were reproduced in failing tests and fixed. Eleven focused tests now cover precision/signs, bad numeric/status fields, unavailable quotes, omitted-zero versus malformed records, resume mismatch, tampering, duplicate writers and atomic batch validation/reopen. An initial test cleanup failure was a test-owned SQLite connection left open on Windows; it was closed explicitly.

Historical smoke replay retained1,939raw messages and parsed1,926tickers. Per-record FULL synchronous commits took264.297seconds (7.34records/sec), below the observed feed rate. Atomic batches of100completed in2.094seconds (925.98records/sec), including verification. Both artifacts are preserved (`replay_journal.sqlite`, `batch_replay_journal.sqlite`, associated validation JSON). This demonstrates the need for batching locally; it is not a live network performance guarantee. The future collector must timestamp on receipt, write through a separate bounded queue, cap flush latency, and treat uncommitted crash intervals as missing. Full-history hash verification is linear in stored history; bounded segments/checkpoints are still needed for a long run.

The user's EUR50–200 and every-few-trades LLM-steering requirements supersede qualification based only on the fixed strategy. See `SMALL_WALLET_LLM_EVALUATION.md`. The fixed rule is a control; the adaptive policy, AI costs and wallet-specific feasibility must be validated as part of the system. No paid model calls, production integration or prospective performance start occurred.
