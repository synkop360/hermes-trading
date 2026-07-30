from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository


def _export_payload(repository: StateRepository) -> dict[str, object]:
    return {
        "positions": repository.export_open_positions(),
        "open_orders": repository.export_open_orders(),
        "system_status": repository.export_system_status(),
        "tables": repository.export_canonical_tables(),
    }


def _write_export(path: Path, payload: dict[str, object]) -> None:
    ArtifactProjector(path.parent).write_snapshot(path.name, payload)


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect Hermes canonical state")
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("integrity")
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path)
    commands.add_parser("compare")
    commands.add_parser("rebuild")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir: Path = args.state_dir
    repository = StateRepository.open_read_only(state_dir / "trading.db")
    try:
        if args.command == "integrity":
            print(
                "integrity="
                f"{repository.integrity_check()} schema_version={repository.schema_version()}"
            )
            return 0

        if args.command == "export":
            payload = _export_payload(repository)
            if args.output is None:
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            if _is_inside(args.output, state_dir):
                raise ValueError("export output must not be inside the canonical state directory")
            _write_export(args.output, payload)
            return 0

        projector = ArtifactProjector(state_dir)
        if args.command == "compare":
            expected = projector.snapshot_payloads(repository)
            mismatches = [
                name
                for name, payload in expected.items()
                if not _matches_snapshot(state_dir / name, payload)
            ]
            if mismatches:
                print(f"compare=mismatch snapshots={','.join(mismatches)}")
                return 1
            print("compare=ok")
            return 0

        projector.rebuild_all(repository)
        print("rebuild=ok")
        return 0
    finally:
        repository.close()


def _matches_snapshot(path: Path, expected: object) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")) == expected
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"inspect_state: {error}", file=sys.stderr)
        raise SystemExit(2)
