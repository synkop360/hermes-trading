from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hermes_trading.domain import (
    Decision,
    Fill,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Position,
    PositionStatus,
)
from hermes_trading.persistence import (
    ActivePositionExists,
    DuplicateIntentError,
    RelatedRecordNotFoundError,
    StateRepository,
    UnsupportedSchemaError,
)
from hermes_trading.persistence.migrations import _normalize_sql


def sample_decision() -> Decision:
    return Decision(
        decision_id="decision-btc-1",
        asset="BTC/USDT",
        action="enter",
        reason="rsi_entry",
        candle_id="btc-1h-20260730T200000Z",
        strategy_version="69",
        reference_price=Decimal("65000.0"),
        decided_at="2026-07-30T20:00:00+00:00",
    )


def sample_intent(client_order_id: str) -> OrderIntent:
    return OrderIntent(
        client_order_id=client_order_id,
        decision_id="decision-btc-1",
        asset="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.00010"),
        reference_price=Decimal("65000.0"),
        created_at="2026-07-30T20:00:01+00:00",
        position_id="pos-btc-1",
        stop_price=Decimal("64675.0"),
    )


def sample_order() -> OrderRecord:
    return OrderRecord(
        order_id="order-btc-1",
        client_order_id="ht-btc-20260730",
        asset="BTC/USDT",
        side="buy",
        order_type="market",
        status=OrderStatus.FILLED,
        requested_quantity=Decimal("0.00010"),
        filled_quantity=Decimal("0.00010"),
        average_fill_price=Decimal("65001.0"),
        fees=Decimal("0.01"),
        created_at="2026-07-30T20:00:01+00:00",
        updated_at="2026-07-30T20:00:02+00:00",
        exchange_order_id="exchange-btc-1",
    )


def sample_fill() -> Fill:
    return Fill(
        fill_id="fill-btc-1",
        order_id="order-btc-1",
        asset="BTC/USDT",
        side="buy",
        quantity=Decimal("0.00010"),
        price=Decimal("65001.0"),
        fee=Decimal("0.01"),
        filled_at="2026-07-30T20:00:02+00:00",
    )


