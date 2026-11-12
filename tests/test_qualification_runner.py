import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hermes_trading.domain import Position, PositionStatus
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import QualificationPhase, QualificationService
from hermes_trading.qualification_runner import QualificationRunner
from hermes_trading.qualification_runner import QualificationCycleSchedule
from tests.test_qualification import NOW, evidence


def closed_position(position_id: str, realized_pnl: Decimal, closed_at: datetime | None = None) -> Position:
    closed = closed_at or NOW
    return Position(
        position_id=position_id,
        asset="BTC/USDT",
        status=PositionStatus.CLOSED,
        quantity=Decimal("0.00010"),
        entry_price=Decimal("65001.0"),
        stop_price=Decimal("64675.0"),
        strategy_version="83",
        opened_at=(closed - timedelta(hours=1)).isoformat(),
        closed_at=closed.isoformat(),
        realized_pnl=realized_pnl,
        fees=Decimal("0.01"),
    )


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

    def test_state_runner_sums_realized_pnl_from_closed_positions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            repository.save_position(closed_position("pos-1", Decimal("5")))
            repository.save_position(closed_position("pos-2", Decimal("-2")))
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            report = QualificationRunner(repository, state_dir, QualificationService()).run_from_state(
                QualificationPhase.VALIDATION_READY, now=NOW
            )
            repository.close()

        gate = report.snapshot.gate("overall_pnl")
        self.assertEqual(gate.status.value, "pass")
        self.assertIn("pnl=3", gate.observed)

    def test_state_runner_excludes_positions_closed_before_training_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            repository.save_position(closed_position("pos-1", Decimal("-40")))
            (state_dir / "qualification-training-started-at.txt").write_text(
                (NOW + timedelta(days=1)).isoformat() + "\n", encoding="utf-8"
            )
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            report = QualificationRunner(repository, state_dir, QualificationService()).run_from_state(
                QualificationPhase.VALIDATION_READY, now=NOW + timedelta(days=1)
            )
            repository.close()

        gate = report.snapshot.gate("overall_pnl")
        self.assertEqual(gate.status.value, "pass")
        self.assertIn("pnl=0", gate.observed)
        self.assertEqual(report.snapshot.gate("closed_positions").observed, "0")

    def test_state_runner_excludes_positions_closed_after_qualification_run_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            repository.save_position(closed_position("pos-1", Decimal("2"), NOW))
            repository.save_position(closed_position("pos-2", Decimal("30"), NOW + timedelta(days=1)))
            (state_dir / "qualification-training-started-at.txt").write_text(
                (NOW - timedelta(days=1)).isoformat() + "\n", encoding="utf-8"
            )
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            report = QualificationRunner(repository, state_dir, QualificationService()).run_from_state(
                QualificationPhase.VALIDATION_READY, now=NOW
            )
            repository.close()

        gate = report.snapshot.gate("overall_pnl")
        self.assertIn("pnl=2", gate.observed)
        self.assertEqual(report.snapshot.gate("closed_positions").observed, "1")

    def test_state_runner_fails_qualification_on_negative_overall_pnl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            repository = StateRepository.open(state_dir / "trading.db")
            repository.migrate()
            repository.save_position(closed_position("pos-1", Decimal("-60")))
            (state_dir / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            report = QualificationRunner(repository, state_dir, QualificationService()).run_from_state(
                QualificationPhase.VALIDATION_READY, now=NOW
            )
            repository.close()

        self.assertFalse(report.snapshot.valid)
        self.assertEqual(report.snapshot.gate("overall_pnl").status.value, "fail")

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
