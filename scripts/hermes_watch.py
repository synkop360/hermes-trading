from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(r"E:\_OB_VAULTS\Main Vault\Projects\Automatic Trading\hermes-trading")
STATE_DIR = PROJECT_DIR / "state"
MIRROR_DIR = Path(r"C:\Users\thoma\hermes-trading-config")
REMOTE_STATE_DIR = "/app/state"
STATE_FILES = [
    "goal.yaml",
    "strategy.yaml",
    "heartbeat.json",
    "decisions.jsonl",
    "trades.jsonl",
    "orders.jsonl",
    "hypotheses.jsonl",
    "reflection_state.json",
    "positions.json",
]
DEFAULT_NO_TRADE_DECISIONS = 30


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_DIR,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )


def _clean_railway_stdout(output: str) -> str:
    lines = output.splitlines()
    if lines and lines[0].startswith("Using SSH key from file "):
        return "\n".join(lines[1:])
    return output


def _looks_like_ssh_gateway_payload(content: str) -> bool:
    """True for Railway's own SSH-gateway discovery/handshake JSON. Seen in
    production: `railway ssh -- "<command>"` started returning this instead
    of executing the command (while Railway itself was down), and this
    cron -- run unattended every ~15-20 min -- silently overwrote every
    local state file with it on each cycle, since only an empty response was
    previously rejected. No real state file is ever a JSON object carrying
    both of these top-level keys together."""
    stripped = content.lstrip()
    return stripped.startswith('{"status":"ready"') and '"account"' in content[:200]


def _download_remote_file(name: str) -> bool:
    result = _run(["railway.cmd", "ssh", "--", f"cat {REMOTE_STATE_DIR}/{name} 2>/dev/null || true"])
    content = _clean_railway_stdout(result.stdout)
    if not content or _looks_like_ssh_gateway_payload(content):
        return False
    target = STATE_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def _upload_remote_file(local_path: Path, remote_path: str) -> None:
    encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
    _run(["railway.cmd", "ssh", "--", f"mkdir -p $(dirname {remote_path}) && base64 -d > {remote_path}"], input_text=encoded)


def _sync_to_mirror() -> None:
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    for name in STATE_FILES:
        source = STATE_DIR / name
        if source.exists():
            shutil.copy2(source, MIRROR_DIR / name)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_hypothesis_timestamp() -> datetime | None:
    hypotheses = _load_jsonl(STATE_DIR / "hypotheses.jsonl")
    for row in reversed(hypotheses):
        parsed = _parse_timestamp(row.get("timestamp"))
        if parsed:
            return parsed
    return None


def _count_after(path: Path, since: datetime | None, predicate=None) -> int:
    count = 0
    for row in _load_jsonl(path):
        timestamp = _parse_timestamp(row.get("timestamp"))
        if since and timestamp and timestamp <= since:
            continue
        if predicate and not predicate(row):
            continue
        count += 1
    return count


def _read_scalar_yaml_int(path: Path, key: str, default: int) -> int:
    if not path.exists():
        return default
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*(\d+)\s*$", path.read_text(encoding="utf-8"))
    return int(match.group(1)) if match else default


def _strategy_version() -> str:
    path = STATE_DIR / "strategy.yaml"
    if not path.exists():
        return "unknown"
    match = re.search(r"(?m)^\s*version\s*:\s*['\"]?([^'\"\r\n]+)", path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else "unknown"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _reflection_is_due() -> tuple[bool, str]:
    since = _last_hypothesis_timestamp()
    reflection_every = _read_scalar_yaml_int(STATE_DIR / "goal.yaml", "reflection_every", 5)
    no_trade_limit = int(
        os.environ.get(
            "HERMES_NO_TRADE_REFLECTION_DECISIONS",
            os.environ.get("HERMES_REFLECT_MIN_NEW_DECISIONS", str(DEFAULT_NO_TRADE_DECISIONS)),
        )
    )
    closed_trades = _count_after(STATE_DIR / "trades.jsonl", since)
    decisions = _count_after(STATE_DIR / "decisions.jsonl", since)
    closed_decisions = _count_after(STATE_DIR / "decisions.jsonl", since, lambda row: bool(row.get("trade_closed")))

    if closed_trades >= reflection_every:
        return True, f"{closed_trades} closed trades since last reflection"
    if decisions >= no_trade_limit and closed_decisions == 0:
        return True, f"{decisions} no-trade decisions since last reflection"
    return False, f"{closed_trades} closed trades, {decisions} decisions since last reflection"


def _run_reflection() -> None:
    _run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "hermes_trading.reflect",
            "--hermes",
            "--state-dir",
            str(STATE_DIR),
        ]
    )


def main() -> int:
    _load_dotenv(PROJECT_DIR / ".env")
    _load_dotenv(Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env")
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = [name for name in STATE_FILES if _download_remote_file(name)]
    _sync_to_mirror()

    before_version = _strategy_version()
    due, reason = _reflection_is_due()
    if due:
        _run_reflection()
        after_version = _strategy_version()
        _sync_to_mirror()
        _upload_remote_file(STATE_DIR / "strategy.yaml", f"{REMOTE_STATE_DIR}/strategy.yaml")
        _upload_remote_file(STATE_DIR / "hypotheses.jsonl", f"{REMOTE_STATE_DIR}/hypotheses.jsonl")
        archived = STATE_DIR / "history" / f"v{int(before_version):04d}.yaml"
        if archived.exists():
            _upload_remote_file(archived, f"{REMOTE_STATE_DIR}/history/{archived.name}")
        print(
            f"Hermes watch ok: reflection applied {before_version}->{after_version}; "
            f"reason={reason}; downloaded={len(downloaded)} files"
        )
    else:
        print(f"Hermes watch ok: no reflection due; strategy={before_version}; reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
