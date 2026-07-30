from __future__ import annotations

import errno
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
        event_line = json.dumps(
            json_value(payload),
            separators=(",", ":"),
            sort_keys=True,
        )
        with _artifact_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(event_line)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

    def write_snapshot(self, name: str, payload: Any) -> None:
        path = self._artifact_path(name, ".json")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.state_dir,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(json_value(payload), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            with _artifact_lock(path):
                os.replace(temporary_path, path)
                _fsync_directory(self.state_dir)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
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


def _fsync_directory(directory: Path) -> bool:
    """Sync a replaced directory entry where the operating system supports it.

    Windows does not expose directory handles through ``os.open``. Other
    platforms may explicitly report directory fsync as unsupported; those
    cases return False. Unexpected open or fsync failures remain visible.
    """
    if os.name == "nt":
        return False

    unsupported_errors = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        getattr(errno, "ENOSYS", errno.EINVAL),
    }
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
    except OSError as error:
        if error.errno in unsupported_errors:
            return False
        raise

    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno in unsupported_errors:
                return False
            raise
    finally:
        os.close(descriptor)
    return True


@contextmanager
def _artifact_lock(artifact_path: Path) -> Iterator[None]:
    lock_path = artifact_path.with_suffix(artifact_path.suffix + ".lock")
    with lock_path.open("a+b") as lock_handle:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"\0")
            lock_handle.flush()
        lock_handle.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
