from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermes_trading.qualification import EvidenceCheck, GateStatus

if TYPE_CHECKING:
    from hermes_trading.persistence.repository import StateRepository


EVIDENCE_KEYS = {
    "replay_without_lookahead": "no replay campaign evidence recorded",
    "fees_and_slippage_charged": "no fee and slippage evidence recorded",
    "duplicate_candle_protected": "no duplicate candle protection evidence recorded",
    "risk_limits_exercised": "no risk limit exercise evidence recorded",
}


class QualificationEvidenceCollector:
    def __init__(self, state_dir: Path, repository: StateRepository | None = None) -> None:
        self._state_dir = state_dir
        self._repository = repository

    def collect(self) -> dict[str, EvidenceCheck]:
        evidence_path = self._state_dir / "qualification-evidence.json"
        raw = _read_json_object(evidence_path)
        checks = {
            key: _parse_check(raw.get(key), missing_reason)
            for key, missing_reason in EVIDENCE_KEYS.items()
        }
        if self._repository and checks["duplicate_candle_protected"].status == GateStatus.MISSING:
            count = self._repository.duplicate_candle_evidence_count()
            if count > 0:
                checks["duplicate_candle_protected"] = EvidenceCheck.passed(
                    str(count), ">= 1", "duplicate candle protection exercised"
                )
        if self._repository and checks["risk_limits_exercised"].status == GateStatus.MISSING:
            count = self._repository.risk_limit_evidence_count()
            if count > 0:
                checks["risk_limits_exercised"] = EvidenceCheck.passed(
                    str(count), ">= 1", "risk limit exercised"
                )
        return checks


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_check(value: Any, missing_reason: str) -> EvidenceCheck:
    if value is True:
        return EvidenceCheck.passed()
    if value is False:
        return EvidenceCheck.failed("false")
    if not isinstance(value, dict):
        return EvidenceCheck.missing(missing_reason)
    try:
        status = GateStatus(str(value["status"]))
    except (KeyError, ValueError):
        return EvidenceCheck.missing(missing_reason)
    observed = str(value.get("observed", status.value))
    required = str(value.get("required", "true"))
    reason = str(value.get("reason", "satisfied" if status == GateStatus.PASS else missing_reason))
    return EvidenceCheck(status, observed, required, reason)
