"""Recette entrypoint: initialize the paper pilot on an empty volume and serve it.

Deployed on the Railway ``recette`` environment as
``python -m scripts.recette_service`` (see ``docs/RECETTE.md``). The service:

* refuses, by variable name only, any ``EXCHANGE_*``, ``HERMES_CLOUD_CHECKPOINT_*``
  or ``HERMES_DISCORD_*`` variable (even an empty one) -- values are never read
  or printed;
* forces the paper posture (paper mode, real orders refused if set, no
  notification delivery) without ever defining or modifying the four risk
  guardrail variables;
* initializes the eight-account ``pilot_paper`` bootstrap in ``<volume>/pilot``
  when the volume is empty and stamps the volume with a provenance/version
  marker, so a restart resumes only from a volume the recette service itself
  identified (the volume root is a mount point: it is never removed);
* serves bounded paper sessions in a loop: shared public-market samples,
  deterministic ``reflect --fallback`` reflection without any model, no
  credentials and no private network, separated by one interval. A ``STOP``
  file in ``<volume>/pilot`` ends the service cleanly.

Local development keeps the ``pilot_paper`` ``.test-tmp*`` confinement: the
volume-tree relaxation only applies when ``HERMES_RECETTE=true`` is set, which
is the deployment's own opt-in.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hermes_trading import pilot_paper

RECETTE_EXPERIMENT_ID = "pilot-paper-recette"
MARKER_NAME = "recette_provenance.json"
MARKER_SCHEMA_VERSION = 1
RECETTE_KIND = "hermes_recette_volume"
SERVICE_NAME = "scripts.recette_service"
PILOT_DIRNAME = "pilot"
# A fresh ext4 volume may carry an empty lost+found directory: it is tolerated.
TOLERATED_EMPTY_ENTRIES = frozenset({"lost+found"})
FORBIDDEN_ENV_PREFIXES = ("EXCHANGE_", "HERMES_CLOUD_CHECKPOINT_", "HERMES_DISCORD_")
DEFAULT_ROOT = Path("/app/state")
DEFAULT_MAX_CYCLES = 24
DEFAULT_MAX_RUNTIME_SECONDS = 86400.0
DEFAULT_INTERVAL_SECONDS = 3600.0
DEFAULT_REFLECT_EVERY = 1
ENV_MAX_CYCLES = "HERMES_RECETTE_MAX_CYCLES"
ENV_MAX_RUNTIME_SECONDS = "HERMES_RECETTE_MAX_RUNTIME_SECONDS"
ENV_INTERVAL_SECONDS = "HERMES_RECETTE_INTERVAL_SECONDS"
ENV_REFLECT_EVERY = "HERMES_RECETTE_REFLECT_EVERY"
CLEAN_STOP_REASONS = {"max_cycles_reached", "max_runtime_reached", "stop_requested"}


class RecetteError(RuntimeError):
    """The recette service cannot start or continue safely."""


def is_recette_deployment(environ: dict[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get("HERMES_RECETTE", "").strip().lower() == "true"


def forbidden_variable_names(environ: dict[str, str] | None = None) -> list[str]:
    """Names of present forbidden variables; values are never read."""
    environ = os.environ if environ is None else environ
    return sorted(
        name for name in environ if name.startswith(FORBIDDEN_ENV_PREFIXES)
    )


def assert_paper_environment(environ: dict[str, str] | None = None) -> None:
    environ = os.environ if environ is None else environ
    mode = environ.get("HERMES_TRADING_MODE", "").strip().lower()
    if mode and mode != "paper":
        raise RecetteError("recette is paper only (HERMES_TRADING_MODE is not paper)")
    orders = environ.get("HERMES_TRADING_ALLOW_REAL_ORDERS", "").strip().lower()
    if orders and orders != "false":
        raise RecetteError("recette requires HERMES_TRADING_ALLOW_REAL_ORDERS=false")
    notify = environ.get("HERMES_NOTIFY_ENABLED", "").strip().lower()
    if notify == "true":
        raise RecetteError("recette refuses notification delivery (HERMES_NOTIFY_ENABLED)")


def assert_recette_environment(environ: dict[str, str] | None = None) -> None:
    assert_paper_environment(environ)
    names = forbidden_variable_names(environ)
    if names:
        raise RecetteError(
            "recette refuses forbidden variables (names only): " + ", ".join(names)
        )


def code_version() -> str:
    """Installed package version, informational only (never checked on resume)."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return "unknown"
    try:
        return version("hermes-trading")
    except PackageNotFoundError:
        return "unknown"


