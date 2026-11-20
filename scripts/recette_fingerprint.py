"""Reproducible fingerprint of the sources the recette actually executes.

Delivered by LAT-141 (lot 1, C1); contract in ``docs/RECETTE.md`` (section
« Empreinte du code exécuté »). ``heartbeat.code_version`` reads the installed
package version, which is ``unknown`` in the image (the project is *virtual* in
``uv.lock``: it is never installed) and would be a static ``0.1.0`` otherwise.
It cannot tell which commit runs. The fingerprint can:

* ``hermes_trading`` -- every ``*.py`` of the imported package, with the rule of
  ``hermes_trading.paper_publication._release_hashes`` (relative POSIX path ->
  SHA-256 of the bytes), so this component is the digest of the pinned release
  map itself;
* ``scripts`` -- every ``*.py`` of the imported ``scripts`` package;
* ``dependencies`` -- ``pyproject.toml`` and ``uv.lock`` beside ``scripts/``
  (the image copies exactly these four inputs, see ``.dockerignore``).

A component digest is the SHA-256 of the canonical JSON of its
``{path: sha256}`` map; the fingerprint is the SHA-256 of the canonical JSON of
``{"algorithm", "components"}``. Only hexadecimal digests and file counts are
published: no absolute path, no environment value, no file content. The same
value is computed from a checkout (``--source``) or from a commit without a
checkout (``--git-rev``), so a report can be reconciled with the commit of a
pull request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ALGORITHM = "hermes-recette-runtime-v1"
SCHEMA_VERSION = 1
COMPONENTS = ("hermes_trading", "scripts", "dependencies")
PACKAGE_DIRS = {"hermes_trading": "hermes_trading", "scripts": "scripts"}
DEPENDENCY_FILES = ("pyproject.toml", "uv.lock")
HEX64 = re.compile(r"[0-9a-f]{64}")
SHORT_CHARS = 12
MAX_FILES = 10000
MAX_FILE_BYTES = 16 * 1024 * 1024


class FingerprintError(RuntimeError):
    """The sources cannot be fingerprinted reliably."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _excluded(relative: PurePosixPath) -> bool:
    # Test scratch trees never belong to a delivery.
    return any(part.startswith(".test-tmp") for part in relative.parts)