def sample_position(position_id: str, asset: str, status: PositionStatus = PositionStatus.OPEN) -> Position:
    return Position(
        position_id=position_id,
        asset=asset,
        status=status,
        quantity=Decimal("0.00010"),
        entry_price=Decimal("65001.0"),
        stop_price=Decimal("64675.0"),
        strategy_version="69",
        opened_at="2026-07-30T20:00:02+00:00",
        fees=Decimal("0.01"),
    )


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "trading.db"
        self.repo = StateRepository.open(self.db_path)

    def tearDown(self) -> None:
        self.repo.close()
        self.temporary_directory.cleanup()

    def test_repository_enables_wal_and_migrates_once(self) -> None:
        self.repo.migrate()

        self.assertEqual(self.repo.journal_mode(), "wal")
        self.assertEqual(
            self.repo._connection.execute("PRAGMA foreign_keys").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.repo._connection.execute("PRAGMA busy_timeout").fetchone()[0],
            5000,
        )
        self.assertEqual(self.repo.schema_version(), 4)

        self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 4)
        self.assertEqual(self.repo.integrity_check(), "ok")

    def test_duplicate_candle_and_risk_evidence_counts_are_visible(self) -> None:
        self.repo.migrate()
        self.repo.open_incident("qualification:duplicate-candle", "duplicate_candle_protected", severity="info")
        self.repo.open_incident("qualification:risk-limit", "risk_limit_exercised", severity="info")

        self.assertEqual(self.repo.duplicate_candle_evidence_count(), 1)
        self.assertEqual(self.repo.risk_limit_evidence_count(), 1)

    def test_sql_normalization_preserves_escaped_literal_quotes(self) -> None:
        self.assertEqual(
            _normalize_sql(
                "CREATE  INDEX example\n"
                "ON records(status) WHERE status = 'O''PEN'"
            ),
            (
                "create index example on records(status) "
                "where status = 'O''PEN'"
            ),
        )

    def test_migration_creates_all_canonical_tables(self) -> None:
        self.repo.migrate()

        self.assertEqual(
            self.repo.table_names(),
            {
                "approval_requests",
                "candles_processed",
                "control_nonces",
                "control_commands",
                "cost_events",
                "decisions",
                "incidents",
                "order_fills",
                "order_intents",
                "orders",
                "positions",
                "projection_payloads",
                "provider_attempts",
                "qualification_checks",
                "qualification_runs",
                "risk_state",
                "risk_decisions",
                "schema_migrations",
                "strategy_versions",
            },
        )

    def test_migration_upgrades_a_valid_v1_database_with_immutable_projection_payloads(self) -> None:
        self.repo.migrate()
        self.repo.record_decision(sample_decision())
        with self.repo._connection:
            self.repo._connection.execute("DROP TABLE risk_decisions")
            self.repo._connection.execute("DROP TABLE projection_payloads")
            self.repo._connection.execute("DROP TABLE control_nonces")
            self.repo._connection.execute("DELETE FROM schema_migrations WHERE version IN (2, 3, 4)")

        self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 4)
        self.assertIn("projection_payloads", self.repo.table_names())
        payload = self.repo._connection.execute(
            "SELECT payload FROM projection_payloads WHERE decision_id = ?",
            ("decision-btc-1",),
        ).fetchone()
        self.assertIn('"decision_id":"decision-btc-1"', payload["payload"])
        self.assertIn("risk_decisions", self.repo.table_names())

    def test_migration_rejects_v1_marker_with_a_future_table_present(self) -> None:
        self.repo.migrate()
        with self.repo._connection:
            self.repo._connection.execute("DELETE FROM schema_migrations WHERE version IN (2, 3)")

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

    def test_migration_rejects_an_unknown_schema_version(self) -> None:
        with self.repo._connection:
            self.repo._connection.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            self.repo._connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (2, '2026-07-30T20:00:00+00:00')"
            )

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

    def test_migration_rolls_back_when_an_existing_table_is_incompatible(self) -> None:
        with self.repo._connection:
            self.repo._connection.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            self.repo._connection.execute("CREATE TABLE positions (position_id TEXT PRIMARY KEY)")

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

        self.assertNotIn("decisions", self.repo.table_names())
        self.assertEqual(self.repo.schema_version(), 0)

    def test_migration_does_not_stamp_an_incompatible_preexisting_table(self) -> None:
        with self.repo._connection:
            self.repo._connection.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            self.repo._connection.execute(
                "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY)"
            )

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 0)
        self.assertNotIn("orders", self.repo.table_names())

    def test_recorded_v1_is_rejected_when_a_table_is_missing(self) -> None:
        self.repo.migrate()
        with self.repo._connection:
            self.repo._connection.execute("DROP TABLE decisions")

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 4)

    def test_recorded_v1_is_rejected_when_a_table_is_incompatible(self) -> None:
        self.repo.migrate()
        with self.repo._connection:
            self.repo._connection.execute("DROP TABLE decisions")
            self.repo._connection.execute(
                "CREATE TABLE decisions (decision_id TEXT PRIMARY KEY)"
            )

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 4)

    def test_recorded_v1_rejects_a_disabled_active_position_index(self) -> None:
        self.repo.migrate()
        with self.repo._connection:
            self.repo._connection.execute(
                "DROP INDEX one_active_position_per_asset"
            )
            self.repo._connection.execute(
                """
                CREATE UNIQUE INDEX one_active_position_per_asset
                ON positions(asset)
                WHERE status IN ('opening', 'open', 'closing') AND 0
                """
            )

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 4)

    def test_recorded_v1_rejects_uppercase_active_status_literals(self) -> None:
        self.repo.migrate()
        with self.repo._connection:
            self.repo._connection.execute(
                "DROP INDEX one_active_position_per_asset"
            )
            self.repo._connection.execute(
                """
                CREATE UNIQUE INDEX one_active_position_per_asset
                ON positions(asset)
                WHERE status IN ('OPENING', 'OPEN', 'CLOSING')
                """
            )

        self.repo.save_position(sample_position("one", "BTC/USDT"))
        self.repo.save_position(sample_position("two", "BTC/USDT"))

        with self.assertRaises(UnsupportedSchemaError):
            self.repo.migrate()

        self.assertEqual(self.repo.schema_version(), 4)

    def test_order_intent_client_id_is_unique(self) -> None:
        self.repo.migrate()
        intent = sample_intent(client_order_id="ht-btc-20260730")
        self.repo.record_decision(sample_decision())

        self.repo.create_order_intent(intent)

        with self.assertRaises(DuplicateIntentError):
            self.repo.create_order_intent(intent)

    def test_only_one_active_position_per_asset(self) -> None:
        self.repo.migrate()
        self.repo.save_position(sample_position("one", "BTC/USDT"))

        with self.assertRaises(ActivePositionExists):
            self.repo.save_position(sample_position("two", "BTC/USDT"))

    def test_order_intent_rejects_a_missing_decision(self) -> None:
        self.repo.migrate()

        with self.assertRaises(RelatedRecordNotFoundError):
            self.repo.create_order_intent(sample_intent("ht-orphan-intent"))

    def test_order_rejects_a_missing_intent(self) -> None:
        self.repo.migrate()

        with self.assertRaises(RelatedRecordNotFoundError):
            self.repo.upsert_order(sample_order())

    def test_fill_rejects_a_missing_order(self) -> None:
        self.repo.migrate()

        with self.assertRaises(RelatedRecordNotFoundError):
            self.repo.record_fill(sample_fill())

    def test_qualification_check_rejects_a_missing_run(self) -> None:
        self.repo.migrate()

        with self.assertRaises(sqlite3.IntegrityError):
            with self.repo._connection:
                self.repo._connection.execute(
                    """
                    INSERT INTO qualification_checks (
                        qualification_check_id, qualification_run_id, status,
                        reason, revision, checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "check-orphan-1",
                        "run-missing",
                        "failed",
                        "missing_run",
                        1,
                        "2026-07-30T20:00:00+00:00",
                    ),
                )

    def test_control_nonce_replay_survives_repository_reopen_and_expires(self) -> None:
        self.repo.migrate()
        accepted_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        expires_at = accepted_at + timedelta(minutes=5)

        self.assertTrue(self.repo.record_control_nonce("nonce-1", accepted_at=accepted_at, expires_at=expires_at))
        self.repo.close()
        self.repo = StateRepository.open(self.db_path)
        self.repo.migrate()

        self.assertFalse(self.repo.record_control_nonce("nonce-1", accepted_at=accepted_at, expires_at=expires_at))
        self.assertTrue(
            self.repo.record_control_nonce(
                "nonce-1",
                accepted_at=expires_at + timedelta(seconds=1),
                expires_at=expires_at + timedelta(minutes=5, seconds=1),
            )
        )

    def test_repository_round_trips_domain_records_and_candle_identity(self) -> None:
        self.repo.migrate()
        self.repo.record_decision(sample_decision())
        self.repo.create_order_intent(sample_intent("ht-btc-20260730"))
        self.repo.upsert_order(sample_order())
        self.repo.record_fill(sample_fill())
        position = sample_position("pos-btc-1", "BTC/USDT")
        self.repo.save_position(position)

        self.assertEqual(self.repo.open_position("BTC/USDT"), position)
        self.assertFalse(self.repo.is_candle_processed("BTC/USDT", "1h", "2026-07-30T20:00:00+00:00"))
        self.repo.mark_candle_processed("BTC/USDT", "1h", "2026-07-30T20:00:00+00:00")
        self.assertTrue(self.repo.is_candle_processed("BTC/USDT", "1h", "2026-07-30T20:00:00+00:00"))

    def test_order_upsert_replaces_mutable_execution_fields(self) -> None:
        self.repo.migrate()
        self.repo.record_decision(sample_decision())
        self.repo.create_order_intent(sample_intent("ht-btc-20260730"))
        self.repo.upsert_order(sample_order())
        revised = OrderRecord(
            **{**sample_order().__dict__, "status": OrderStatus.PARTIALLY_FILLED, "filled_quantity": Decimal("0.00005")}
        )

        self.repo.upsert_order(revised)

        row = self.repo._connection.execute(
            "SELECT status, filled_quantity FROM orders WHERE order_id = ?", ("order-btc-1",)
        ).fetchone()
        self.assertEqual(tuple(row), ("partially_filled", "0.00005"))


if __name__ == "__main__":
    unittest.main()
