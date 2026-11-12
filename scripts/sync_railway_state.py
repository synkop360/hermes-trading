from __future__ import annotations

import argparse
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REMOTE_STATE_DIR = "/app/state"
STATE_FILES = [
    "goal.yaml",
    "strategy.yaml",
    "heartbeat.json",
    "positions.json",
    "decisions.jsonl",
    "trades.jsonl",
    "orders.jsonl",
    "hypotheses.jsonl",
    "reflection_state.json",
    "qualification-training-started-at.txt",
]

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=True)


def _clean_railway_stdout(output: str) -> str:
    lines = output.splitlines()
    if lines and lines[0].startswith("Using SSH key from file "):
        return "\n".join(lines[1:])
    return output


def _looks_like_ssh_gateway_payload(content: str) -> bool:
    """True for Railway's own SSH-gateway discovery/handshake JSON (seen once
    in production: `railway ssh -- "<command>"` returned this instead of
    executing the command, and this function's caller silently overwrote
    goal.yaml/strategy.yaml/trades.jsonl/etc. with it, since it's non-empty
    and previously only empty responses were rejected). No real state file
    is ever a JSON object carrying both of these top-level keys together, so
    this is a safe, narrow signature rather than a general "looks like JSON"
    check that would also reject legitimate JSON state files."""
    stripped = content.lstrip()
    return stripped.startswith('{"status":"ready"') and '"account"' in content[:200]


def download_remote_file(state_dir: Path, name: str, *, runner: Runner = default_runner) -> bool:
    try:
        result = runner(["railway.cmd", "ssh", "--", f"cat {REMOTE_STATE_DIR}/{name} 2>/dev/null || true"])
    except subprocess.CalledProcessError as error:
        print(f"  skip {name}: railway ssh failed ({error})")
        return False
    content = _clean_railway_stdout(result.stdout)
    if not content:
        return False
    if _looks_like_ssh_gateway_payload(content):
        print(f"  skip {name}: railway ssh returned its own gateway payload instead of file content (server-side issue, not a real file)")
        return False
    target = state_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return True


def sync_once(
    state_dir: Path,
    *,
    runner: Runner = default_runner,
    files: list[str] = STATE_FILES,
) -> list[str]:
    return [name for name in files if download_remote_file(state_dir, name, runner=runner)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mirror hermes-trading state files from the Railway volume (/app/state) "
            "into a local directory, so a viewer bind-mounted on that directory stays fresh."
        )
    )
    parser.add_argument("--state-dir", default="state")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=0.0,
        help="Repeat forever, sleeping this many seconds between pulls. 0 (default) pulls once and exits.",
    )
    args = parser.parse_args(argv)

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    while True:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            synced = sync_once(state_dir)
        except Exception as error:  # noqa: BLE001 - a continuous sync loop must survive transient failures
            if args.interval_seconds <= 0:
                raise
            print(f"[{stamp}] sync cycle failed, will retry next interval: {error}")
            time.sleep(args.interval_seconds)
            continue
        print(f"[{stamp}] synced {len(synced)}/{len(STATE_FILES)} state files from Railway: {', '.join(synced) or 'none'}")
        if args.interval_seconds <= 0:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