def marker_payload(now: datetime) -> dict:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "kind": RECETTE_KIND,
        "service": SERVICE_NAME,
        "experiment_id": RECETTE_EXPERIMENT_ID,
        "pilot_root": PILOT_DIRNAME,
        "code_version": code_version(),
        "created_at": now.astimezone(timezone.utc).isoformat(),
    }


def pilot_root(volume: Path) -> Path:
    return Path(volume).absolute() / PILOT_DIRNAME


def volume_is_empty(volume: Path) -> bool:
    """True when the volume holds nothing but tolerated empty entries."""
    volume = Path(volume)
    for entry in volume.iterdir():
        if (
            entry.name in TOLERATED_EMPTY_ENTRIES
            and entry.is_dir()
            and not entry.is_symlink()
        ):
            try:
                if not any(entry.iterdir()):
                    continue
            except PermissionError:
                # root-owned lost+found cannot be listed: never ours, never data.
                continue
        return False
    return True


def read_recette_marker(root: Path) -> dict | None:
    path = Path(root) / MARKER_NAME
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def assert_identified_root(root: Path) -> dict:
    """A non-empty volume is only resumed when this service identified it."""
    marker = read_recette_marker(root)
    if marker is None:
        raise RecetteError(
            "refusing non-empty volume of unknown origin; a recette volume must "
            f"carry its own {MARKER_NAME} marker"
        )
    if (
        marker.get("schema_version") != MARKER_SCHEMA_VERSION
        or marker.get("kind") != RECETTE_KIND
        or marker.get("service") != SERVICE_NAME
        or marker.get("experiment_id") != RECETTE_EXPERIMENT_ID
        or marker.get("pilot_root") != PILOT_DIRNAME
    ):
        raise RecetteError("recette volume marker is incompatible with this service")
    pilot = pilot_root(root)
    if pilot.is_symlink() or not (pilot / "manifest.json").is_file():
        raise RecetteError("recette volume marker has no pilot bootstrap beside it")
    return marker


def _check_link_free(root: Path) -> None:
    raw = Path(root).absolute()
    for candidate in (raw, *raw.parents):
        if candidate.is_symlink():
            raise RecetteError(f"recette root path contains a symlink: {candidate}")


def _write_marker(root: Path, data: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".recette_marker-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, root / MARKER_NAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def initialize_recette_volume(
    root: Path, *, environ: dict[str, str] | None = None, now: datetime | None = None
) -> dict:
    """Create the pilot bootstrap in an empty/absent volume, then stamp it.

    The volume root may be a mount point, so it is created if absent but never
    removed; the pilot lives in ``<volume>/pilot``. The marker is written after
    the bootstrap: a crash between the two leaves an unidentified volume that a
    restart refuses, never half-trusts.
    """
    recette = is_recette_deployment(environ)
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = Path(root).absolute()
    _check_link_free(root)
    if root.exists() and (not root.is_dir() or not volume_is_empty(root)):
        raise RecetteError(f"recette volume must be empty or absent: {root}")
    if not recette and not any(part.startswith(".test-tmp") for part in root.parts):
        # Same confinement as pilot_paper, checked before the volume is created.
        raise RecetteError("root must be inside a .test-tmp* directory (no HERMES_RECETTE opt-in)")
    root.mkdir(parents=True, exist_ok=True)
    try:
        receipt = pilot_paper.init_pilot_accounts(
            pilot_root(root), experiment_id=RECETTE_EXPERIMENT_ID, now=at,
            allow_recette_root=recette,
        )
    except ValueError as error:
        raise RecetteError(str(error)) from error
    marker = marker_payload(at)
    _write_marker(root, marker)
    return {"status": "initialized", "receipt": receipt, "marker": marker}


def prepare_recette_volume(
    root: Path, *, environ: dict[str, str] | None = None, now: datetime | None = None
) -> dict:
    """Validate the environment, then init or recognize the volume."""
    assert_recette_environment(environ)
    root = Path(root).absolute()
    _check_link_free(root)
    if root.exists() and root.is_dir() and not volume_is_empty(root):
        marker = assert_identified_root(root)
        return {"status": "recognized", "marker": marker}
    return initialize_recette_volume(root, environ=environ, now=now)


