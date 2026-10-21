"""Recover one known malformed hypothesis while preserving the original.

Standalone stdlib helper: remote-main is restricted to the paper container.
It never submits orders, calls a model, or changes strategy configuration.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import tempfile
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def repair_bytes(source, bad_sha256):
    lines = source.splitlines(keepends=True)
    output, details = [], []
    for index, line in enumerate(lines):
        if not line.strip():
            output.append(line)
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("hypothesis record must be an object")
        except (ValueError, UnicodeDecodeError) as exc:
            if sha(line) != bad_sha256 or index == len(lines) - 1:
                raise ValueError("unknown corruption or incomplete tail; repair aborted") from exc
            record = {"mode": "audit_quarantined_malformed_record", "source_line": index + 1,
                      "source_sha256": sha(line), "source_snapshot_sha256": sha(source),
                      "raw_base64": base64.b64encode(line).decode("ascii"),
                      "parse_error": str(exc), "recorded_at": datetime.now(timezone.utc).isoformat()}
            details.append({"line": index + 1, "source_sha256": sha(line)})
            line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
        output.append(line)
    if len(details) != 1:
        raise ValueError("expected exactly one known malformed record")
    repaired = b"".join(output)
    for line in repaired.splitlines():
        if line.strip():
            json.loads(line)
    return repaired, details[0]


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def apply_repair(path, *, expected_sha256, bad_sha256):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("target must be a regular file")
    original = path.read_bytes()
    if sha(original) != expected_sha256:
        raise ValueError("source changed; no replacement performed")
    repaired, detail = repair_bytes(original, bad_sha256)
    if shutil.disk_usage(path.parent).free < len(original) + len(repaired) + 1024 * 1024:
        raise ValueError("insufficient room for backup and replacement")
    backup = path.with_name(path.name + ".pre-repair-" + expected_sha256)
    with backup.open("xb") as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".hypothesis-repair-", dir=path.parent)
    with os.fdopen(fd, "wb") as handle:
        handle.write(repaired)
        handle.flush()
        os.fsync(handle.fileno())
    original_stat = path.stat()
    os.chmod(temporary, stat.S_IMODE(original_stat.st_mode))
    if os.name != "nt":
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
    if sha(path.read_bytes()) != expected_sha256:
        raise ValueError("source changed after staging; backup/temp retained, replacement aborted")
    os.replace(temporary, path)
    _sync_directory(path.parent)
    if sha(path.read_bytes()) != sha(repaired):
        raise RuntimeError("replacement verification failed; retained backup available")
    return {"before_sha256": expected_sha256, "after_sha256": sha(repaired),
            "backup": str(backup), "replaced_record": detail, "status": "repaired"}


def is_python_worker(args, executable, pid):
    return (pid != 1 and executable.startswith("python")
            and any(arg in {b"hermes_trading.colocated", b"hermes_trading.run"} for arg in args))


def _workers():
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            executable = Path(os.readlink(entry / "exe")).name
        except (FileNotFoundError, PermissionError):
            continue
        if is_python_worker(args, executable, int(entry.name)):
            found.append(int(entry.name))
        if b"hermes_trading.reflect" in args:
            raise RuntimeError("independent remote reflection writer is running; defer repair")
    return found


def _process_state(pid):
    for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
        if line.startswith("State:"):
            return line.split()[1]
    raise RuntimeError("cannot establish worker process state")


def _wait_stopped(pid):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        states = []
        for task in (Path("/proc") / str(pid) / "task").iterdir():
            for line in (task / "status").read_text().splitlines():
                if line.startswith("State:"):
                    states.append(line.split()[1])
        if states and all(state in {"T", "t"} for state in states):
            return
        time.sleep(.01)
    raise RuntimeError("worker did not confirm stopped state; abort repair")


def _open_log_fd(pid, target):
    for fd in (Path("/proc") / str(pid) / "fd").iterdir():
        try:
            destination = os.readlink(fd)
        except FileNotFoundError:
            continue
        if destination == str(target):
            return True
    return False


def coordinated_repair(target, workers, *, expected_sha256, bad_sha256):
    paused = []
    try:
        for pid in workers:
            if _process_state(pid) in {"T", "t"}:
                raise RuntimeError("worker was already stopped; refuse to take pause ownership")
            os.kill(pid, signal.SIGSTOP)
            paused.append(pid)
            _wait_stopped(pid)
        for pid in paused:
            if _open_log_fd(pid, target):
                raise RuntimeError("worker holds hypothesis log open; defer to an idle window")
        result = apply_repair(target, expected_sha256=expected_sha256, bad_sha256=bad_sha256)
        return {**result, "worker_pids": list(paused)}
    finally:
        for pid in reversed(paused):
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--bad-sha256", required=True)
    args = parser.parse_args()
    if os.name != "posix" or os.environ.get("HERMES_TRADING_MODE", "").lower() not in {"training", "paper", "train", "demo"}:
        raise RuntimeError("remote repair requires an explicitly configured paper container")
    target = Path("/app/state/hypotheses.jsonl")
    workers = _workers()
    if not workers:
        raise RuntimeError("no identifiable worker process; refuse uncoordinated repair")
    result = coordinated_repair(target, workers, expected_sha256=args.expected_sha256, bad_sha256=args.bad_sha256)
    print(json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
