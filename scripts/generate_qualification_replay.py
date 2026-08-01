from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


JOURNALS = ("decisions.jsonl", "trades.jsonl", "orders.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Hermes qualification replay evidence from state journals")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args()

    generated_at = _parse_timestamp(args.generated_at) if args.generated_at else datetime.now(UTC)
    report = build_report(Path(args.state_dir), generated_at=generated_at)
    output_path = Path(args.state_dir) / "qualification-replay.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"qualification-replay status={report['status']} "
        f"candles_checked={report['candles_checked']} "
        f"lookahead_violations={report['lookahead_violations']} "
        f"path={output_path}"
    )


def build_report(state_dir: Path, *, generated_at: datetime) -> dict[str, Any]:
    events: list[tuple[str, int, dict[str, Any]]] = []
    violations: list[dict[str, Any]] = []
    decision_count = 0
    current = generated_at.astimezone(UTC)

    for journal in JOURNALS:
        for line_number, payload in _read_jsonl(state_dir / journal):
            timestamp_value = _event_timestamp_value(journal, payload)
            timestamp = _optional_timestamp(timestamp_value)
            asset = str(payload.get("asset", "unknown"))
            if journal == "decisions.jsonl":
                decision_count += 1
            if timestamp is None:
                violations.append(_violation(journal, line_number, asset, "missing_or_invalid_timestamp"))
                continue
            if timestamp > current:
                violations.append(_violation(journal, line_number, asset, "event_timestamp_after_report_time"))
            candle_close = _candle_close(payload.get("candle_id"))
            if candle_close is not None and timestamp < candle_close:
                violations.append(_violation(journal, line_number, asset, "decision_before_candle_close"))
            events.append((journal, line_number, payload))

    status = "pass" if decision_count > 0 and not violations else "fail"
    return {
        "run_id": f"replay-journal-scan-{uuid4()}",
        "status": status,
        "method": "journal_chronology_scan",
        "generated_at": current.isoformat(),
        "candles_checked": decision_count,
        "events_checked": len(events),
        "lookahead_violations": len(violations),
        "violations": violations[:50],
    }


def _read_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return []
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            rows.append((line_number, {"timestamp": None, "asset": "unknown"}))
            continue
        if isinstance(payload, dict):
            rows.append((line_number, payload))
    return rows


def _optional_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_timestamp(str(value))
    except ValueError:
        return None


def _event_timestamp_value(journal: str, payload: dict[str, Any]) -> object:
    if journal == "trades.jsonl":
        return payload.get("timestamp") or payload.get("closed_at") or payload.get("opened_at")
    if journal == "orders.jsonl":
        return payload.get("timestamp") or payload.get("updated_at") or payload.get("created_at")
    return payload.get("timestamp") or payload.get("decided_at") or payload.get("created_at")


def _candle_close(value: object) -> datetime | None:
    if not value:
        return None
    parts = str(value).rsplit(":", 1)
    if len(parts) != 2:
        return None
    return _optional_timestamp(parts[1])


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _violation(journal: str, line_number: int, asset: str, reason: str) -> dict[str, Any]:
    return {"journal": journal, "line": line_number, "asset": asset, "reason": reason}


if __name__ == "__main__":
    main()
