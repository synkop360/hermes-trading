from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


class PromotionBlocked(RuntimeError):
    pass


class UnsafeRollback(RuntimeError):
    pass


@dataclass(frozen=True)
class StrategyVersion:
    strategy: dict[str, Any]
    candidate_hash: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PromotionApproval:
    candidate_hash: str
    approved_at: datetime
    expires_at: datetime
    qualification_run_id: str


class StrategyRegistry:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.strategy_path = state_dir / "strategy.yaml"
        self.candidate_path = state_dir / "candidate_strategy.yaml"
        self.history_dir = state_dir / "history"

    def load_champion(self) -> dict[str, Any]:
        return _load_yaml(self.strategy_path)

    def load_challenger(self) -> dict[str, Any]:
        return _load_yaml(self.candidate_path)

    def propose_challenger(
        self,
        strategy: dict[str, Any],
        *,
        based_on_version: str,
        provider: str | None,
        model: str | None,
        proposed_at: datetime | None = None,
    ) -> StrategyVersion:
        current = (proposed_at or datetime.now(UTC)).astimezone(UTC)
        candidate_hash = _strategy_hash(strategy)
        payload = {
            "candidate": {
                "based_on_version": based_on_version,
                "proposed_at": current.isoformat(),
                "provider": provider,
                "model": model,
                "hash": candidate_hash,
            },
            "strategy": strategy,
        }
        _atomic_write_yaml(self.candidate_path, payload)
        return StrategyVersion(strategy=strategy, candidate_hash=candidate_hash, metadata=payload["candidate"])

    def promote_challenger(
        self,
        approval: PromotionApproval,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        candidate_payload = self.load_challenger()
        candidate = candidate_payload.get("strategy")
        metadata = candidate_payload.get("candidate")
        if not isinstance(candidate, dict) or not isinstance(metadata, dict):
            raise PromotionBlocked("candidate strategy is missing or invalid")
        candidate_hash = str(metadata.get("hash") or _strategy_hash(candidate))
        if approval.candidate_hash != candidate_hash:
            raise PromotionBlocked("approval hash does not match candidate")
        if approval.approved_at.astimezone(UTC) > current:
            raise PromotionBlocked("approval is from the future")
        if approval.expires_at.astimezone(UTC) <= current:
            raise PromotionBlocked("approval is expired")
        if not approval.qualification_run_id:
            raise PromotionBlocked("qualification approval is required")

        champion = self.load_champion()
        self._archive(champion)
        _atomic_write_yaml(self.strategy_path, candidate)
        try:
            self.candidate_path.unlink()
        except FileNotFoundError:
            pass
        return candidate

    def defensive_rollback(
        self,
        *,
        reason: str,
        target: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        champion = self.load_champion()
        if _risk_value(target, "stop_loss_pct") > _risk_value(champion, "stop_loss_pct"):
            raise UnsafeRollback("rollback cannot raise stop_loss_pct")
        if _risk_value(target, "position_size_r") > _risk_value(champion, "position_size_r"):
            raise UnsafeRollback("rollback cannot raise position_size_r")
        self._archive(champion)
        _atomic_write_yaml(self.strategy_path, target)
        return target

    def _archive(self, strategy: dict[str, Any]) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        version = str(strategy.get("version", "1"))
        try:
            number = int(version)
            filename = f"v{number:04d}.yaml"
        except ValueError:
            filename = f"v{hashlib.sha256(version.encode('utf-8')).hexdigest()[:12]}.yaml"
        _atomic_write_yaml(self.history_dir / filename, strategy)


def _strategy_hash(strategy: dict[str, Any]) -> str:
    encoded = yaml.safe_dump(strategy, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _risk_value(strategy: dict[str, Any], name: str) -> float:
    try:
        return float(strategy.get(name, 0))
    except (TypeError, ValueError):
        return 0.0


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return payload


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "PromotionApproval",
    "PromotionBlocked",
    "StrategyRegistry",
    "StrategyVersion",
    "UnsafeRollback",
]
