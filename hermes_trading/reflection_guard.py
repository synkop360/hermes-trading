from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ReflectionModelBlock:
    blocked: bool
    reason: str
    consecutive_failures: int


def reflection_model_entry_block(
    state_dir: Path,
    env: Mapping[str, str] | None = None,
) -> ReflectionModelBlock:
    values = os.environ if env is None else env
    limit = _nonnegative_int(values.get("HERMES_REFLECTION_MODEL_FAILURE_LIMIT"), 3)
    if limit <= 0:
        return ReflectionModelBlock(False, "reflection_model_guard_disabled", 0)

    consecutive = _consecutive_model_failure_cycles(state_dir / "hypotheses.jsonl")
    if consecutive < limit:
        return ReflectionModelBlock(False, "reflection_model_healthy", consecutive)
    return ReflectionModelBlock(
        True,
        f"reflection_model_blocked:{consecutive}_consecutive_model_failures",
        consecutive,
    )


def _consecutive_model_failure_cycles(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for record in reversed(_load_jsonl(path)):
        mode = str(record.get("mode", ""))
        if mode == "hermes_multi_asset_cycle":
            break
        if mode != "hermes_multi_asset_no_change":
            continue
        results = record.get("results")
        if (
            isinstance(results, list)
            and results
            and all(isinstance(item, dict) and item.get("status") == "model_failed" for item in results)
        ):
            count += 1
            continue
        break
    return count


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _nonnegative_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value if value not in {None, ""} else default)
    except ValueError:
        return default
    return max(0, parsed)


__all__ = ["ReflectionModelBlock", "reflection_model_entry_block"]
