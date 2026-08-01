from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import QualificationEvidence, QualificationPhase, QualificationService, QualificationSnapshot


@dataclass(frozen=True)
class QualificationReport:
    snapshot: QualificationSnapshot
    run_id: str
    markdown: str


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
        try:
            started_at = datetime.fromisoformat(configured_start).astimezone(UTC) if configured_start else current
        except ValueError:
            started_at = current
        strategy_bytes = (self.projector.state_dir / "strategy.yaml").read_bytes()
        strategy_hash = hashlib.sha256(strategy_bytes).hexdigest()
        profitability_path = self.projector.state_dir / "profitability.json"
        profitability = json.loads(profitability_path.read_text(encoding="utf-8")) if profitability_path.exists() else {}
        evidence = QualificationEvidence(
            training_started_at=started_at,
            closed_positions=len(self.repository.closed_positions()),
            strategy_hash=strategy_hash,
            approved_strategy_hash=strategy_hash,
            costs_complete=not profitability.get("missing_required_costs"),
            replay_without_lookahead=False,
            fees_and_slippage_charged=False,
            duplicate_candle_protected=False,
            risk_limits_exercised=False,
        )
        return self.run(requested_phase, evidence, now=current)
