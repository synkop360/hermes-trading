from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.adapters.execution import ExecutionSafetyError, resolve_execution_settings


def main() -> int:
    try:
        settings = resolve_execution_settings()
    except ExecutionSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"mode={settings.mode} executor={settings.executor} validate_only={settings.validate_only}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
