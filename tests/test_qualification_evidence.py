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


if __name__ == "__main__":
    unittest.main()
