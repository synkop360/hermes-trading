from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.reflect import mark_regime_start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reset the reflection regime marker to now. Run this after a structural "
            "strategy change (entry/exit indicator, direction) so reflection's score "
            "and consecutive_losses metrics stop being computed over trades from a "
            "strategy logic that no longer exists."
        )
    )
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args(argv)

    started_at = mark_regime_start(Path(args.state_dir))
    print(f"Reflection regime marker reset to {started_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
