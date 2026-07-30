from __future__ import annotations

import sqlite3
from collections.abc import Sequence


class UnsupportedSchemaError(RuntimeError):
    """Raised when a database records migrations this program cannot read."""


Migration = tuple[int, Sequence[str]]
ColumnShape = tuple[str, str, int, int]


V1_COLUMNS: dict[str, tuple[ColumnShape, ...]] = {
    "schema_migrations": (
        ("version", "INTEGER", 0, 1),
        ("applied_at", "TEXT", 1, 0),
    ),
    "candles_processed": (
        ("candle_id", "TEXT", 0, 1),
        ("asset", "TEXT", 1, 0),
        ("timeframe", "TEXT", 1, 0),
        ("close_timestamp", "TEXT", 1, 0),
        ("processed_at", "TEXT", 1, 0),
    ),
    "decisions": (
        ("decision_id", "TEXT", 0, 1),
        ("asset", "TEXT", 1, 0),
        ("action", "TEXT", 1, 0),
        ("reason", "TEXT", 1, 0),
        ("candle_id", "TEXT", 1, 0),
        ("strategy_version", "TEXT", 1, 0),
        ("reference_price", "TEXT", 1, 0),
        ("decided_at", "TEXT", 1, 0),
    ),
    "order_intents": (
        ("client_order_id", "TEXT", 0, 1),
        ("decision_id", "TEXT", 1, 0),
        ("position_id", "TEXT", 0, 0),
        ("asset", "TEXT", 1, 0),
        ("side", "TEXT", 1, 0),
        ("order_type", "TEXT", 1, 0),
        ("quantity", "TEXT", 1, 0),
        ("reference_price", "TEXT", 1, 0),
        ("stop_price", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0),
    ),
    "orders": (
        ("order_id", "TEXT", 0, 1),
        ("client_order_id", "TEXT", 1, 0),
        ("exchange_order_id", "TEXT", 0, 0),
        ("asset", "TEXT", 1, 0),
        ("side", "TEXT", 1, 0),
        ("order_type", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("requested_quantity", "TEXT", 1, 0),
        ("filled_quantity", "TEXT", 1, 0),
        ("average_fill_price", "TEXT", 0, 0),
        ("fees", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "order_fills": (
        ("fill_id", "TEXT", 0, 1),
        ("order_id", "TEXT", 1, 0),
        ("asset", "TEXT", 1, 0),
        ("side", "TEXT", 1, 0),
        ("quantity", "TEXT", 1, 0),
        ("price", "TEXT", 1, 0),
        ("fee", "TEXT", 1, 0),
        ("filled_at", "TEXT", 1, 0),
    ),
    "positions": (
        ("position_id", "TEXT", 0, 1),
        ("asset", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("quantity", "TEXT", 1, 0),
        ("entry_price", "TEXT", 1, 0),
        ("stop_price", "TEXT", 1, 0),
        ("strategy_version", "TEXT", 1, 0),
        ("opened_at", "TEXT", 1, 0),
        ("closed_at", "TEXT", 0, 0),
        ("realized_pnl", "TEXT", 1, 0),
        ("fees", "TEXT", 1, 0),
    ),
    "risk_state": (
        ("risk_state_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("revision", "INTEGER", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "strategy_versions": (
        ("strategy_version_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("source_event_id", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    ),
    "provider_attempts": (
        ("provider_attempt_id", "TEXT", 0, 1),
        ("provider", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("revision", "INTEGER", 1, 0),
        ("attempted_at", "TEXT", 1, 0),
    ),
    "cost_events": (
        ("cost_event_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("amount", "TEXT", 1, 0),
        ("currency", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("occurred_at", "TEXT", 1, 0),
    ),
    "qualification_runs": (
        ("qualification_run_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("revision", "INTEGER", 1, 0),
        ("started_at", "TEXT", 1, 0),
        ("completed_at", "TEXT", 0, 0),
    ),
    "qualification_checks": (
        ("qualification_check_id", "TEXT", 0, 1),
        ("qualification_run_id", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("revision", "INTEGER", 1, 0),
        ("checked_at", "TEXT", 1, 0),
    ),
    "approval_requests": (
        ("approval_request_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("revision", "INTEGER", 1, 0),
        ("requested_at", "TEXT", 1, 0),
        ("resolved_at", "TEXT", 0, 0),
    ),
    "control_commands": (
        ("control_command_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 0, 0),
        ("revision", "INTEGER", 1, 0),
        ("requested_at", "TEXT", 1, 0),
        ("completed_at", "TEXT", 0, 0),
    ),
    "incidents": (
        ("incident_id", "TEXT", 0, 1),
        ("status", "TEXT", 1, 0),
        ("reason", "TEXT", 1, 0),
        ("severity", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("opened_at", "TEXT", 1, 0),
        ("resolved_at", "TEXT", 0, 0),
    ),
}

V1_FOREIGN_KEYS = {
    "order_intents": {
        ("decisions", "decision_id", "decision_id", "NO ACTION", "NO ACTION")
    },
    "orders": {
        ("order_intents", "client_order_id", "client_order_id", "NO ACTION", "NO ACTION")
    },
    "order_fills": {
        ("orders", "order_id", "order_id", "NO ACTION", "NO ACTION")
    },
    "qualification_checks": {
        (
            "qualification_runs",
            "qualification_run_id",
            "qualification_run_id",
            "NO ACTION",
            "NO ACTION",
        )
    },
}

V1_INDEXES = {
    "unique_processed_candle": (
        "candles_processed",
        ("asset", "timeframe", "close_timestamp"),
        1,
        0,
    ),
    "unique_client_order_id": (
        "order_intents",
        ("client_order_id",),
        1,
        0,
    ),
    "one_active_position_per_asset": (
        "positions",
        ("asset",),
        1,
        1,
    ),
}

V1_INDEX_SQL = {
    "unique_processed_candle": (
        "create unique index unique_processed_candle "
        "on candles_processed(asset, timeframe, close_timestamp)"
    ),
    "unique_client_order_id": (
        "create unique index unique_client_order_id "
        "on order_intents(client_order_id)"
    ),
    "one_active_position_per_asset": (
        "create unique index one_active_position_per_asset "
        "on positions(asset) "
        "where status in ('opening', 'open', 'closing')"
    ),
}


MIGRATIONS: tuple[Migration, ...] = (
    (
        1,
        (
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS candles_processed (
                candle_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                close_timestamp TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                UNIQUE(asset, timeframe, close_timestamp)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                candle_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                reference_price TEXT NOT NULL,
                decided_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_intents (
                client_order_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                position_id TEXT,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity TEXT NOT NULL,
                reference_price TEXT NOT NULL,
                stop_price TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (decision_id) REFERENCES decisions(decision_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL,
                exchange_order_id TEXT,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_quantity TEXT NOT NULL,
                filled_quantity TEXT NOT NULL,
                average_fill_price TEXT,
                fees TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_order_id) REFERENCES order_intents(client_order_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NOT NULL,
                filled_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(order_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                status TEXT NOT NULL,
                quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                stop_price TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                realized_pnl TEXT NOT NULL,
                fees TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS risk_state (
                risk_state_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS strategy_versions (
                strategy_version_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                source_event_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS provider_attempts (
                provider_attempt_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                attempted_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cost_events (
                cost_event_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                revision INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS qualification_runs (
                qualification_run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS qualification_checks (
                qualification_check_id TEXT PRIMARY KEY,
                qualification_run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (qualification_run_id)
                    REFERENCES qualification_runs(qualification_run_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_request_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                requested_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS control_commands (
                control_command_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                requested_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                severity TEXT NOT NULL,
                revision INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_position_per_asset
            ON positions(asset)
            WHERE status IN ('opening', 'open', 'closing')
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_client_order_id
            ON order_intents(client_order_id)
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_processed_candle
            ON candles_processed(asset, timeframe, close_timestamp)
            """,
        ),
    ),
)


def _table_columns(
    connection: sqlite3.Connection, table_name: str
) -> tuple[ColumnShape, ...]:
    return tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    )


def _foreign_keys(
    connection: sqlite3.Connection, table_name: str
) -> set[tuple[str, str, str, str, str]]:
    return {
        (row[2], row[3], row[4], row[5], row[6])
        for row in connection.execute(f"PRAGMA foreign_key_list({table_name})")
    }


def _validate_v1_schema(connection: sqlite3.Connection) -> None:
    actual_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected_tables = set(V1_COLUMNS)
    if actual_tables != expected_tables:
        raise UnsupportedSchemaError(
            "schema v1 table mismatch: "
            f"missing={sorted(expected_tables - actual_tables)}, "
            f"unexpected={sorted(actual_tables - expected_tables)}"
        )

    for table_name, expected_columns in V1_COLUMNS.items():
        actual_columns = _table_columns(connection, table_name)
        if actual_columns != expected_columns:
            raise UnsupportedSchemaError(
                f"schema v1 column mismatch for {table_name}"
            )

    for table_name in V1_COLUMNS:
        expected_foreign_keys = V1_FOREIGN_KEYS.get(table_name, set())
        actual_foreign_keys = _foreign_keys(connection, table_name)
        if actual_foreign_keys != expected_foreign_keys:
            raise UnsupportedSchemaError(
                f"schema v1 foreign key mismatch for {table_name}"
            )

    for index_name, (
        table_name,
        expected_columns,
        expected_unique,
        expected_partial,
    ) in V1_INDEXES.items():
        index_row = connection.execute(
            f"PRAGMA index_list({table_name})"
        ).fetchall()
        matching_rows = [row for row in index_row if row[1] == index_name]
        if len(matching_rows) != 1:
            raise UnsupportedSchemaError(f"schema v1 index missing: {index_name}")
        row = matching_rows[0]
        actual_columns = tuple(
            item[2]
            for item in connection.execute(f"PRAGMA index_info({index_name})")
        )
        if (
            actual_columns != expected_columns
            or row[2] != expected_unique
            or row[4] != expected_partial
        ):
            raise UnsupportedSchemaError(
                f"schema v1 index mismatch for {index_name}"
            )

        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        normalized_sql = " ".join(index_sql[0].lower().split())
        if normalized_sql != V1_INDEX_SQL[index_name]:
            raise UnsupportedSchemaError(
                f"schema v1 index definition mismatch for {index_name}"
            )

    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise UnsupportedSchemaError("schema v1 contains orphan records")


def migrate(connection: sqlite3.Connection, applied_at: str) -> None:
    """Apply every known migration once, rejecting databases from newer code."""
    known_versions = {version for version, _ in MIGRATIONS}

    with connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        if _table_columns(connection, "schema_migrations") != V1_COLUMNS[
            "schema_migrations"
        ]:
            raise UnsupportedSchemaError(
                "schema_migrations table is incompatible"
            )
        applied_versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if not applied_versions.issubset(known_versions):
            raise UnsupportedSchemaError(
                f"unsupported schema migration versions: {sorted(applied_versions - known_versions)}"
            )

        for version, statements in MIGRATIONS:
            if version in applied_versions:
                if version == 1:
                    _validate_v1_schema(connection)
                continue
            try:
                for statement in statements:
                    connection.execute(statement)
            except sqlite3.DatabaseError as error:
                raise UnsupportedSchemaError(
                    f"schema v{version} could not be applied"
                ) from error
            if version == 1:
                _validate_v1_schema(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, applied_at),
            )
