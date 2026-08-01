import tempfile
import unittest
from pathlib import Path

from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import QualificationPhase, QualificationService
from hermes_trading.qualification_runner import QualificationRunner
from tests.test_qualification import NOW, evidence


class QualificationRunnerTests(unittest.TestCase):
    def test_report_writes_human_and_machine_artifacts_without_promoting_phase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            report = QualificationRunner(repository, state_dir, QualificationService()).run(
                QualificationPhase.VALIDATION_READY, evidence(), now=NOW
            )
            repository.close()

            markdown = (state_dir / "qualification-latest.md").read_text(encoding="utf-8")
            machine_report_exists = (state_dir / "qualification-latest.json").exists()

        self.assertTrue(report.snapshot.promotable)
        self.assertIn("PASS | training_duration", markdown)
        self.assertTrue(machine_report_exists)
