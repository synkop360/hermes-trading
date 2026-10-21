from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from hermes_trading.costs import CostLedger, hermes_usage_event
from hermes_trading.providers import ModelCandidate, ProviderAttempt, ProviderRouter
from hermes_trading.supervisor_budget import ModelDispatchDeferred, active_budget


@dataclass(frozen=True)
class ModelRunResult:
    output: str
    provider: str
    model: str | None


class ModelResponseCaptureError(RuntimeError):
    """The request may have completed but its response was not durably saved."""


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
        pinned_candidate: ModelCandidate | None = None,
        strict_pinned: bool = False,
        paid_calls_authorized: bool = False,
    ) -> None:
        if type(strict_pinned) is not bool or type(paid_calls_authorized) is not bool:
            raise ValueError("model execution controls must be literal booleans")
        if strict_pinned:
            if pinned_candidate is None or any(
                not isinstance(value, str) or not value.strip() or value.strip().lower() in {"auto", "hermes_auto"}
                for value in (pinned_candidate.provider, pinned_candidate.model)
            ):
                raise ValueError("strict execution requires an explicit provider and model")
            if type(pinned_candidate.free) is not bool:
                raise ValueError("strict execution requires explicit payment classification")
        self.router = router
        self.local_run = local_run
        self.openai_run = openai_run
        self.command_run = command_run
        self.timeout_seconds = timeout_seconds or int(os.environ.get("HERMES_REFLECTION_TIMEOUT_SECONDS", "60"))
        self.cost_ledger = cost_ledger
        self.pinned_candidate = pinned_candidate
        self.strict_pinned = strict_pinned
        self.paid_calls_authorized = paid_calls_authorized

    def run(self, prompt: str, *, now: datetime | None = None,
            response_capture: Callable[[dict], None] | None = None) -> ModelRunResult:
        if response_capture is not None and (not self.strict_pinned or not callable(response_capture)):
            raise ValueError("durable response capture requires strict pinned execution")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        errors: list[tuple[str, str | None, Exception]] = []

        if self.strict_pinned:
            candidate = self.pinned_candidate
            if not candidate.free and not self.paid_calls_authorized:
                raise PermissionError("paid pinned model calls require explicit authorization")
            if _provider_cooling_down(candidate.provider, candidate.model,
                                      list(self.router.store.provider_attempts()), current):
                raise RuntimeError("pinned model is cooling down")

        if self.pinned_candidate is not None:
            # A pinned model replaces both the "hermes_auto" opaque selector and
            # the free-pool rotation below with one fixed choice, so the same
            # model answers every reflection cycle instead of whichever free
            # candidate happened to be available/successful most recently.
            # Local/OpenAI fallback on failure is unchanged -- resilience is
            # preserved, only the "which free model" rotation is removed.
            candidate = self.pinned_candidate
            try:
                started = datetime.now(UTC)
                output = _json_output(self._hermes(prompt, candidate, response_capture=response_capture))
                self.router.record_success(candidate, now=current, latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000))
                return ModelRunResult(output, candidate.provider, candidate.model)
            except (ModelResponseCaptureError,ModelDispatchDeferred):
                raise
            except Exception as error:
                errors.append((candidate.provider, candidate.model, error))
                self.router.record_failure(
                    candidate,
                    error_class=_error_class(error),
                    retry_after_seconds=_retry_after_seconds(error),
                    now=current,
                )
                if self.strict_pinned:
                    raise RuntimeError(_combined_error(errors)) from error
        else:
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

    def _hermes(self, prompt: str, candidate: ModelCandidate | None = None, *, response_capture=None) -> str:
        command = ["hermes", "-z", prompt]
        descriptor, raw_usage_path = tempfile.mkstemp(prefix="hermes-usage-", suffix=".json")
        os.close(descriptor)
        usage_path = Path(raw_usage_path)
        command_options = {}
        if self.strict_pinned:
            python = os.environ.get('HERMES_PROPOSAL_PYTHON')
            if not python:
                hermes = shutil.which('hermes')
                adjacent = Path(hermes).parent / ('python.exe' if os.name=='nt' else 'python') if hermes else None
                python = str(adjacent) if adjacent is not None and adjacent.is_file() else sys.executable
            command = [python,'-m','hermes_trading.proposal_transport']
            command.append('--free-required' if candidate.free else '--paid-authorized')
            command_options = {'input':prompt,'cwd':str(Path(__file__).resolve().parent.parent)}
        command.extend(["--usage-file", str(usage_path)])
        if candidate is not None:
            command.extend(["--provider", candidate.provider, "-m", candidate.model])
        dispatched=False
        try:
            command_run = self.command_run or subprocess.run
            try:
                timeout=self.timeout_seconds
                budget=active_budget()
                if self.strict_pinned and budget is not None:timeout=budget.model_timeout(timeout)
                dispatched=True
                result = command_run(command, check=True, capture_output=True, text=True, timeout=timeout, **command_options)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                receipt = _transport_receipt(usage_path) if self.strict_pinned else None
                _capture_response(response_capture, candidate, error.stdout, error.stderr, process_error=str(error),transport_receipt=receipt)
                if self.strict_pinned and (isinstance(error,subprocess.TimeoutExpired) or error.returncode==75 or error.returncode<0):
                    raise ModelResponseCaptureError('proposal request remains unresolved; usage evidence retained at '+str(usage_path)) from error
                raise
            _capture_response(response_capture, candidate, result.stdout, result.stderr)
            return result.stdout
        finally:
            active_error = sys.exception()
            try:
                if dispatched:self._record_usage(candidate, usage_path)
            except Exception as error:
                if isinstance(active_error, ModelResponseCaptureError):
                    raise ModelResponseCaptureError(
                        f"response and usage accounting require reconciliation; evidence retained at {usage_path}: {error}"
                    ) from error
                raise RuntimeError(f"usage accounting failed; evidence retained at {usage_path}: {error}") from error
            try:
                # Uncertain requests retain their only surviving receipt.
                if not isinstance(active_error,ModelResponseCaptureError):usage_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                if isinstance(active_error,ModelDispatchDeferred):
                    raise ModelDispatchDeferred(
                        f"no model request dispatched; cleanup failed; empty usage artifact retained at {usage_path}: {error}"
                    ) from error
                if isinstance(active_error, ModelResponseCaptureError):
                    raise ModelResponseCaptureError(
                        f"response requires reconciliation; cleanup failed and evidence retained at {usage_path}: {error}"
                    ) from error
                raise

    def _record_usage(self, candidate: ModelCandidate | None, usage_path: Path) -> None:
        if self.cost_ledger is None or candidate is None:
            return
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            usage = {}
        valid = isinstance(usage, dict) and all(
            type(usage.get(key)) is int and usage[key] >= 0 for key in ("input_tokens", "output_tokens")
        )
        event = hermes_usage_event(provider=candidate.provider, model=candidate.model,
                                  free=candidate.free, usage=usage if isinstance(usage, dict) else {})
        if isinstance(usage,dict):
            event = replace(event,metadata={**event.metadata,**{key:usage[key] for key in ('pricing_verification','raw_response','raw_http_response_b64','raw_http_sha256') if key in usage}})
        if not valid:
            event = replace(event, amount=None, quality="missing",
                            metadata={**event.metadata, "usage_status": "missing_or_invalid"})
        self.cost_ledger.record(event)


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


def _transport_receipt(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value,dict):return {key:value[key] for key in ('pricing_verification','raw_response','raw_http_response_b64','raw_http_sha256') if key in value}
    except (ValueError,OSError):pass
    return None


def _capture_response(capture, candidate, stdout, stderr, *, process_error=None,transport_receipt=None):
    if capture is None:
        return
    def decoded(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    response = {"output": decoded(stdout), "stderr": decoded(stderr),
                "provider": candidate.provider, "model": candidate.model}
    if process_error is not None:
        response["process_error"] = process_error
    if transport_receipt is not None:response['transport_receipt']=transport_receipt
    try:
        capture(response)
    except Exception as error:
        raise ModelResponseCaptureError("model response requires reconciliation after capture failure") from error


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


__all__ = ["ModelRunResult", "ModelResponseCaptureError", "ReflectionModelRunner"]
