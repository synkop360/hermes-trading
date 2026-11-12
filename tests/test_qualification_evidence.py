import json
from pathlib import Path
import tempfile
import unittest

from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification_evidence import QualificationEvidenceCollector


class QualificationEvidenceCollectorTests(unittest.TestCase):
    def test_missing_evidence_is_reported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checks = QualificationEvidenceCollector(Path(temp_dir)).collect()

        self.assertEqual(checks["replay_without_lookahead"].status.value, "missing")
        self.assertEqual(checks["fees_and_slippage_charged"].status.value, "missing")
        self.assertEqual(checks["duplicate_candle_protected"].status.value, "missing")
        self.assertEqual(checks["risk_limits_exercised"].status.value, "missing")

    def test_file_evidence_can_mark_a_gate_passed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "qualification-evidence.json").write_text(
                json.dumps({
                    "replay_without_lookahead": {
                        "status": "pass",
                        "observed": "replay-2026-08-01",
                        "required": "no lookahead detected",
                        "reason": "offline replay completed without future candles",
                    }
                }),
                encoding="utf-8",
            )
            checks = QualificationEvidenceCollector(state).collect()

        self.assertEqual(checks["replay_without_lookahead"].status.value, "pass")
        self.assertEqual(checks["replay_without_lookahead"].observed, "replay-2026-08-01")

    def test_durable_evidence_only_replaces_missing_json_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            repository = StateRepository.open(state / "trading.db")
            try:
                repository.migrate()
                repository.open_incident("qualification:duplicate-candle:1", "duplicate_candle_protected", severity="info")
                checks = QualificationEvidenceCollector(state, repository=repository).collect()
            finally:
                repository.close()

        self.assertEqual(checks["duplicate_candle_protected"].status.value, "pass")
        self.assertEqual(checks["duplicate_candle_protected"].observed, "1")
        self.assertEqual(checks["risk_limits_exercised"].status.value, "missing")

    def test_cost_ledger_marks_fees_and_slippage_charged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "cost_events.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({
                            "category": "kraken_fee",
                            "amount": "0.01",
                            "currency": "USD",
                            "quality": "actual",
                            "source": "kraken",
                            "occurred_at": "2026-08-01T12:00:00+00:00",
                            "period_start": None,
                            "period_end": None,
                            "metadata": {"order_id": "order-1"},
                        }),
                        json.dumps({
                            "category": "slippage",
                            "amount": "0.10",
                            "currency": "USD",
                            "quality": "actual",
                            "source": "kraken_fill_vs_decision",
                            "occurred_at": "2026-08-01T12:00:01+00:00",
                            "period_start": None,
                            "period_end": None,
                            "metadata": {"order_id": "order-1"},
                        }),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            checks = QualificationEvidenceCollector(state).collect()

        self.assertEqual(checks["fees_and_slippage_charged"].status.value, "pass")
        self.assertEqual(checks["fees_and_slippage_charged"].observed, "kraken_fee=0.01 slippage=0.10")

    def test_replay_report_marks_no_lookahead_passed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "qualification-replay.json").write_text(
                json.dumps({
                    "run_id": "replay-2026-08-01",
                    "status": "pass",
                    "candles_checked": 120,
                    "lookahead_violations": 0,
                }),
                encoding="utf-8",
            )

            checks = QualificationEvidenceCollector(state).collect()

        self.assertEqual(checks["replay_without_lookahead"].status.value, "pass")
        self.assertEqual(
            checks["replay_without_lookahead"].observed,
            "replay-2026-08-01 candles=120 violations=0",
        )

    def test_replay_report_marks_lookahead_violations_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "qualification-replay.json").write_text(
                json.dumps({
                    "run_id": "replay-2026-08-01",
                    "status": "pass",
                    "candles_checked": 120,
                    "lookahead_violations": 2,
                }),
                encoding="utf-8",
            )

            checks = QualificationEvidenceCollector(state).collect()

        self.assertEqual(checks["replay_without_lookahead"].status.value, "fail")
        self.assertEqual(checks["replay_without_lookahead"].reason, "replay detected lookahead violations")


if __name__ == "__main__":
    unittest.main()
