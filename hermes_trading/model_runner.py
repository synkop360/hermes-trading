from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from hermes_trading.costs import CostLedger, hermes_usage_event
from hermes_trading.providers import ModelCandidate, ProviderAttempt, ProviderRouter


@dataclass(frozen=True)
class ModelRunResult:
    output: str
    provider: str
    model: str | None


class ReflectionModelRunner:
    """Run Hermes automatic selection first; OpenAI is an unremembered fallback."""

    def __init__(
        self,
        router: ProviderRouter,
        *,
        local_run: Callable[[str], str] | None = None,
        openai_run: Callable[[str], str] | None = None,
        command_run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        timeout_seconds: int | None = None,
        cost_ledger: CostLedger | None = None,
    ) -> None:
        self.router = router
        self.local_run = local_run
        self.openai_run = openai_run
        self.command_run = command_run
        self.timeout_seconds = timeout_seconds or int(os.environ.get("HERMES_REFLECTION_TIMEOUT_SECONDS", "60"))
        self.cost_ledger = cost_ledger

    def run(self, prompt: str, *, now: datetime | None = None) -> ModelRunResult:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        errors: list[tuple[str, str | None, Exception]] = []
        try:
            return ModelRunResult(_json_output(self._hermes(prompt)), "hermes_auto", None)
        except Exception as error:
            errors.append(("hermes_auto", "auto", error))
            self.router.store.record_provider_attempt(
                ProviderAttempt("hermes_auto", "auto", _error_class(error), current, _retry_after_seconds(error))
            )

        for candidate in self.router.candidates(now=current):
            try:
                started = datetime.now(UTC)
                output = _json_output(self._hermes(prompt, candidate))
                self.router.record_success(candidate, now=current, latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000))
                return ModelRunResult(output, candidate.provider, candidate.model)
            except Exception as error:
                errors.append((candidate.provider, candidate.model, error))
                self.router.record_failure(
                    candidate,
                    error_class=_error_class(error),
                    retry_after_seconds=_retry_after_seconds(error),
                    now=current,
                )

        if self.local_run is not None:
            if _provider_cooling_down("local_model", "fallback", list(self.router.store.provider_attempts()), current):
                raise RuntimeError(_combined_error(errors + [("local_model", "fallback", RuntimeError("local model fallback cooling down"))]))
            try:
                return ModelRunResult(_json_output(self.local_run(prompt)), "local_model", None)
            except Exception as error:
                errors.append(("local_model", "fallback", error))
                self.router.store.record_provider_attempt(
                    ProviderAttempt("local_model", "fallback", _error_class(error), current, _retry_after_seconds(error))
                )

        if self.openai_run is not None:
            if _provider_cooling_down("openai_api", "fallback", list(self.router.store.provider_attempts()), current):
                raise RuntimeError(_combined_error(errors + [("openai_api", "fallback", RuntimeError("openai_api fallback cooling down"))]))
            try:
                return ModelRunResult(_json_output(self.openai_run(prompt)), "openai_api", None)
            except Exception as error:
                errors.append(("openai_api", "fallback", error))
                self.router.store.record_provider_attempt(
                    ProviderAttempt("openai_api", "fallback", _error_class(error), current, _retry_after_seconds(error))
                )
        raise RuntimeError(_combined_error(errors))

    def _hermes(self, prompt: str, candidate: ModelCandidate | None = None) -> str:
        command = ["hermes", "-z", prompt]
        descriptor, raw_usage_path = tempfile.mkstemp(prefix="hermes-usage-", suffix=".json")
        os.close(descriptor)
        usage_path = Path(raw_usage_path)
        command.extend(["--usage-file", str(usage_path)])
        if candidate is not None:
            command.extend(["--provider", candidate.provider, "-m", candidate.model])
        try:
            command_run = self.command_run or subprocess.run
            result = command_run(command, check=True, capture_output=True, text=True, timeout=self.timeout_seconds)
            self._record_usage(candidate, usage_path)
            return result.stdout
        finally:
            try:
                usage_path.unlink()
            except FileNotFoundError:
                pass

    def _record_usage(self, candidate: ModelCandidate | None, usage_path: Path) -> None:
        if self.cost_ledger is None or candidate is None or not usage_path.exists():
            return
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            usage = {}
        self.cost_ledger.record(
            hermes_usage_event(
                provider=candidate.provider,
                model=candidate.model,
                free=candidate.free,
                usage=usage,
            )
        )


def _json_output(output: str) -> str:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return json.dumps(value, separators=(",", ":"))
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return json.dumps(value, separators=(",", ":"))
    raise ValueError("model output did not contain a JSON object")


def _error_class(error: Exception) -> str:
    text = str(error).lower()
    if "429" in text or "rate" in text:
        return "rate_limit"
    if "timeout" in text:
        return "timeout"
    if "model" in text and ("404" in text or "not found" in text):
        return "invalid_model"
    return "unavailable"


def _retry_after_seconds(error: Exception) -> int:
    return int(os.environ.get("HERMES_OPENAI_RATE_LIMIT_COOLDOWN_SECONDS", "3600")) if _error_class(error) == "rate_limit" else 300


def _provider_cooling_down(provider: str, model: str, attempts: list[ProviderAttempt], now: datetime) -> bool:
    latest = next(
        (
            attempt
            for attempt in reversed(attempts)
            if attempt.provider == provider and attempt.model == model
        ),
        None,
    )
    if latest is None or latest.status == "success" or latest.retry_after_seconds <= 0:
        return False
    return latest.attempted_at + timedelta(seconds=latest.retry_after_seconds) > now


def _combined_error(errors: list[tuple[str, str | None, Exception]]) -> str:
    details = []
    for provider, model, error in errors:
        label = provider if model is None else f"{provider}:{model}"
        details.append(f"{label}={error}")
    return "all Hermes reflection attempts failed: " + "; ".join(details)


__all__ = ["ModelRunResult", "ReflectionModelRunner"]
