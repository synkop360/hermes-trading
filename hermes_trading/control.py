from __future__ import annotations

from pathlib import Path

from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import QualificationPhase, QualificationService
from hermes_trading.qualification_runner import QualificationRunner


class ControlService:
    """Local command facade. Commands may request evidence, never self-promote."""

    def __init__(self, repository: StateRepository, state_dir: Path) -> None:
        self.repository = repository
        self.state_dir = state_dir

    def request_qualification(self, phase: QualificationPhase) -> dict[str, object]:
        report = QualificationRunner(
            self.repository, self.state_dir, QualificationService()
        ).run_from_state(phase)
        return {
            "requested_phase": phase.value,
            "promoted": False,
            "promotable": report.snapshot.promotable,
            "run_id": report.run_id,
        }