def resolve_bounds(
    environ: dict[str, str] | None = None,
    *,
    max_cycles: int | None = None,
    max_runtime_seconds: float | None = None,
    interval_seconds: float | None = None,
    reflect_every: int | None = None,
) -> dict:
    """Bounds for one bounded session, from arguments or environment defaults."""
    environ = os.environ if environ is None else environ

    def env_int(name: str, default: int) -> int:
        raw = environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return int(raw)
        except ValueError as error:
            raise RecetteError(f"{name} must be an integer") from error

    def env_float(name: str, default: float) -> float:
        raw = environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError as error:
            raise RecetteError(f"{name} must be numeric") from error

    bounds = {
        "max_cycles": (
            max_cycles if max_cycles is not None
            else env_int(ENV_MAX_CYCLES, DEFAULT_MAX_CYCLES)
        ),
        "max_runtime_seconds": (
            max_runtime_seconds if max_runtime_seconds is not None
            else env_float(ENV_MAX_RUNTIME_SECONDS, DEFAULT_MAX_RUNTIME_SECONDS)
        ),
        "interval_seconds": (
            interval_seconds if interval_seconds is not None
            else env_float(ENV_INTERVAL_SECONDS, DEFAULT_INTERVAL_SECONDS)
        ),
        "reflect_every": (
            reflect_every if reflect_every is not None
            else env_int(ENV_REFLECT_EVERY, DEFAULT_REFLECT_EVERY)
        ),
    }
    try:
        pilot_paper._check_session_bounds(**bounds)
    except ValueError as error:
        raise RecetteError(str(error)) from error
    return bounds


def _default_runner(root, *, environ, bounds, allow_recette_root):
    return pilot_paper.run_pilot_session(
        root, environ=environ, allow_recette_root=allow_recette_root, **bounds
    )


def _emit(stream, record: dict) -> None:
    print(json.dumps(record, sort_keys=True), file=stream, flush=True)


async def serve(
    root,
    *,
    environ: dict[str, str] | None = None,
    max_cycles: int | None = None,
    max_runtime_seconds: float | None = None,
    interval_seconds: float | None = None,
    reflect_every: int | None = None,
    session_runner=None,
    sleep=None,
    emit=None,
    stream=None,
) -> dict:
    """Serve identified recette volumes with repeated bounded paper sessions."""
    environ = os.environ if environ is None else environ
    stream = sys.stdout if stream is None else stream
    emit = (lambda record: _emit(stream, record)) if emit is None else emit
    sleep = sleep or asyncio.sleep
    prepared = prepare_recette_volume(root, environ=environ)
    root = Path(root).absolute().resolve()
    recette = is_recette_deployment(environ)
    bounds = resolve_bounds(
        environ,
        max_cycles=max_cycles,
        max_runtime_seconds=max_runtime_seconds,
        interval_seconds=interval_seconds,
        reflect_every=reflect_every,
    )
    runner = session_runner or _default_runner
    pilot = pilot_root(root)
    emit({"kind": "recette_service_start", "root": str(root), "pilot_root": str(pilot),
          "volume": prepared["status"], "code_version": code_version(),
          "marker_code_version": prepared["marker"].get("code_version"),
          "bounds": bounds})
    passes: list[dict] = []

    def stopped() -> dict | None:
        if not (pilot / pilot_paper.STOP_FILE).exists():
            return None
        summary = {
            "kind": "recette_service_stop", "root": str(root),
            "stop_reason": "stop_requested", "passes": passes,
        }
        emit(summary)
        return summary

    while True:
        assert_identified_root(root)
        if (summary := stopped()) is not None:
            return summary
        report = await runner(
            pilot, environ=environ, bounds=bounds, allow_recette_root=recette
        )
        if report.get("stop_reason") not in CLEAN_STOP_REASONS:
            raise RecetteError(
                "recette session ended outside its bounds: "
                + str(report.get("stop_reason"))
            )
        record = {
            "kind": "recette_pass", "stop_reason": report["stop_reason"],
            "completed_cycles": report.get("completed_cycles"),
            "samples": report.get("samples"), "entries": report.get("entries"),
            "exits": report.get("exits"), "public_proof": report.get("public_proof"),
        }
        passes.append(record)
        emit(record)
        if (summary := stopped()) is not None:
            return summary
        # The last cycle of a session does not wait: keep one interval between
        # passes so two sessions never run back-to-back cycles.
        await sleep(bounds["interval_seconds"])


def main(argv=None, *, environ=None, session_runner=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--max-runtime-seconds", type=float, default=None)
    parser.add_argument("--interval-seconds", type=float, default=None)
    parser.add_argument("--reflect-every", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(serve(
            args.root, environ=environ, session_runner=session_runner,
            max_cycles=args.max_cycles,
            max_runtime_seconds=args.max_runtime_seconds,
            interval_seconds=args.interval_seconds,
            reflect_every=args.reflect_every,
        ))
    except (RecetteError, ValueError, PermissionError, pilot_paper.PilotSessionError) as error:
        print(f"refused: {error}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("interrupted: recette stopping", file=sys.stderr, flush=True)
        return 130
    return 0 if summary["stop_reason"] == "stop_requested" else 1


if __name__ == "__main__":
    raise SystemExit(main())
