from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.run import ensure_state_files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    ensure_state_files(state_dir)

    now = datetime.now(UTC).isoformat()
    (state_dir / "qualification-training-started-at.txt").write_text(now + "\n", encoding="utf-8")
    for name in ("qualification-evidence.json", "qualification-replay.json"):
        path = state_dir / name
        if path.exists():
            path.unlink()

    print(f"qualification reset: training_started_at={now}")
    print("closed positions and virtual wallet P&L before this timestamp no longer count toward qualification")


if __name__ == "__main__":
    main()