def _package_hashes(directory: Path) -> dict[str, str]:
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise FingerprintError("source package directory is missing")
    hashes: dict[str, str] = {}
    for path in sorted(directory.rglob("*.py")):
        relative = PurePosixPath(path.relative_to(directory).as_posix())
        if _excluded(relative):
            continue
        if path.is_symlink() or not path.is_file():
            raise FingerprintError("source file is not a regular file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise FingerprintError("source file exceeds the fingerprint size bound")
        hashes[relative.as_posix()] = _sha(path.read_bytes())
        if len(hashes) > MAX_FILES:
            raise FingerprintError("source tree exceeds the fingerprint file bound")
    if not hashes:
        raise FingerprintError("source package has no Python file")
    return hashes


def _dependency_hashes(directory: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in DEPENDENCY_FILES:
        path = Path(directory) / name
        if path.is_symlink() or not path.is_file():
            raise FingerprintError("dependency file is missing")
        hashes[name] = _sha(path.read_bytes())
    return hashes


def tree_hashes(source: Path | str) -> dict[str, dict[str, str]]:
    """Per-component ``{path: sha256}`` maps of one source tree (a checkout)."""
    source = Path(source)
    return {
        "hermes_trading": _package_hashes(source / PACKAGE_DIRS["hermes_trading"]),
        "scripts": _package_hashes(source / PACKAGE_DIRS["scripts"]),
        "dependencies": _dependency_hashes(source),
    }


def executed_hashes() -> dict[str, dict[str, str]]:
    """Maps of the sources *imported by this process* (not of a guessed root)."""
    import hermes_trading

    package = Path(hermes_trading.__file__).resolve().parent
    scripts_dir = Path(__file__).resolve().parent
    return {
        "hermes_trading": _package_hashes(package),
        "scripts": _package_hashes(scripts_dir),
        "dependencies": _dependency_hashes(scripts_dir.parent),
    }


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args], input=stdin, capture_output=True,
            check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise FingerprintError("git is unavailable: " + type(error).__name__) from error
    if completed.returncode != 0:
        raise FingerprintError("git cannot read the requested revision")
    return completed.stdout


def git_hashes(repo: Path | str, rev: str) -> dict[str, dict[str, str]]:
    """Per-component maps read from the objects of commit ``rev`` (no checkout)."""
    if not isinstance(rev, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", rev) \
            or rev.startswith("-"):
        raise FingerprintError("revision must be a plain commit name")
    repo = Path(repo)
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", rev, "--",
                   *PACKAGE_DIRS.values(), *DEPENDENCY_FILES)
    entries: list[tuple[str, str, str, str]] = []
    for row in listing.split(b"\0"):
        if not row:
            continue
        meta, _, name = row.partition(b"\t")
        mode, kind, obj = meta.decode("ascii").split(" ")
        entries.append((mode, kind, obj, name.decode("utf-8")))
    maps: dict[str, dict[str, str]] = {name: {} for name in COMPONENTS}
    wanted: list[tuple[str, str, str]] = []
    for mode, kind, obj, name in entries:
        path = PurePosixPath(name)
        if name in DEPENDENCY_FILES:
            component, relative = "dependencies", path
        else:
            top = path.parts[0]
            component = next((key for key, value in PACKAGE_DIRS.items() if value == top), None)
            if component is None or path.suffix != ".py":
                continue
            relative = PurePosixPath(*path.parts[1:])
            if _excluded(relative):
                continue
        if kind != "blob" or mode == "120000":
            raise FingerprintError("source file is not a regular file")
        wanted.append((component, relative.as_posix(), obj))
    if not wanted:
        raise FingerprintError("revision has no source")
    batch = _git(repo, "cat-file", "--batch", stdin="".join(f"{obj}\n" for _, _, obj in wanted).encode("ascii"))
    offset = 0
    for component, relative, obj in wanted:
        end = batch.index(b"\n", offset)
        header = batch[offset:end].decode("ascii").split(" ")
        if len(header) != 3 or header[0] != obj or header[1] != "blob":
            raise FingerprintError("git returned an unexpected object")
        size = int(header[2])
        start = end + 1
        maps[component][relative] = _sha(batch[start:start + size])
        offset = start + size + 1
    for name in COMPONENTS:
        if not maps[name]:
            raise FingerprintError("revision lacks a source component")
    if set(maps["dependencies"]) != set(DEPENDENCY_FILES):
        raise FingerprintError("dependency file is missing")
    return maps


def summarize(maps: dict[str, dict[str, str]]) -> dict:
    """Public fingerprint of per-component maps: digests and counts only."""
    if set(maps) != set(COMPONENTS):
        raise FingerprintError("fingerprint requires every component")
    components = {
        name: {"files": len(maps[name]), "sha256": _sha(_canonical(dict(sorted(maps[name].items()))))}
        for name in COMPONENTS
    }
    digest = _sha(_canonical({"algorithm": ALGORITHM,
                              "components": {name: value["sha256"] for name, value in components.items()}}))
    return {"schema_version": SCHEMA_VERSION, "status": "identified", "algorithm": ALGORITHM,
            "fingerprint": digest, "short": digest[:SHORT_CHARS], "components": components}


def unavailable(reason: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": "unavailable", "algorithm": ALGORITHM,
            "reason": reason}


def runtime_fingerprint() -> dict:
    """Fingerprint of the executed sources; never raises (``unavailable``)."""
    try:
        return summarize(executed_hashes())
    except (FingerprintError, OSError, ValueError) as error:
        return unavailable("fingerprint_unavailable_" + type(error).__name__)


def is_valid_summary(value) -> bool:
    """Strict shape check, so only digests and counts are ever republished."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION \
            or value.get("algorithm") != ALGORITHM:
        return False
    if value.get("status") == "unavailable":
        return set(value) == {"schema_version", "status", "algorithm", "reason"} \
            and isinstance(value["reason"], str) \
            and re.fullmatch(r"[a-z_]{1,40}[A-Za-z]{0,40}", value["reason"]) is not None
    if value.get("status") != "identified" or set(value) != {
            "schema_version", "status", "algorithm", "fingerprint", "short", "components"}:
        return False
    digest = value["fingerprint"]
    if not isinstance(digest, str) or not HEX64.fullmatch(digest) or value["short"] != digest[:SHORT_CHARS]:
        return False
    components = value["components"]
    if not isinstance(components, dict) or set(components) != set(COMPONENTS):
        return False
    for component in components.values():
        if not isinstance(component, dict) or set(component) != {"files", "sha256"}:
            return False
        if type(component["files"]) is not int or not 0 < component["files"] <= MAX_FILES:
            return False
        if not isinstance(component["sha256"], str) or not HEX64.fullmatch(component["sha256"]):
            return False
    expected = _sha(_canonical({"algorithm": ALGORITHM,
                                "components": {name: components[name]["sha256"] for name in COMPONENTS}}))
    return expected == digest


def main(argv=None, *, stdout=None) -> int:
    stdout = sys.stdout if stdout is None else stdout
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--source", type=Path, help="checkout root (default: the executed sources)")
    origin.add_argument("--git-rev", help="commit to read from the repository objects")
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository for --git-rev")
    parser.add_argument("--files", action="store_true",
                        help="also print the relative {path: sha256} maps to locate a difference")
    parser.add_argument("--expect", help="exit 1 unless the fingerprint equals this value")
    args = parser.parse_args(argv)
    try:
        if args.git_rev:
            maps = git_hashes(args.repo, args.git_rev)
        elif args.source:
            maps = tree_hashes(args.source)
        else:
            maps = executed_hashes()
        result = summarize(maps)
    except (FingerprintError, OSError, ValueError) as error:
        print(json.dumps(unavailable("fingerprint_unavailable_" + type(error).__name__), sort_keys=True),
              file=stdout)
        return 2
    if args.files:
        result = {**result, "files": {name: dict(sorted(maps[name].items())) for name in COMPONENTS}}
    if args.expect is not None:
        result = {**result, "expected": args.expect, "matches": args.expect == result["fingerprint"]}
    print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    if args.expect is not None and not result["matches"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
