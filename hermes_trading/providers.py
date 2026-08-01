from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    free: bool
    source: str


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    model: str
    status: str
    attempted_at: datetime
    retry_after_seconds: int = 0
    latency_ms: int | None = None


class ProviderAttemptStore(Protocol):
    def provider_attempts(self) -> Iterable[ProviderAttempt]: ...

    def record_provider_attempt(self, attempt: ProviderAttempt) -> None: ...


@dataclass(frozen=True)
class HermesCatalog:
    candidates: tuple[ModelCandidate, ...]

    @classmethod
    def load(cls, path: Path) -> HermesCatalog:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(())
        return cls(tuple(_catalog_candidates(payload)))


class ProviderRouter:
    def __init__(self, store: ProviderAttemptStore, catalog: HermesCatalog) -> None:
        self.store = store
        self.catalog = catalog

    def candidates(self, *, now: datetime | None = None) -> list[ModelCandidate]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        attempts = list(self.store.provider_attempts())
        eligible = [candidate for candidate in self.catalog.candidates if candidate.provider != "openai" and candidate.free and not self._cooling_down(candidate, attempts, current)]
        return sorted(eligible, key=lambda candidate: self._rank(candidate, attempts))

    def record_success(self, candidate: ModelCandidate, *, now: datetime | None = None, latency_ms: int | None = None) -> None:
        self.store.record_provider_attempt(ProviderAttempt(candidate.provider, candidate.model, "success", (now or datetime.now(UTC)).astimezone(UTC), latency_ms=latency_ms))

    def record_failure(self, candidate: ModelCandidate, *, error_class: str, retry_after_seconds: int = 300, now: datetime | None = None) -> None:
        self.store.record_provider_attempt(ProviderAttempt(candidate.provider, candidate.model, error_class, (now or datetime.now(UTC)).astimezone(UTC), max(0, retry_after_seconds)))

    @staticmethod
    def _cooling_down(candidate: ModelCandidate, attempts: list[ProviderAttempt], now: datetime) -> bool:
        latest = next((item for item in reversed(attempts) if item.provider == candidate.provider and item.model == candidate.model), None)
        return latest is not None and latest.status != "success" and latest.retry_after_seconds > 0 and latest.attempted_at + timedelta(seconds=latest.retry_after_seconds) > now

    @staticmethod
    def _rank(candidate: ModelCandidate, attempts: list[ProviderAttempt]) -> tuple[Any, ...]:
        matching = [item for item in attempts if item.provider == candidate.provider and item.model == candidate.model]
        successes = [item for item in matching if item.status == "success"]
        last_success = max((item.attempted_at for item in successes), default=datetime.min.replace(tzinfo=UTC))
        latency = min((item.latency_ms for item in successes if item.latency_ms is not None), default=10**12)
        return (0 if candidate.provider == "nous" else 1, -last_success.timestamp(), latency, candidate.provider, candidate.model)


def _catalog_candidates(payload: object) -> list[ModelCandidate]:
    rows: list[ModelCandidate] = []
    for provider, models in _provider_models(payload):
        for model in models:
            model_id, metadata = _model_details(model)
            if model_id:
                rows.append(ModelCandidate(provider, model_id, _is_explicitly_free(model_id, metadata), "cache"))
    return rows


def _provider_models(payload: object) -> Iterable[tuple[str, list[object]]]:
    if isinstance(payload, dict):
        providers = payload.get("providers", payload)
        if isinstance(providers, dict):
            for provider, entry in providers.items():
                if isinstance(entry, dict):
                    models = entry.get("models", [])
                else:
                    models = entry
                if isinstance(provider, str) and isinstance(models, list):
                    yield provider, models
        elif isinstance(providers, list):
            for entry in providers:
                if isinstance(entry, dict) and isinstance(entry.get("provider"), str) and isinstance(entry.get("models"), list):
                    yield entry["provider"], entry["models"]


def _model_details(model: object) -> tuple[str, dict[str, Any]]:
    if isinstance(model, str):
        return model, {}
    if isinstance(model, dict):
        model_id = model.get("id", model.get("model", model.get("name", "")))
        return (str(model_id) if model_id else ""), model
    return "", {}


def _is_explicitly_free(model: str, metadata: dict[str, Any]) -> bool:
    if model.endswith(":free") or metadata.get("free") is True:
        return True
    try:
        return Decimal(str(metadata["input_cost"])) == 0 and Decimal(str(metadata["output_cost"])) == 0
    except (InvalidOperation, KeyError, ValueError):
        return False


__all__ = ["HermesCatalog", "ModelCandidate", "ProviderAttempt", "ProviderRouter"]
