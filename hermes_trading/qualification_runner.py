from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import (
    DEFAULT_STARTING_WALLET_BALANCE,
    QualificationEvidence,
    QualificationPhase,
    QualificationService,
    QualificationSnapshot,
)
from hermes_trading.qualification_evidence import QualificationEvidenceCollector


@dataclass(frozen=True)
class QualificationReport:
    snapshot: QualificationSnapshot
    run_id: str
    markdown: str


@dataclass
class QualificationCycleSchedule:
    every_cycles: int
    cycles: int = 0

    def record_cycle(self) -> bool:
        if self.every_cycles <= 0:
            return False
        self.cycles += 1
        if self.cycles < self.every_cycles:
            return False
        self.cycles = 0
        return True


class QualificationRunner:
    def __init__(self, repository: StateRepository, state_dir: Path, service: QualificationService) -> None:
        self.repository, self.projector, self.service = repository, ArtifactProjector(state_dir), service

    def run(self, requested_phase: QualificationPhase, evidence: QualificationEvidence, *, now: datetime | None = None) -> QualificationReport:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        snapshot = self.service.evaluate(requested_phase, evidence, now=current)
        run_id = f"qualification-{uuid4()}"
        self.repository.record_qualification_snapshot(run_id, snapshot, current)
        markdown = "\n".join([f"Hermes Trading | Qualification {requested_phase.value}", *[f"{gate.status.value.upper()} | {gate.name} | {gate.reason}" for gate in snapshot.gates]])
        self.projector.write_snapshot("qualification-latest.json", {"run_id": run_id, "requested_phase": requested_phase.value, "promotable": snapshot.promotable, "gates": [{"name": gate.name, "status": gate.status.value, "reason": gate.reason} for gate in snapshot.gates]})
        (self.projector.state_dir / "qualification-latest.md").write_text(markdown + "\n", encoding="utf-8")
        return QualificationReport(snapshot, run_id, markdown)

    def run_from_state(self, requested_phase: QualificationPhase, *, now: datetime | None = None) -> QualificationReport:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        configured_start = os.getenv("HERMES_TRAINING_STARTED_AT")
        start_path = self.projector.state_dir / "qualification-training-started-at.txt"
        try:
            start_value = configured_start or (start_path.read_text(encoding="utf-8").strip() if start_path.exists() else current.isoformat())
            started_at = datetime.fromisoformat(start_value).astimezone(UTC)
        except ValueError:
            started_at = current
        if not configured_start and not start_path.exists():
            start_path.write_text(started_at.isoformat() + "\n", encoding="utf-8")
        strategy_bytes = (self.projector.state_dir / "strategy.yaml").read_bytes()
        strategy_hash = hashlib.sha256(strategy_bytes).hexdigest()
        profitability_path = self.projector.state_dir / "profitability.json"
        profitability = json.loads(profitability_path.read_text(encoding="utf-8")) if profitability_path.exists() else {}
        typed_evidence = QualificationEvidenceCollector(
            self.projector.state_dir, repository=self.repository
        ).collect()
        closed_positions = tuple(
            position
            for position in self.repository.closed_positions()
            if position.closed_at is not None
            and started_at <= datetime.fromisoformat(position.closed_at).astimezone(UTC) <= current
        )
        total_realized_pnl = sum((position.realized_pnl for position in closed_positions), Decimal("0"))
        evidence = QualificationEvidence(
            training_started_at=started_at,
            closed_positions=len(closed_positions),
            strategy_hash=strategy_hash,
            approved_strategy_hash=strategy_hash,
            costs_complete=not profitability.get("missing_required_costs"),
            replay_without_lookahead=typed_evidence["replay_without_lookahead"],
            fees_and_slippage_charged=typed_evidence["fees_and_slippage_charged"],
            duplicate_candle_protected=typed_evidence["duplicate_candle_protected"],
            risk_limits_exercised=typed_evidence["risk_limits_exercised"],
            starting_wallet_balance=resolve_starting_wallet_balance(),
            total_realized_pnl=total_realized_pnl,
        )
        return self.run(requested_phase, evidence, now=current)


def resolve_starting_wallet_balance(env: Mapping[str, str] | None = None) -> Decimal:
    values = env if env is not None else os.environ
    raw = values.get("HERMES_QUALIFICATION_STARTING_WALLET_BALANCE")
    if not raw:
        return DEFAULT_STARTING_WALLET_BALANCE
    try:
        return Decimal(raw)
    except InvalidOperation:
        return DEFAULT_STARTING_WALLET_BALANCE


def compute_virtual_wallet_balance(repository: StateRepository, env: Mapping[str, str] | None = None) -> Decimal:
    closed_positions = repository.closed_positions()
    total_realized_pnl = sum((position.realized_pnl for position in closed_positions), Decimal("0"))
    return resolve_starting_wallet_balance(env) + total_realized_pnl
