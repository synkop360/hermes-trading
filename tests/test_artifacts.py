import json
import multiprocessing
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from hermes_trading.domain import Position, PositionStatus
from hermes_trading.persistence import StateRepository
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.run import ensure_state_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TABLES = {
    "approval_requests",
    "candles_processed",
    "control_commands",
    "cost_events",
    "decisions",
    "incidents",
    "order_fills",
    "order_intents",
    "orders",
    "positions",
    "provider_attempts",
    "qualification_checks",
    "qualification_runs",
    "risk_state",
    "schema_migrations",
    "strategy_versions",
}


def append_events_worker(state_dir: str, worker_id: int, count: int) -> None:
    projector = ArtifactProjector(state_dir)
    for event_index in range(count):
        projector.append_event(
            "decisions.jsonl",
            {
                "event_id": f"{worker_id}-{event_index}",
                "payload": "x" * 20_000,
            },
        )


def write_snapshots_worker(state_dir: str, worker_id: int, count: int) -> None:
    projector = ArtifactProjector(state_dir)
    for snapshot_index in range(count):
        projector.write_snapshot(
            "positions.json",
            {
                "positions": [
                    {
                        "worker_id": worker_id,
                        "snapshot_index": snapshot_index,
                        "payload": "x" * 20_000,
                    }
                ]
            },
        )


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

    def test_concurrent_process_snapshots_are_valid_and_leave_no_temps(self) -> None:
        worker_count = 8
        snapshots_per_worker = 10
        self.run_processes(
            write_snapshots_worker,
            [
                (
                    str(self.state_dir),
                    worker_id,
                    snapshots_per_worker,
                )
                for worker_id in range(worker_count)
            ],
        )

        payload = json.loads((self.state_dir / "positions.json").read_text())
        self.assertEqual(len(payload["positions"]), 1)
        self.assertEqual(list(self.state_dir.glob("*.tmp")), [])

    def test_snapshot_syncs_parent_directory_after_replacement(self) -> None:
        projector = ArtifactProjector(self.state_dir)

        with patch(
            "hermes_trading.persistence.artifacts._fsync_directory"
        ) as sync_directory:
            projector.write_snapshot("positions.json", {"positions": []})

        sync_directory.assert_called_once_with(self.state_dir)

    def test_snapshot_does_not_mask_unexpected_directory_sync_failure(self) -> None:
        projector = ArtifactProjector(self.state_dir)

        with patch(
            "hermes_trading.persistence.artifacts._fsync_directory",
            side_effect=OSError("directory sync failed"),
        ):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                projector.write_snapshot("positions.json", {"positions": []})

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

    def test_events_without_nonempty_canonical_key_are_never_deduplicated(self) -> None:
        projector = ArtifactProjector(self.state_dir)
        cases = (
            ("decisions.jsonl", [{"event_id": "one"}, {"event_id": "two"}, {"decision_id": ""}]),
            ("orders.jsonl", [{"event_id": "one"}, {"event_id": "two"}, {"order_id": None}]),
            ("fills.jsonl", [{"event_id": "one"}, {"event_id": "two"}, {"fill_id": ""}]),
            ("positions.jsonl", [{"event_id": "one"}, {"position_id": "p", "status": ""}]),
            ("trades.jsonl", [{"event_id": "one"}, {"position_id": "", "status": "closed"}]),
        )

        for name, events in cases:
            for event in events:
                projector.append_event(name, event)
            self.assertEqual(len((self.state_dir / name).read_text().splitlines()), len(events))

    def test_concurrent_process_appends_preserve_every_complete_event(self) -> None:
        worker_count = 8
        events_per_worker = 10
        self.run_processes(
            append_events_worker,
            [
                (
                    str(self.state_dir),
                    worker_id,
                    events_per_worker,
                )
                for worker_id in range(worker_count)
            ],
        )

        lines = (self.state_dir / "decisions.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in lines]
        self.assertEqual(len(events), worker_count * events_per_worker)
        self.assertEqual(
            {event["event_id"] for event in events},
            {
                f"{worker_id}-{event_index}"
                for worker_id in range(worker_count)
                for event_index in range(events_per_worker)
            },
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
        self.assertEqual(integrity.stdout.strip(), "integrity=ok schema_version=2")
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

    def test_compare_names_directory_and_invalid_encoding_snapshots_as_mismatches(
        self,
    ) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        ArtifactProjector(self.state_dir).rebuild_all(repository)
        repository.close()
        positions_path = self.state_dir / "positions.json"
        positions_path.unlink()
        positions_path.mkdir()
        (self.state_dir / "open_orders.json").write_bytes(b"\xff")

        result = self.run_cli("compare")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "compare=mismatch snapshots=positions.json,open_orders.json",
        )

    def test_export_writes_only_to_an_explicit_external_destination(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        repository.close()
        with tempfile.TemporaryDirectory() as output_directory:
            output_path = Path(output_directory) / "canonical.json"

            result = self.run_cli("export", "--output", str(output_path))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(output_path.read_text())["system_status"]["schema_version"],
                2,
            )
        self.assertFalse((self.state_dir / "positions.json").exists())

    def test_export_includes_all_16_canonical_tables_with_decimal_text(self) -> None:
        repository = seeded_repository(self.state_dir / "trading.db")
        repository.close()

        result = self.run_cli("export")

        self.assertEqual(result.returncode, 0, result.stderr)
        tables = json.loads(result.stdout)["tables"]
        self.assertEqual(set(tables), CANONICAL_TABLES)
        self.assertEqual(tables["positions"][0]["quantity"], "0.001")

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

    def run_processes(
        self,
        target: Callable[..., None],
        argument_sets: list[tuple[object, ...]],
    ) -> None:
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(target=target, args=arguments)
            for arguments in argument_sets
        ]
        deadline = time.monotonic() + 30
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(max(0, deadline - time.monotonic()))
            timed_out = [process for process in processes if process.is_alive()]
            if timed_out:
                self.fail(
                    "worker processes exceeded 30 seconds: "
                    f"{[process.pid for process in timed_out]}"
                )
            self.assertEqual(
                [process.exitcode for process in processes],
                [0] * len(processes),
            )
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
