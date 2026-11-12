# Reflection outcome diagnostics

Bounded correction authorized by autonomous repair scope. Preserve strategy/cadence/risk behavior and deployed state; distinguish a completed deterministic review choosing no change from a model/provider failure. This supports diagnosing and restoring the original adaptive loop without paid model calls.

Evidence: all824October mirrored hermes_asset_failed records have empty errors; all10October applied changes identify provider=deterministic. The deterministic generator returns(None,errors=[])at a parameter bound, and run_hermes_reflection labels every None as model_failed. Separately, external watcher uses the latest hypothesis of any provenance as its due watermark; in-worker deterministic reviews can interfere with LLM review cadence. That ownership issue requires separate evidence and a policy fix; do not silently change it in this diagnostic patch.

1. Write isolated tests using temporary state and injected proposal generators: deterministic bounded no-change; actual provider failure; ambiguous missing proposal. Observe current no-change test fail.
2. Mark explicit no-change outcome/reason in the deterministic generator only at the no-op bound. Log explicit no-change with provider/model and keep original strategy/history unchanged. Preserve true/ambiguous failure paths and add provenance to failure logs.
3. Run focused and existing reflection tests, independent review and graph update. Correct prior alignment report's overly broad LLM-failure inference. No deployment or live state mutation.
