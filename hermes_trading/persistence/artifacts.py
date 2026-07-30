from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hermes_trading.domain import json_value
from hermes_trading.persistence.repository import StateRepository


class ArtifactProjector:
    """Projects canonical SQLite state into readable, replaceable audit files."""

    SNAPSHOT_NAMES = ("positions.json", "open_orders.json", "system_status.json")

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)

    def append_event(self, name: str, payload: Any) -> None:
        path = self._artifact_path(name, ".jsonl")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            json.dump(json_value(payload), handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def write_snapshot(self, name: str, payload: Any) -> None:
        path = self._artifact_path(name, ".json")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as handle:
                json.dump(json_value(payload), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def rebuild_all(self, repository: StateRepository) -> None:
        for name, payload in self.snapshot_payloads(repository).items():
            self.write_snapshot(name, payload)

    def rebuild_missing(self, repository: StateRepository) -> None:
        for name, payload in self.snapshot_payloads(repository).items():
            if not (self.state_dir / name).exists():
                self.write_snapshot(name, payload)

    @staticmethod
    def snapshot_payloads(repository: StateRepository) -> dict[str, dict[str, Any]]:
        return {
            "positions.json": {"positions": repository.export_open_positions()},
            "open_orders.json": {"orders": repository.export_open_orders()},
            "system_status.json": repository.export_system_status(),
        }

    def _artifact_path(self, name: str, suffix: str) -> Path:
        path = Path(name)
        if path.name != name or path.suffix != suffix:
            raise ValueError(f"artifact name must be a flat {suffix} filename")
        return self.state_dir / path


__all__ = ["ArtifactProjector"]
