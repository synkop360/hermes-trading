from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
