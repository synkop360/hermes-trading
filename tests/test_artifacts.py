import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hermes_trading.domain import Position, PositionStatus
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.run import ensure_state_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def seeded_repository(path: Path) -> StateRepository:
    repository = StateRepository.open(path)
    repository.migrate()
    repository.save_position(
        Position(
            position_id="position-btc-1",
            asset="BTC/USDT",
            status=PositionStatus.OPEN,
            quantity=Decimal("0.001"),
            entry_price=Decimal("65000"),
            stop_price=Decimal("64000"),
            strategy_version="01",
            opened_at="2026-07-30T20:00:00+00:00",
        )
    )
    return repository


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary_directory.name)
        self.repository: StateRepository | None = None

    def tearDown(self) -> None:
        if self.repository is not None:
            self.repository.close()
        self.temporary_directory.cleanup()

    def test_snapshot_is_valid_json_after_replacement(self) -> None:
        projector = ArtifactProjector(self.state_dir)

        projector.write_snapshot("positions.json", {"positions": []})

        self.assertEqual(
            json.loads((self.state_dir / "positions.json").read_text()),
            {"positions": []},
        )
        self.assertFalse((self.state_dir / "positions.json.tmp").exists())

    def test_rebuild_exports_open_positions_without_sqlite_reader(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        self.repository = repository

        ArtifactProjector(self.state_dir).rebuild_all(repository)

        payload = json.loads((self.state_dir / "positions.json").read_text())
        self.assertEqual(payload["positions"][0]["asset"], "BTC/USDT")

    def test_append_event_writes_one_compact_json_object_per_line(self) -> None:
        projector = ArtifactProjector(self.state_dir)

        projector.append_event("decisions.jsonl", {"asset": "BTC/USDT", "quantity": Decimal("0.001")})

        self.assertEqual(
            (self.state_dir / "decisions.jsonl").read_text(),
            '{"asset":"BTC/USDT","quantity":"0.001"}\n',
        )

    def test_rebuild_writes_all_snapshots_without_replacing_legacy_journals(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        self.repository = repository
        journal = self.state_dir / "orders.jsonl"
        journal.write_text('{"legacy":true}\n')

        ArtifactProjector(self.state_dir).rebuild_all(repository)

        self.assertEqual(journal.read_text(), '{"legacy":true}\n')
        self.assertTrue((self.state_dir / "positions.json").exists())
        self.assertTrue((self.state_dir / "open_orders.json").exists())
        self.assertTrue((self.state_dir / "system_status.json").exists())

    def test_bootstrap_migrates_database_and_builds_missing_snapshots(self) -> None:
        journal = self.state_dir / "trades.jsonl"
        journal.write_text('{"legacy":true}\n')

        ensure_state_files(self.state_dir)

        self.assertTrue((self.state_dir / "trading.db").exists())
        self.assertTrue((self.state_dir / "positions.json").exists())
        self.assertTrue((self.state_dir / "open_orders.json").exists())
        self.assertTrue((self.state_dir / "system_status.json").exists())
        self.assertEqual(journal.read_text(), '{"legacy":true}\n')

    def test_integrity_and_export_are_read_only(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        repository.close()
        journal = self.state_dir / "trades.jsonl"
        journal.write_text('{"legacy":true}\n')

        integrity = self.run_cli("integrity")
        export = self.run_cli("export")

        self.assertEqual(integrity.returncode, 0, integrity.stderr)
        self.assertEqual(integrity.stdout.strip(), "integrity=ok schema_version=1")
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertEqual(json.loads(export.stdout)["positions"][0]["asset"], "BTC/USDT")
        self.assertEqual(journal.read_text(), '{"legacy":true}\n')
        self.assertFalse((self.state_dir / "positions.json").exists())

    def test_compare_reports_snapshot_mismatches_without_rebuilding(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        ArtifactProjector(self.state_dir).rebuild_all(repository)
        repository.close()
        (self.state_dir / "positions.json").write_text('{"positions":[]}\n')

        result = self.run_cli("compare")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "compare=mismatch snapshots=positions.json")

    def test_export_writes_only_to_an_explicit_external_destination(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        repository.close()
        with tempfile.TemporaryDirectory() as output_directory:
            output_path = Path(output_directory) / "canonical.json"

            result = self.run_cli("export", "--output", str(output_path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output_path.read_text())["system_status"]["schema_version"],
                1,
            )
        self.assertFalse((self.state_dir / "positions.json").exists())

    def test_rebuild_is_explicit_and_preserves_legacy_journals(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        repository.close()
        journal = self.state_dir / "orders.jsonl"
        journal.write_text('{"legacy":true}\n')

        result = self.run_cli("rebuild")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(journal.read_text(), '{"legacy":true}\n')
        self.assertTrue((self.state_dir / "positions.json").exists())

    def run_cli(self, command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "scripts/inspect_state.py",
                "--state-dir",
                str(self.state_dir),
                command,
                *arguments,
            ],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
