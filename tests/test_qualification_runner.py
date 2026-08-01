import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import QualificationPhase, QualificationService
from hermes_trading.qualification_runner import QualificationRunner
from hermes_trading.qualification_runner import QualificationCycleSchedule
from tests.test_qualification import NOW, evidence


class QualificationRunnerTests(unittest.TestCase):
    def test_cycle_schedule_runs_only_on_configured_interval(self):
        schedule = QualificationCycleSchedule(3)

        self.assertFalse(schedule.record_cycle())
        self.assertFalse(schedule.record_cycle())
        self.assertTrue(schedule.record_cycle())
        self.assertFalse(schedule.record_cycle())
    def test_state_runner_persists_training_start_when_not_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            runner = QualificationRunner(repository, state_dir, QualificationService())
            runner.projector.state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            first = runner.run_from_state(QualificationPhase.VALIDATION_READY, now=NOW)
            second = runner.run_from_state(QualificationPhase.VALIDATION_READY, now=NOW + timedelta(days=1))
            repository.close()

        self.assertEqual(first.snapshot.gate("training_duration").observed, "0")
        self.assertEqual(second.snapshot.gate("training_duration").observed, "1")

    def test_state_runner_marks_missing_typed_evidence_as_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            report = QualificationRunner(repository, state_dir, QualificationService()).run_from_state(
                QualificationPhase.VALIDATION_READY, now=NOW
            )
            repository.close()

        self.assertEqual(report.snapshot.gate("replay_without_lookahead").status.value, "missing")
        self.assertEqual(report.snapshot.gate("fees_and_slippage").status.value, "missing")
        self.assertEqual(report.snapshot.gate("duplicate_candle_protection").status.value, "missing")
        self.assertEqual(report.snapshot.gate("risk_limits_exercised").status.value, "missing")

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
