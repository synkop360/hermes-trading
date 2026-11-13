# Paper service health

Run the existing `paper_boot.run_paper` unchanged while publishing an allowlist
of technical liveness fields to Railway logs every 60 seconds. Never print
wallet values, positions, batch identities, worker return values or exception
messages. Missing/stale telemetry remains unknown/stale rather than success.

1. Test redaction, missing/stale/future heartbeats, unknown admission reasons,
   worker completion and error propagation with unittest.
2. Implement `scripts/paper_service.py` without trading-engine changes.
3. Test locally with the portable interpreter and review independently.
4. Deploy the reviewed wrapper with the existing image and paper-only guard;
   verify actual fresh common-batch telemetry. Preserve account clocks,
   strategies, risk limits and all native admission/exit checks.
