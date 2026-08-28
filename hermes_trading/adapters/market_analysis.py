from __future__ import annotations

import json
import os
from typing import Any

import httpx

from hermes_trading.adapters import require_schema

SCHEMA_VERSION = 1

DEFAULT_MODEL = "huggingface.co/ewinregirgojr/minicpm5-mtp-gguf-stock-analyst:Q8_0"
DEFAULT_PORT = "12434"
DEFAULT_TIMEOUT_SECONDS = "30"
_VALID_BIAS = {"bullish", "bearish", "neutral"}


def _build_prompt(asset: str, snapshot: dict[str, Any]) -> str:
    return (
        "You are a market analyst reviewing exactly one trading symbol for a "
        "quantitative trading system. Base your answer only on the data given "
        "below -- do not assume access to any other information.\n"
        "Return exactly one JSON object, no markdown, no extra text.\n\n"
        "Required keys:\n"
        '- "bias": exactly one of "bullish", "bearish", "neutral"\n'
        '- "confidence": a number from 0.0 to 1.0\n'
        '- "summary": one concise sentence explaining the bias\n\n'
        f"Symbol: {asset}\n"
        f"Market snapshot:\n{json.dumps(snapshot, sort_keys=True, default=str)}\n"
    )


def _parse_response(raw_text: str) -> dict[str, Any]:
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("market analysis response must be a JSON object")

    bias = str(parsed.get("bias", "")).strip().lower()
    if bias not in _VALID_BIAS:
        raise ValueError(f"unsupported bias value: {parsed.get('bias')!r}")

    try:
        confidence = float(parsed.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    confidence = max(0.0, min(1.0, confidence))

    summary = str(parsed.get("summary", "")).strip()[:280]

    return {"bias": bias, "confidence": round(confidence, 4), "summary": summary}


def fetch(
    asset: str,
    snapshot: dict[str, Any],
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Ask a locally-hosted market-analysis model (served via Docker Model
    Runner's OpenAI-compatible endpoint, same one reflect.py's
    ``_run_local_model_prompt`` already talks to) for a qualitative read on
    one asset.

    This is advisory context for reflect.py's hours-cadence strategy-tuning
    prompt only -- see reflect.py::_fetch_market_analysis and
    _build_asset_reflection_prompt. It must never be wired into loop.py's
    live per-cycle entry/exit decision: the default model is a small
    (~1B parameter), unverified community fine-tune, and a first smoke test
    already produced an internally inconsistent answer (bias disagreeing
    with its own summary text). Treat the output as one more noisy opinion
    for a human-reviewable hypothesis log, never as a signal a live order
    depends on.
    """
    env = dict(os.environ if env is None else env)
    model = env.get("HERMES_MARKET_ANALYSIS_MODEL", "").strip() or DEFAULT_MODEL
    port = env.get("HERMES_LOCAL_MODEL_PORT", "").strip() or DEFAULT_PORT
    timeout = float(env.get("HERMES_MARKET_ANALYSIS_TIMEOUT_SECONDS", "").strip() or DEFAULT_TIMEOUT_SECONDS)

    response = httpx.post(
        f"http://127.0.0.1:{port}/engines/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": _build_prompt(asset, snapshot)}],
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("market analysis model returned no choices")
    text = choices[0].get("message", {}).get("content")
    if not text:
        raise ValueError("market analysis model response did not contain text output")

    analysis = _parse_response(text)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "model": model,
        **analysis,
    }
    return require_schema(payload, SCHEMA_VERSION)
