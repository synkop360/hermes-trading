from __future__ import annotations

import errno
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from hermes_trading.domain import json_value
from hermes_trading.persistence.repository import ProjectionItem, StateRepository


_WINDOWS_LOCK_TIMEOUT_SECONDS = 10.0
_WINDOWS_LOCK_INITIAL_RETRY_SECONDS = 0.01
_WINDOWS_LOCK_MAX_RETRY_SECONDS = 0.25
_WINDOWS_LOCK_CONTENTION_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", 36),
        36,  # Windows may report ERROR_LOCK_VIOLATION as this POSIX errno.
    }
)


class ArtifactProjector:
    """Projects canonical SQLite state into readable, replaceable audit files."""

    SNAPSHOT_NAMES = ("positions.json", "open_orders.json", "system_status.json")

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)

    def append_event(self, name: str, payload: Any) -> None:
        path = self._artifact_path(name, ".jsonl")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        normalized = json_value(payload)
        event_line = json.dumps(
            normalized,
            separators=(",", ":"),
            sort_keys=True,
        )
        event_key = _event_key(name, normalized)
        with _artifact_lock(path):
            if name=='decisions.jsonl' and isinstance(normalized,dict) and (self.state_dir/'trading.db').is_file():
                # A stale outbox reader may have loaded a quote before its
                # retirement. Check under the SAME artifact lock as replacement.
                import sqlite3
                from contextlib import closing
                from hermes_trading.archive_quote_state import quote_already_retired
                identity=normalized.get('decision_id');candle=normalized.get('candle_id')
                asset=normalized.get('asset');stamp=normalized.get('decided_at')
                if all(isinstance(v,str) and v for v in (identity,candle,asset,stamp)) and candle.startswith(asset+':') and candle.endswith(':'+stamp):
                    timeframe=candle[len(asset)+1:-(len(stamp)+1)]
                    if timeframe.startswith('paper-position:'):
                        with closing(sqlite3.connect((self.state_dir/'trading.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as canonical:
                            if (not canonical.execute('SELECT 1 FROM decisions WHERE decision_id=?',(identity,)).fetchone() and
                                    quote_already_retired(canonical,asset,timeframe,stamp)):
                                return
            if path.exists() and event_key is not None:
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if _event_key(name, existing) == event_key:
                        return
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

    def project_candle_outcome(
        self,
        repository: StateRepository,
        *,
        decision: Any,
        orders: tuple[Any, ...],
        fills: tuple[Any, ...],
        positions: tuple[Any, ...],
        closed_positions: tuple[Any, ...],
    ) -> None:
        """Project a committed candle outcome without participating in SQLite state."""
        self.append_event("decisions.jsonl", decision)
        for order in orders:
            self.append_event("orders.jsonl", order)
        for fill in fills:
            self.append_event("fills.jsonl", fill)
        for position in positions:
            self.append_event("positions.jsonl", position)
        for position in closed_positions:
            self.append_event("trades.jsonl", _trade_payload(position, decision))
        self.rebuild_all(repository)

    def reconcile(self, repository: StateRepository) -> None:
        """Resume human projections idempotently from canonical SQLite state."""
        records = repository.export_canonical_tables()
        for decision in records["decisions"]:
            self.append_event("decisions.jsonl", decision)
        for order in records["orders"]:
            self.append_event("orders.jsonl", order)
        for fill in records["order_fills"]:
            self.append_event("fills.jsonl", fill)
        for position in records["positions"]:
            self.append_event("positions.jsonl", position)
            if position["status"] == "closed":
                self.append_event("trades.jsonl", _trade_payload(position, repository.closed_position_decision(position)))
        self.rebuild_all(repository)

    def project_pending_item(self, repository: StateRepository, item: ProjectionItem) -> None:
        """Project one durable outbox item, leaving acknowledgement to the caller."""
        outcome = repository.projection_outcome(item)
        self.project_candle_outcome(
            repository,
            decision=outcome["decision"],
            orders=outcome["orders"],
            fills=outcome["fills"],
            positions=outcome["positions"],
            closed_positions=outcome["closed_positions"],
        )

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


def _trade_payload(position, decision):
    from hermes_trading.operational_exits import RISK_STOP_REASONS, RECOVERY_EXIT_REASONS
    payload=json_value(position)
    metadata=json_value(decision) if decision is not None else {}
    if metadata.get('reason') in RECOVERY_EXIT_REASONS:
        classification='operational_risk_stop' if metadata['reason'] in RISK_STOP_REASONS else 'operational_recovery'
        payload=dict(payload,evidence_classification=classification,
                     exit_reason=metadata['reason'],exit_decision_id=metadata['decision_id'])
    return payload


def _event_key(name: str, payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    if name == "decisions.jsonl":
        return _nonempty_key(payload, "decision_id")
    if name == "orders.jsonl":
        return _nonempty_key(payload, "order_id")
    if name == "fills.jsonl":
        return _nonempty_key(payload, "fill_id")
    if name == "risk_decisions.jsonl":
        return _nonempty_key(payload, "risk_decision_id")
    if name in {"positions.jsonl", "trades.jsonl"}:
        position_id = _nonempty_key(payload, "position_id")
        status = _nonempty_key(payload, "status")
        return f"{position_id}:{status}" if position_id is not None and status is not None else None
    return None


def _nonempty_key(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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

            _acquire_windows_lock(lock_handle, msvcrt)
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


def _acquire_windows_lock(lock_handle: Any, msvcrt: Any) -> None:
    """Acquire a one-byte Windows lock without msvcrt's coarse blocking retry.

    `LK_LOCK` may surface transient deadlock errors while several spawned
    processes contend for an artifact. Retrying `LK_NBLCK` ourselves keeps the
    wait bounded and lets interruption propagate instead of masking it.
    """
    deadline = time.monotonic() + _WINDOWS_LOCK_TIMEOUT_SECONDS
    delay = _WINDOWS_LOCK_INITIAL_RETRY_SECONDS
    while True:
        try:
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in _WINDOWS_LOCK_CONTENTION_ERRNOS:
                raise

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out acquiring artifact lock: {lock_handle.name}"
                ) from error
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _WINDOWS_LOCK_MAX_RETRY_SECONDS)
