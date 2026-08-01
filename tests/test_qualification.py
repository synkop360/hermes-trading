import unittest
from datetime import UTC, datetime, timedelta

from hermes_trading.qualification import (
    QualificationEvidence,
    QualificationPhase,
    QualificationService,
)


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


if __name__ == "__main__":
    unittest.main()
