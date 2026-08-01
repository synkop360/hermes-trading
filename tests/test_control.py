import tempfile
import unittest
from pathlib import Path

from hermes_trading.control import ControlService
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification import QualificationPhase


class ControlServiceTests(unittest.TestCase):
    def test_request_qualification_reports_status_without_promoting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir)
            (state / "strategy.yaml").write_text("version: '83'\n", encoding="utf-8")
            repository = StateRepository.open(state / "trading.db")
            repository.migrate()
            service = ControlService(repository, state)
            result = service.request_qualification(QualificationPhase.VALIDATION_READY)
            repository.close()

        self.assertEqual(result["requested_phase"], "validation_ready")
        self.assertFalse(result["promoted"])
