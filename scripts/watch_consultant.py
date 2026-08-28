from __future__ import annotations

import argparse
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# Matches the observed real-world cadence of Railway's in-process trigger
# (HERMES_REFLECTION_INPROCESS_EVERY_CYCLES=20 loop cycles, ~15-20 min in
# practice) -- see run_reflection_once's docstring for why this must be an
# unconditional, fixed-cadence call rather than gated on accumulated evidence.
DEFAULT_INTERVAL_SECONDS = 900.0


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)


def run_reflection_once(state_dir: Path, project_dir: Path) -> subprocess.CompletedProcess[str]:
    """Unconditionally invokes reflect.py --hermes, exactly like Railway's
    in-process trigger (loop.py::ReflectionCycleSchedule) unconditionally
    calls run_hermes_reflection() every N loop cycles: it is
    run_hermes_reflection()'s own per-asset cooldown/eligibility gate
    (ReflectionPolicy) that decides whether anything actually changes, not
    this caller.

    An earlier version of this script pre-gated on its own evidence
    threshold (30 no-trade decisions) before calling reflection at all --
    that was a second, stricter gate stacked on top of the same internal
    cooldown, and made this consultant instance reflect roughly hourly
    instead of matching prod's ~15 minute cadence, confounding the
    baseline-vs-consultant comparison this script exists for. Don't
    reintroduce a pre-gate here; if reflection is too frequent, tune
    ReflectionPolicy's env vars (HERMES_REFLECT_COOLDOWN_HOURS etc.),
    the same knob prod uses, so both sides stay comparable.

    --hermes (not the deterministic in-process path) is required here: only
    the LLM-prompt path (_generate_asset_proposal) actually reads the
    market-analysis text reflect.py embeds in the prompt --
    generate_deterministic_asset_proposal ignores the prompt argument
    entirely, so it would never benefit from the consultant model.
    """
    return _run(
        ["uv", "run", "python", "-m", "hermes_trading.reflect", "--hermes", "--state-dir", str(state_dir)],
        cwd=project_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local-only reflection watcher for a second hermes-trading instance (the "
            "market-analysis 'consultant' comparison variant, run alongside the Railway "
            "baseline via HERMES_STATE_DIR). Unlike scripts/hermes_watch.py this never "
            "talks to Railway -- it calls reflect.py --hermes against a local --state-dir "
            "on a fixed cadence, matching how Railway's in-process trigger calls "
            "run_hermes_reflection() every N loop cycles regardless of accumulated "
            "evidence, so both sides are gated by the same internal cooldown logic and "
            "stay comparable."
        )
    )
    parser.add_argument("--state-dir", default="state-consultant")
    parser.add_argument(
        "--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS,
        help=(
            f"Repeat forever, sleeping this many seconds between calls (default "
            f"{DEFAULT_INTERVAL_SECONDS:.0f}s, matching prod's observed cadence). "
            "Pass 0 to call once and exit."
        ),
    )
    args = parser.parse_args(argv)
    interval_seconds = args.interval_seconds

    # This script's whole purpose is running the consultant variant, so it
    # enables market analysis by default -- explicitly set
    # HERMES_MARKET_ANALYSIS_ENABLED=false first if you ever want this
    # watcher to drive a plain (non-consultant) instance instead.
    os.environ.setdefault("HERMES_MARKET_ANALYSIS_ENABLED", "true")

    project_dir = Path(__file__).resolve().parents[1]
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        state_dir = project_dir / state_dir

    while True:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            run_reflection_once(state_dir, project_dir)
            print(f"[{stamp}] reflection cycle ran")
        except subprocess.CalledProcessError as error:
            print(f"[{stamp}] reflection cycle failed, will retry next interval: {error.stderr}")
        if interval_seconds <= 0:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
