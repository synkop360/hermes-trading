"""Optional, controlled storage limits for a *local* alert demonstration.

Delivered by LAT-141 (lot 1, C1); contract in ``docs/RECETTE.md`` (section
« Démonstration locale des alertes »). With the default recette budget (the
whole volume), the seven-day threshold depends on capacity, occupation and
growth that nobody controls (LAT-140): reduced limits make it deterministic
without filling the volume. They are a demonstration tool, never a recette
setting:

* refused by the service when ``HERMES_RECETTE=true`` (the deployment), when
  the volume is not under a ``.test-tmp*`` tree, or when a transport other
  than the local outbox is injected;
* published as ``storage.limits_source: local_demo``, which the window check
  (``scripts.recette_evidence``) never accepts as recette evidence.

The file is a small JSON object with exactly ``schema_version`` (1),
``experiment_id`` (``pilot-paper-recette``), ``max_state_bytes`` and
``reserve_bytes``. The values follow ``paired_storage_admission`` (reserve of at
least two maximum packets, strictly below the budget) and the budget is bounded
by ``MAX_DEMO_STATE_BYTES`` (the 1 GiB recette volume).
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from hermes_trading.paired_packet_storage import MAX_PACKET_BYTES
from hermes_trading.paired_storage_admission import validate_storage_limits

EXPERIMENT_ID = "pilot-paper-recette"
KEYS = frozenset({"schema_version", "experiment_id", "max_state_bytes", "reserve_bytes"})
MAX_FILE_BYTES = 4096
MIN_RESERVE_BYTES = 2 * MAX_PACKET_BYTES
MAX_DEMO_STATE_BYTES = 1024 ** 3
LIMITS_SOURCE = "local_demo"
# Defaults of the demonstration: a 256 MiB budget, the 128 MiB minimum reserve.
DEFAULT_DEMO_LIMITS = {"schema_version": 1, "experiment_id": EXPERIMENT_ID,
                       "max_state_bytes": 2 * MIN_RESERVE_BYTES, "reserve_bytes": MIN_RESERVE_BYTES}


class StorageLimitsError(ValueError):
    """Demonstration storage limits are invalid or not allowed here."""


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise StorageLimitsError("storage limits repeat a key")
    return dict(pairs)


def _reject_constant(name):
    raise StorageLimitsError("storage limits must be finite numbers")


def validate_demo_limits(limits) -> dict:
    """Strict validation; returns a fresh dictionary of the four fields."""
    if not isinstance(limits, dict) or set(limits) != KEYS:
        raise StorageLimitsError(
            "storage limits require exactly schema_version, experiment_id, max_state_bytes, reserve_bytes")
    if type(limits["schema_version"]) is not int or limits["schema_version"] != 1:
        raise StorageLimitsError("storage limits require schema_version 1")
    if limits["experiment_id"] != EXPERIMENT_ID:
        raise StorageLimitsError("storage limits require the recette experiment")
    for key in ("max_state_bytes", "reserve_bytes"):
        if type(limits[key]) is not int:
            raise StorageLimitsError("storage limits require integer bytes")
    if not 0 < limits["max_state_bytes"] <= MAX_DEMO_STATE_BYTES:
        raise StorageLimitsError("demonstration budget must stay within 1 GiB")
    try:
        validate_storage_limits(limits, EXPERIMENT_ID)
    except ValueError as error:
        raise StorageLimitsError(str(error)) from error
    return {key: limits[key] for key in sorted(KEYS)}


def load_demo_limits(path: Path | str) -> dict:
    """Read one bounded, regular, link-free JSON file, then validate it."""
    path = Path(path)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
    except OSError as error:
        raise StorageLimitsError("storage limits file is unreadable or a link") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise StorageLimitsError("storage limits file must be a small regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(MAX_FILE_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > MAX_FILE_BYTES:
        raise StorageLimitsError("storage limits file must be a small regular file")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                          parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise StorageLimitsError("storage limits file is not JSON") from error
    return validate_demo_limits(data)


def assert_local_demonstration(root: Path | str, *, recette: bool, transport) -> None:
    """Demonstration limits stay local: no deployment, a scratch root, the outbox."""
    if recette:
        raise StorageLimitsError("demonstration storage limits are refused in the recette deployment")
    if not any(part.startswith(".test-tmp") for part in Path(root).absolute().parts):
        raise StorageLimitsError("demonstration storage limits require a .test-tmp* volume")
    if transport is not None:
        raise StorageLimitsError("demonstration storage limits use the local_outbox transport only")
