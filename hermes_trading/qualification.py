from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class QualificationPhase(str, Enum):
    TRAINING = "training"
    VALIDATION_READY = "validation_ready"
    KRAKEN_VALIDATE_ONLY = "kraken_validate_only"
    MICRO_LIVE_BTC = "micro_live_btc"
    LIVE_BTC = "live_btc"
    LIVE_ETH = "live_eth"
    LIVE_XRP = "live_xrp"
    LIVE_SHIB = "live_shib"
    PAUSED = "paused"


class GateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"


@dataclass(frozen=True)
class GateResult:
    name: str
    status: GateStatus
    observed: str
    required: str
    reason: str


@dataclass(frozen=True)
class EvidenceCheck:
    status: GateStatus
    observed: str
    required: str
    reason: str

    @classmethod
    def passed(cls, observed: str = "true", required: str = "true", reason: str = "satisfied") -> EvidenceCheck:
        return cls(GateStatus.PASS, observed, required, reason)

    @classmethod
    def failed(cls, observed: str, required: str = "true", reason: str | None = None) -> EvidenceCheck:
        return cls(GateStatus.FAIL, observed, required, reason or f"requires {required}; observed {observed}")

    @classmethod
    def missing(cls, reason: str, required: str = "true") -> EvidenceCheck:
        return cls(GateStatus.MISSING, "missing", required, reason)


@dataclass(frozen=True)
class QualificationEvidence:
    training_started_at: datetime
    closed_positions: int
    strategy_hash: str
    approved_strategy_hash: str
    costs_complete: bool
    replay_without_lookahead: bool | EvidenceCheck
    fees_and_slippage_charged: bool | EvidenceCheck
    duplicate_candle_protected: bool | EvidenceCheck
    risk_limits_exercised: bool | EvidenceCheck


@dataclass(frozen=True)
class QualificationSnapshot:
    requested_phase: QualificationPhase
    gates: tuple[GateResult, ...]

    @property
    def valid(self) -> bool:
        return all(gate.status == GateStatus.PASS for gate in self.gates)

    @property
    def promotable(self) -> bool:
        return self.requested_phase != QualificationPhase.TRAINING and self.valid

    def gate(self, name: str) -> GateResult:
        return next(gate for gate in self.gates if gate.name == name)


class QualificationService:
    def evaluate(
        self,
        requested_phase: QualificationPhase,
        evidence: QualificationEvidence,
        *,
        now: datetime | None = None,
    ) -> QualificationSnapshot:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        elapsed_days = (current - evidence.training_started_at.astimezone(UTC)).days
        gates = [
            _gate("training_duration", elapsed_days >= 14, str(elapsed_days), ">= 14 days"),
            _gate("closed_positions", evidence.closed_positions >= 50, str(evidence.closed_positions), ">= 50"),
            _gate("strategy_unchanged", evidence.strategy_hash == evidence.approved_strategy_hash, evidence.strategy_hash, evidence.approved_strategy_hash),
            _evidence_gate("replay_without_lookahead", evidence.replay_without_lookahead),
            _evidence_gate("fees_and_slippage", evidence.fees_and_slippage_charged),
            _evidence_gate("duplicate_candle_protection", evidence.duplicate_candle_protected),
            _evidence_gate("risk_limits_exercised", evidence.risk_limits_exercised),
        ]
        if requested_phase not in {QualificationPhase.TRAINING, QualificationPhase.VALIDATION_READY}:
            gates.append(_gate("costs_complete", evidence.costs_complete, str(evidence.costs_complete), "true"))
        return QualificationSnapshot(requested_phase, tuple(gates))


def _gate(name: str, passed: bool, observed: str, required: str) -> GateResult:
    return GateResult(name, GateStatus.PASS if passed else GateStatus.FAIL, observed, required, "satisfied" if passed else f"requires {required}; observed {observed}")


def _evidence_gate(name: str, check: bool | EvidenceCheck) -> GateResult:
    if isinstance(check, EvidenceCheck):
        return GateResult(name, check.status, check.observed, check.required, check.reason)
    return _gate(name, check, str(check), "true")


__all__ = ["EvidenceCheck", "GateResult", "GateStatus", "QualificationEvidence", "QualificationPhase", "QualificationService", "QualificationSnapshot"]
