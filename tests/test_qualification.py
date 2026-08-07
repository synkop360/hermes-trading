import unittest
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hermes_trading.qualification import (
    EvidenceCheck,
    QualificationEvidence,
    QualificationPhase,
    QualificationService,
)
from hermes_trading.persistence.repository import StateRepository


NOW = datetime(2026, 8, 1, tzinfo=UTC)


def evidence(**overrides):
    values = {
        "training_started_at": NOW - timedelta(days=14),
        "closed_positions": 50,
        "strategy_hash": "strategy-83",
        "approved_strategy_hash": "strategy-83",
        "costs_complete": True,
        "replay_without_lookahead": True,
        "fees_and_slippage_charged": True,
        "duplicate_candle_protected": True,
        "risk_limits_exercised": True,
        "starting_wallet_balance": Decimal("50"),
        "total_realized_pnl": Decimal("10"),
    }
    values.update(overrides)
    return QualificationEvidence(**values)


class QualificationServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = QualificationService()

    def test_validation_ready_requires_fourteen_days_and_fifty_closed_positions(self):
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY,
            evidence(training_started_at=NOW - timedelta(days=13), closed_positions=80),
            now=NOW,
        )

        self.assertFalse(result.promotable)
        self.assertEqual(result.gate("training_duration").status.value, "fail")

    def test_changed_strategy_invalidates_previous_evidence(self):
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY,
            evidence(strategy_hash="strategy-84"),
            now=NOW,
        )

        self.assertEqual(result.gate("strategy_unchanged").status.value, "fail")

    def test_missing_typed_evidence_stays_visible_as_missing_gate(self):
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY,
            evidence(replay_without_lookahead=EvidenceCheck.missing("no replay campaign evidence recorded")),
            now=NOW,
        )

        gate = result.gate("replay_without_lookahead")
        self.assertEqual(gate.status.value, "missing")
        self.assertEqual(gate.observed, "missing")
        self.assertEqual(gate.reason, "no replay campaign evidence recorded")

    def test_missing_typed_evidence_blocks_validity_and_promotion(self):
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY,
            evidence(replay_without_lookahead=EvidenceCheck.missing("no replay campaign evidence recorded")),
            now=NOW,
        )

        self.assertFalse(result.valid)
        self.assertFalse(result.promotable)

    def test_negative_overall_pnl_fails_qualification(self):
        result = self.service.evaluate(
            QualificationPhase.VALIDATION_READY,
            evidence(total_realized_pnl=Decimal("-5")),
            now=NOW,
        )

        self.assertFalse(result.valid)
        self.assertFalse(result.promotable)
        gate = result.gate("overall_pnl")
        self.assertEqual(gate.status.value, "fail")

    def test_default_wallet_starts_at_fifty_dollars(self):
        self.assertEqual(QualificationEvidence.__dataclass_fields__["starting_wallet_balance"].default, Decimal("50"))

    def test_wallet_balance_combines_starting_balance_and_pnl(self):
        wallet = evidence(starting_wallet_balance=Decimal("50"), total_realized_pnl=Decimal("-12"))

        self.assertEqual(wallet.wallet_balance, Decimal("38"))

    def test_unknown_cost_blocks_live_but_not_training(self):
        training = self.service.evaluate(
            QualificationPhase.TRAINING, evidence(costs_complete=False), now=NOW
        )
        live = self.service.evaluate(
            QualificationPhase.LIVE_BTC, evidence(costs_complete=False), now=NOW
        )

        self.assertTrue(training.valid)
        self.assertFalse(live.promotable)
        self.assertEqual(live.gate("costs_complete").status.value, "fail")

    def test_snapshot_is_persisted_append_only_with_its_gates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = StateRepository.open(Path(temp_dir) / "trading.db")
            repository.migrate()
            snapshot = self.service.evaluate(QualificationPhase.VALIDATION_READY, evidence(), now=NOW)
            repository.record_qualification_snapshot("qualification-1", snapshot, NOW)
            repository.record_qualification_snapshot("qualification-2", snapshot, NOW)
            rows = repository.qualification_runs()
            repository.close()

        self.assertEqual([row["qualification_run_id"] for row in rows], ["qualification-1", "qualification-2"])
        self.assertEqual(rows[0]["gate_count"], 8)


if __name__ == "__main__":
    unittest.main()
